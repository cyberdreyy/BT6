### Title
Stale-epoch instant-withdraw receipts escape default haircut and claim at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The bug class from CVE-2020-6107 (reading stale/uninitialized state and acting on it) maps to `IdleCreditVault`'s per-epoch receipt accounting. `defaultPendingClaimBasis()` only counts instant-withdraw receipts recorded under `instantWithdrawClaimsByEpoch[epochNumber]` (the current/default epoch), but `claimInstantWithdrawRequest` pays out the *aggregate* `instantWithdrawsRequests[_user]`. An instant receipt created in an earlier epoch and still unfunded is invisible to the recovery-basis computation, escapes the `defaultRecoveryPrice` haircut, and is later paid at par from strategy-held underlying.

### Finding Description
- `requestInstantWithdraw` records basis per request epoch: `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` and `instantWithdrawClaimsByEpoch[currentEpoch]` [1](#0-0) .
- At default finalization, only the *current* epoch's instant claims join the haircut basis: `basis += instantWithdrawClaimsByEpoch[epochNumber]` [2](#0-1) .
- Post-default, `_claimDefaultedInstantWithdrawRequest` clears only `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]`; receipts keyed to an older epoch remain inside `instantWithdrawsRequests[_user]` [3](#0-2) .
- `claimInstantWithdrawRequest` then burns and pays the full aggregate `instantWithdrawsRequests[_user]` at par via `_transferFundedClaim` [4](#0-3) .

Attack sequence:
1. Epoch N-1, buffer/running: attacker calls `cdoEpoch.requestInstantWithdraw(...)` on a tranche; strategy burns CDO tokens, mints the attacker a receipt, and records it under epoch N-1. IdleCDO has insufficient liquidity, so `collectInstantWithdrawFunds` is never called — `pendingInstantWithdraws` stays > 0 and the receipt is unfunded.
2. `stopEpoch`/`startEpoch` advance to epoch N (honest manager calls). The attacker's N-1 receipt remains unfunded.
3. During epoch N the borrower defaults; CDO calls `finalizeDefaultRecovery`. The haircut basis includes `pendingWithdraws` + `instantWithdrawClaimsByEpoch[N]` — the attacker's N-1 basis is excluded, so `defaultRecoveryReserve`/`defaultRecoveryPrice` do not account for it.
4. `defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0` is true, but `_claimDefaultedInstantWithdrawRequest` only clears epoch-N receipts, leaving the attacker's N-1 receipt in `instantWithdrawsRequests`.
5. Attacker calls `claimInstantWithdrawRequest`. `_transferFundedClaim` only checks `balance - defaultRecoveryReserve >= amount` [5](#0-4) , so the attacker is paid 1:1 from non-reserve strategy underlying (funded claims of other users, deposited liquidity) — a receipt that economically belongs to the defaulted pool is paid with zero haircut.

### Impact Explanation
Direct theft/insolvency: the attacker redeems an unfunded defaulted-epoch-equivalent receipt at 100% par, consuming underlying that belongs to other funded claimants or the recovery pool. Loss is bounded by the attacker's instant-withdraw request size (up to the pool's liquid balance at request time). Alternatively, if balance == reserve, all claims revert, permanently freezing the reserve for defaulted users.

### Likelihood Explanation
Requires instant withdrawals enabled (AA-tranche instant flag), a moment where IdleCDO lacks liquidity to fund the instant claim (normal when funds are lent to the borrower), and a subsequent borrower default — all reachable by an unprivileged tranche holder without any privileged misbehavior. The window is one epoch boundary.

### Recommendation
Include all unfunded instant receipts in the default basis regardless of request epoch — e.g., track a global pending-instant basis (`pendingInstantWithdraws` plus funded remainder) rather than only `instantWithdrawClaimsByEpoch[epochNumber]` — or route *all* unsettled instant receipts through the recovery-price path at finalization by clearing every epoch key the user holds, not just `defaultRecoveryEpoch`.

### Proof of Concept
```solidity
// Foundry fork test sketch (contracts/strategies/idle/IdleCreditVault.sol)
function testStaleInstantReceiptEscapesDefaultHaircut() external {
    // epoch N-1: user deposits AA and requests instant withdraw
    uint256 amt = 100_000 * ONE_SCALE;
    _depositWithUser(attacker, amt);
    vm.prank(manager); cdoEpoch.startEpoch();
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(instantAmt, address(AAtranche));
    // IdleCDO has no liquidity -> collectInstantWithdrawFunds never called;
    // pendingInstantWithdraws > 0, receipt keyed to epoch N-1

    _stopCurrentEpoch();          // epoch bumps to N
    vm.prank(manager); cdoEpoch.startEpoch();

    // borrower defaults in epoch N with partial recovery
    _defaultBorrower(50);         // 50% recovery
    vm.prank(manager);
    cdoEpoch.finalizeDefaultRecovery(recovered, recoverySource);

    // attacker claims: stale epoch N-1 receipt pays at PAR
    uint256 pre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    assertEq(underlying.balanceOf(attacker) - pre, instantAmt); // no haircut applied
    // reserve/other funded claimants are shorted by instantAmt * (1 - recoveryPrice)
}
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L380-393)
```text
  function claimInstantWithdrawRequest(address _user) external {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
      // Clear the defaulted-epoch instant receipt first, then continue so the same call can
      // also pay any older instant receipt that was already funded before default finalization.
      _claimDefaultedInstantWithdrawRequest(_user);
    }
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-648)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L842-855)
```text
  function _claimDefaultedInstantWithdrawRequest(address _user) internal returns (uint256 claimBasis) {
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-906)
```text
  function _transferFundedClaim(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    uint256 reserve = defaultRecoveryReserve;
    if (reserve != 0) {
      uint256 balance = underlyingToken.balanceOf(address(this));
      // This should be unreachable when accounting is consistent. Keep the guard so old funded
      // receipts can never spend underlyings reserved for default recovery claimants.
      if (balance < reserve || balance - reserve < _amount) revert NotAllowed();
    }
    underlyingToken.safeTransfer(_user, _amount);
```
