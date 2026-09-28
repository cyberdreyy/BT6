### Title
Aggregate instant-withdraw receipts are claimed at par without verifying the newest request's epoch was funded - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.claimInstantWithdrawRequest` pays out the entire aggregate `instantWithdrawsRequests[_user]` balance and burns the full receipt, with no per-epoch maturity or per-epoch funding check. Analogous to CVE-2018-13093 (a stale cached object reused without validating it is still valid/free), the vault reuses a single aggregate receipt slot without validating that the newest request's epoch has actually been funded via `collectInstantWithdrawFunds`. A user who stacks an old funded instant receipt with a fresh unfunded one can claim both at par immediately, draining liquidity collected for other users' pending claims.

### Finding Description
Instant-withdraw accounting has two ledgers:

- Per-epoch receipts: `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` [1](#0-0) 
- A single aggregate receipt: `instantWithdrawsRequests[_user]`, incremented in `requestInstantWithdraw` [2](#0-1) 

Funding is tracked separately: `pendingInstantWithdraws` is decreased only when the borrower/CDO actually supplies cash through `collectInstantWithdrawFunds` [3](#0-2) .

The claim path ignores both the per-epoch split and the funded remainder:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
``` [4](#0-3) 

Compare with normal withdraws, which are gated by `epochNumber <= lastWithdrawRequest[_user]` so a request must age a full epoch before claiming [5](#0-4) , and with loss-adjusted epochs which are haircut through `lossRecoveryPriceByEpoch` [6](#0-5) . Instant receipts get neither: no epoch-maturity check, and no haircut for a `stopEpochWithDuration` loss epoch, since `lossRecoveryPriceByEpoch` is only consulted in `_claimLossAdjustedWithdrawRequest` for normal/APR0 receipts.

### Impact Explanation
Broken invariant: one receipt one payout, backed by collected funds.

1. A request in epoch N is funded (borrower liquidity collected at `startEpoch`, `pendingInstantWithdraws` decremented).
2. After `stopEpoch`, epoch N+1 begins. The attacker calls `requestInstantWithdraw` again; `instantWithdrawsRequests[_user]` and `pendingInstantWithdraws` both grow, but no funds for the new request have been collected yet [7](#0-6) .
3. `claimInstantWithdrawRequest` burns and pays the *aggregate*, so the unfunded N+1 receipt is paid at par out of underlying sitting in the strategy — which is the reserve backing other users' funded-but-unclaimed instant receipts.
4. Result: the attacker exits an unfunded request instantly at full value; other claimants' funds are temporarily frozen (or permanently lost if the borrower never tops up the shortfall). Additionally, instant receipts requested in an epoch that later realizes a `stopEpochWithDuration` loss are never haircut, so instant requesters claim at par while normal withdrawers in the same epoch absorb the loss via `lossRecoveryPriceByEpoch` — a pro-rata dilution theft.

Loss is quantified as the unfunded portion of the attacker's newest instant request, paid at par instead of waiting for `collectInstantWithdrawFunds`.

### Likelihood Explanation
Requires: instant withdrawals enabled (`setInstantWithdrawParams`), the attacker to be a KYC-allowed tranche holder, and one previously funded instant receipt (or sufficient strategy-held underlying from other users' collected claims). No privileged misbehavior needed — `requestInstantWithdraw` and the claim are unprivileged paths (the vault only enforces `_onlyIdleCDO`). The guard that prevents stacking a stale loss-epoch receipt (`requestWithdraw` lines 263–271) exists only for normal withdraws; `requestInstantWithdraw` has no equivalent check against `lossRecoveryPriceByEpoch` or pending funding state. Likelihood is moderate: the exploit window is each epoch where instant funding has not yet been collected, which recurs every epoch.

### Recommendation
Track instant receipts strictly per epoch and gate claims on funding:

- In `claimInstantWithdrawRequest`, settle per epoch: iterate/record `instantWithdrawsRequestsByEpoch[_user][e]` and only pay epochs where the corresponding `instantWithdrawClaimsByEpoch` share was collected (i.e., funded basis minus `pendingInstantWithdraws` remainder), mirroring how `claimWithdrawRequest` splits funded vs defaulted epochs.
- Apply `lossRecoveryPriceByEpoch` (or an equivalent haircut map) to instant receipts of loss epochs, symmetric to normal receipts.
- Alternatively, revert new `requestInstantWithdraw` while a prior epoch's instant receipt is still unfunded for that user, mirroring the `NotAllowed` guard in `requestWithdraw`.

### Proof of Concept
Foundry fork sketch (mainnet fork, existing deployment; requires an `epochEndDate != 0` running pool with instant withdrawals enabled):

```solidity
function testInstantClaimPaysUnfundedReceipt() public {
    // EPOCH N running, instantDelay elapsed, instant funds collected
    vm.prank(attacker);
    cdo.requestInstantWithdraw(amountA, address(aaTranche)); // funded request
    // funds collected via collectInstantWithdrawFunds at/after epoch start

    // stopEpoch -> buffer -> startEpoch N+1
    _stopAndStartEpoch();

    // Attacker stacks a NEW instant request in epoch N+1 before borrower funds arrive
    vm.prank(attacker);
    cdo.requestInstantWithdraw(amountB, address(aaTranche));
    assertGt(strategy.pendingInstantWithdraws(), 0); // B is unfunded

    // Claim pays amountA + amountB at par from strategy-held underlying
    uint256 balBefore = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdo.claimInstantWithdrawRequest();
    assertEq(underlying.balanceOf(attacker) - balBefore, amountA + amountB);
    // amountB was paid out of reserves backing OTHER users' pending instant claims
}
```

Caveat: I verified the vault-side payout ignores per-epoch funding, but did not fully trace `IdleCDOEpochVariant.claimInstantWithdrawRequest`'s outer gating (instant-delay check); if the CDO layer independently blocks claims until each epoch's funds arrive, this reduces to a design-risk note rather than an exploitable path — the fix is still warranted because the vault, not the CDO, owns the funding ledger (`pendingInstantWithdraws`).

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L106-109)
```text
  /// @notice instant withdraw receipt basis by user and request epoch
  mapping(address => mapping(uint256 => uint256)) public instantWithdrawsRequestsByEpoch;
  /// @notice total outstanding instant-withdraw receipt basis per request epoch
  mapping(uint256 => uint256) public instantWithdrawClaimsByEpoch;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L326-328)
```text
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-375)
```text
  function requestInstantWithdraw(uint256 _amount, address _user) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    // burn strategy tokens from cdo
    _burn(msg.sender, _amount);
  
    // mint equal amount of strategy tokens to the user as receipt, useful in case of default
    _mint(_user, _amount);

    // increase the instant withdraw requests for the user
    instantWithdrawsRequests[_user] += _amount;
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L387-392)
```text
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-403)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L789-800)
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
```
