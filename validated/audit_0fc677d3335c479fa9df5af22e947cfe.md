### Title
Stale `instantWithdrawsRequestsByEpoch` / `instantWithdrawClaimsByEpoch` records are never cleared on the funded-claim path, inflating `defaultPendingClaimBasis` and permanently locking part of the default recovery reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The pam_usb `xfree()` bug is "a release helper frees the buffer but leaves the old contents behind, so the stale data can be picked up again later." The closest analog in this codebase is the instant-withdraw receipt ledger in `IdleCreditVault`: `claimInstantWithdrawRequest` pays a funded instant receipt and clears only the aggregate `instantWithdrawsRequests[_user]`, but never clears the per-epoch records `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]`. If a default is later finalized while `epochNumber` is still that epoch (e.g., the same epoch in which the instant receipt was requested), `defaultPendingClaimBasis` counts the already-paid amount again via `instantWithdrawClaimsByEpoch[epochNumber]`, diluting `defaultRecoveryPrice`, and the phantom claimant's share of `defaultRecoveryReserve` can never be paid out — any attempt to clear it underflows on `instantWithdrawsRequests[_user] -= claimBasis` and reverts.

### Finding Description
`requestInstantWithdraw` records three counters per request: the aggregate `instantWithdrawsRequests[_user]`, the per-user per-epoch `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, and the global per-epoch `instantWithdrawClaimsByEpoch[currentEpoch]` [1](#0-0) .

`claimInstantWithdrawRequest` pays funded instant receipts at par but clears only the aggregate — the per-epoch entries are left behind [2](#0-1) . `collectInstantWithdrawFunds` likewise decrements only `pendingInstantWithdraws` and leaves `instantWithdrawClaimsByEpoch` untouched [3](#0-2) .

The stale data is re-read in two places once a default is finalized:

1. `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` to the recovery basis whenever `pendingInstantWithdraws != 0` — so a paid-out instant receipt from the same epoch inflates the basis [4](#0-3) .
2. `finalizeDefaultRecovery` divides the (fixed) reserve by that inflated `totalBasis`, lowering `defaultRecoveryPrice` for every legitimate claimant [5](#0-4) .

The phantom share is then unrecoverable: `_claimDefaultedInstantWithdrawRequest` reads the stale `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]`, but `instantWithdrawsRequests[_user] -= claimBasis` underflows because the aggregate was already zeroed, so the whole `claimInstantWithdrawRequest` call reverts for that user [6](#0-5) .

### Impact Explanation
Any unprivileged lender who makes an instant withdraw request in epoch N, gets it funded via `collectInstantWithdrawFunds`, and claims it, leaves behind a phantom claim. If the borrower then defaults in the same epoch N while another instant request is still pending (so `defaultInstantWithdrawsFinalized` is true), the phantom amount inflates `totalBasis`. Recovery price drops proportionally, every honest defaulted-epoch claimant (normal and instant) is underpaid, and the phantom share `reserve * phantom / totalBasis` is permanently frozen in the strategy — it cannot be claimed (the clearing path reverts) and the reserve guard in `_transferFundedClaim` prevents it from being spent by funded claims [7](#0-6) . For example, with a 100k instant receipt claimed pre-default, a 100k still-pending instant receipt, a 800k active basis, and a 500k reserve, the phantom basis cuts the recovery price from 50% to ~45.5% and strands ~45k of reserve.

### Likelihood Explanation
The trigger requires only ordinary user behavior: an instant withdrawal that is funded and claimed in the same epoch in which a borrower default is later finalized, while at least one instant receipt is still unfunded at finalization. Instant withdrawals exist precisely for early exits, so same-epoch claim-then-default is a realistic sequence rather than an edge case. No privileged misbehavior is needed — the stale accounting is created entirely by unprivileged user calls plus the normal borrower funding flow.

### Recommendation
Mirror the normal-withdraw clearing pattern used by `_clearWithdrawClaimForEpoch` on the funded instant-claim path: in `claimInstantWithdrawRequest` (or in `collectInstantWithdrawFunds`), zero `instantWithdrawsRequestsByEpoch[_user][epoch]` and decrement `instantWithdrawClaimsByEpoch[epoch]` for the epochs whose receipts are being paid, so that `defaultPendingClaimBasis` only counts receipts that are still outstanding at finalization. Since instant requests record a single `currentEpoch` at request time, storing the request epoch per user (or iterating it when claiming) suffices.

### Proof of Concept
Foundry fork/unit PoC outline against `test/foundry/IdleCreditVault.t.sol` helpers:

```solidity
function testStaleInstantReceiptInflatesDefaultBasis() external {
    _useStandardEpochVariant();
    _stopCurrentEpochWithApr(10e18);              // now in epoch 1 buffer
    // enable instant withdrawals
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(100, 1e18, false);

    uint256 amount = 10_000 * ONE_SCALE;
    uint256 tr = _depositWithUser(user1, amount);
    uint256 tr2 = _depositWithUser(user2, amount);

    // epoch N = strategy.epochNumber(); user1 requests instant withdraw
    vm.prank(user1);
    cdoEpoch.requestInstantWithdraw(tr, address(AAtranche)); // CDO calls strategy.requestInstantWithdraw
    vm.prank(user2);
    cdoEpoch.requestInstantWithdraw(tr2, address(AAtranche));

    // borrower funds both instant requests -> pendingInstantWithdraws -> 0
    // (CDO.collectInstantWithdrawFunds via the standard instant-funding flow)

    // user1 claims at par: aggregate cleared, per-epoch records STALE
    vm.prank(user1);
    cdoEpoch.claimInstantWithdrawRequest();

    uint256 epoch = strategy.epochNumber();
    assertEq(strategy.instantWithdrawsRequestsByEpoch(user1, epoch), amountWithInterest);
    assertEq(strategy.instantWithdrawClaimsByEpoch(epoch), 2 * amountWithInterest); // stale!

    // borrower defaults in the same epoch (user2's receipt still unfunded portion simulated
    // by leaving pendingInstantWithdraws > 0 via partial funding), then:
    // cdoEpoch.finalizeDefaultRecovery(...)
    // assert: defaultPendingClaimBasis includes user1's already-paid amount
    // assert: defaultRecoveryPrice < reserve / honestBasis
    // assert: user1.claimInstantWithdrawRequest() reverts (underflow on aggregate)
    // assert: stranded reserve == defaultRecoveryReserve - sum(claimable)
}
```

The concrete checks: `instantWithdrawClaimsByEpoch[epoch]` remains non-zero after a fully funded claim [2](#0-1) , `defaultPendingClaimBasis` re-adds it [4](#0-3) , and the stale clearing path reverts on `instantWithdrawsRequests[_user] -= claimBasis` [8](#0-7) .

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-403)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
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
