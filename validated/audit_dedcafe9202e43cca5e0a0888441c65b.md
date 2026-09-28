### Title
Recovery-price double rounding permanently locks residual underlying in the vault - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external report describes a value that is converted out and back (ETH→SD→ETH), so the user's withdrawable amount is rounded down twice and the remainder is stranded. The closest analog in idle-tranches is the default-recovery claim path in `IdleCreditVault`: a global `defaultRecoveryPrice` is computed once with floor division in `finalizeDefaultRecovery`, and every later claim converts a user's claim basis back to underlying with a second floor division in `_claimDefaultedWithdrawRequest` (and the symmetric paths for instant, loss-adjusted and post-default receipts). The sum of all payable claims is strictly less than `defaultRecoveryReserve`, and the leftover dust has no sweep path — it is permanently locked in the strategy.

### Finding Description
In `finalizeDefaultRecovery`, the recovery rate is fixed once as

```solidity
uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;   // rounds down
defaultRecoveryReserve = reserveAmount;
defaultRecoveryPrice   = recoveryPrice;
``` [1](#0-0) 

Each defaulted receipt then claims

```solidity
amount = (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL;         // rounds down again
_transferDefaultRecovery(_user, amount);
``` [2](#0-1) 

This is structurally identical to the reported `withdrawableInEth` bug: an entitlement in "basis" units is converted to a rate (basis→price, floor), then converted back to underlying per user (price→amount, floor). Because `recoveryPrice` was truncated by up to 1 wei of `RECOVERY_FULL`, the aggregate of all `claimBasis * recoveryPrice / RECOVERY_FULL` payouts is less than `reserveAmount` by up to `totalBasis / RECOVERY_FULL` wei plus up to 1 wei per claim. The same pattern exists for loss-adjusted receipts via `lossRecoveryPriceByEpoch` in `collectWithdrawFunds`/`_claimLossAdjustedWithdrawRequest` (`amount * RECOVERY_FULL / pendingBasis` then `claimBasis * price / RECOVERY_FULL`) [3](#0-2) [4](#0-3) , and for the standalone `DefaultDistributor.claim` where `rate` is fixed at activation and each claim is `trancheBal * rate / ONE_TRANCHE` [5](#0-4) .

### Impact Explanation
Every defaulted-epoch (or loss-adjusted-epoch) claimant receives strictly less than their proportional share of the recovered/reserved underlying, and after all claims the residual `defaultRecoveryReserve` dust remains inside `IdleCreditVault` with no function to redistribute or sweep it — the strategy's underlying balance is not part of `getContractValue`/NAV once receipts leave live NAV, so it is permanently frozen rather than accruing to anyone. For `DefaultDistributor` the residue is recoverable only via the owner's `transferToken`, i.e. users still lose the dust amount. Quantified bound: loss per user < 1 wei of underlying; aggregate stranded amount < `totalBasis / RECOVERY_FULL + number_of_claimants` wei (for USDC with `RECOVERY_FULL = 1e18` this is sub-cent per claim, up to ~`totalBasis` in whole-token units of dust overall).

### Likelihood Explanation
The rounding is deterministic — it occurs on every finalization where `reserveAmount * RECOVERY_FULL % totalBasis != 0`, which is essentially always for non-trivial bases. However, the absolute loss is dust-scale per claim and cannot be amplified by an unprivileged attacker beyond deliberately creating many small receipts, which does not increase the aggregate truncation bound. This mirrors the external report's own "small amount" characterization.

### Recommendation
Round the stored recovery price up (`recoveryPrice = (reserveAmount * RECOVERY_FULL + totalBasis - 1) / totalBasis`, capped at `RECOVERY_FULL` if `reserveAmount < totalBasis` semantics require) and/or round each claim down while letting the final claimant receive the remaining reserve — the same fix pattern as the report's recommendation to round the interest up instead of double-rounding the user's balance down. Equivalently, pay each claim as `claimBasis * reserveAmount / totalBasis` directly, avoiding the intermediate truncated rate. Apply the same fix to `lossRecoveryPriceByEpoch` in `collectWithdrawFunds` and to `DefaultDistributor.setIsActive`/`claim`.

### Proof of Concept
A Foundry fork test can reproduce the dust-lock deterministically:

```solidity
// after borrower default and manager calls finalizeDefaultRecovery(...)
uint256 reserve = strategy.defaultRecoveryReserve();
uint256 price   = strategy.defaultRecoveryPrice();
uint256 basis   = strategy.defaultPendingClaimBasis(); // == totalBasis - activeBasis

// user claim
uint256 balPre = underlying.balanceOf(user);
cdoEpoch.claimWithdrawRequest();            // or claimInstantWithdrawRequest
uint256 paid = underlying.balanceOf(user) - balPre;

// paid == claimBasis * price / RECOVERY_FULL < claimBasis * reserve / totalBasis
assertLt(paid, claimBasisOf(user) * reserve / basisTotal + 1);

// after ALL receipts are claimed, strategy retains stranded underlying:
// underlying.balanceOf(strategy) == reserve - sum(paid) > 0
// and no external function can move it.
```

Note on completeness: I verified the rounding structure in `finalizeDefaultRecovery`, `_claimDefaultedWithdrawRequest`, `_claimLossAdjustedWithdrawRequest`, `collectWithdrawFunds`, and `DefaultDistributor.claim`, but I did not fully enumerate `_transferDefaultRecovery`'s reserve bookkeeping or a hypothetical sweep function; if a reserve-sweep exists that I did not find, the residual would be owner-recoverable rather than permanently locked, which would reduce the impact to the per-claim dust loss for users.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-430)
```text
  function collectWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    uint256 pendingBasis = pendingWithdraws;
    if (_amount < pendingBasis) {
      // Legacy receipts do not have per-epoch ownership data, so they can only be fully funded.
      if (!defaultRecoveryInitialized) revert NotAllowed();
      uint256 lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis;
      // Avoid storing a zero price, which is indistinguishable from "no loss-adjusted epoch".
      if (lossRecoveryPrice == 0) revert NotAllowed();
      pendingWithdraws = 0;
      lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;
    } else {
      // A plain implementation upgrade may leave legacy normal receipts pending. Their next
      // successful stop can fully fund the aggregate before lazy initialization occurs.
      pendingWithdraws = pendingBasis - _amount;
    }
    if (_amount != 0) {
      underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L686-693)
```text
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
    defaultRecoveryEpoch = epochNumber;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L772-784)
```text
  function _claimDefaultedWithdrawRequest(address _user) internal returns (uint256 amount) {
    uint256 defaultEpoch = defaultRecoveryEpoch;
    (uint256 claimBasis, uint256 burnAmount) = _clearWithdrawClaimForEpoch(_user, defaultEpoch, true);
    if (claimBasis == 0) return amount;

    // pendingWithdraws stores the claim basis owed by the borrower, including APR0 interest.
    pendingWithdraws -= claimBasis;
    // Only receipt principal exists as strategy tokens. APR0 interest is included in claimBasis
    // but was never minted as a user strategy-token receipt.
    _burn(_user, burnAmount);
    amount = (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL;
    _transferDefaultRecovery(_user, amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L789-801)
```text
  function _claimLossAdjustedWithdrawRequest(address _user) internal returns (uint256 amount) {
    uint256 lossEpoch = lastWithdrawRequest[_user];
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (lossRecoveryPrice == 0) return amount;

    (uint256 claimBasis, uint256 burnAmount) = _clearWithdrawClaimForEpoch(_user, lossEpoch, false);
    if (claimBasis == 0) return amount;

    // pendingWithdraws was already cleared when the borrower funded the loss-adjusted amount.
    _burn(_user, burnAmount);
    amount = (claimBasis * lossRecoveryPrice) / RECOVERY_FULL;
    _transferFundedClaim(_user, amount);
  }
```

**File:** contracts/DefaultDistributor.sol (L35-50)
```text
  function claim(address _to) external {
    require(isActive, '!ACTIVE');
    IERC20 tranche = IERC20(trancheToken);
    uint256 trancheBal = tranche.balanceOf(msg.sender);
    tranche.safeTransferFrom(msg.sender, address(this), trancheBal);
    IERC20(token).safeTransfer(_to, trancheBal * rate / ONE_TRANCHE);
  }

  /// @notice Start claim process and set redemption rate
  /// @param _active claim active flag
  function setIsActive(bool _active) external {
    require(owner() == msg.sender, '!AUTH');
    isActive = _active;
    if (_active) {
      rate = IERC20(token).balanceOf(address(this)) * ONE_TRANCHE / IERC20(trancheToken).totalSupply();
    }
```
