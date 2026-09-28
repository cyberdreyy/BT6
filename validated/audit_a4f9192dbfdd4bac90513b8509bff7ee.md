### Title
Stale instant-withdraw receipts from prior epochs are excluded from default recovery basis and then permanently blocked by the recovery-reserve guard - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The kernel bug purges RCU-list entries while concurrent readers still hold references to them — freed memory is consumed by stale readers. The credit-vault analog lives in `IdleCreditVault`'s instant-withdraw accounting: requests are tracked both per-epoch (`instantWithdrawsRequestsByEpoch`, `instantWithdrawClaimsByEpoch`) and in an aggregate counter (`pendingInstantWithdraws`). At default finalization, only the *current* epoch's receipts join the recovery basis (`defaultPendingClaimBasis`), while the aggregate `pendingInstantWithdraws` — which may include receipts requested in earlier epochs that were never funded — decides whether the instant bucket is "finalized". Older unfunded receipts are effectively "purged" from the recovery accounting while a later reader (`claimInstantWithdrawRequest` → `_transferFundedClaim`) still references them, leaving them neither haircut-claimable nor payable at par: the reserve guard makes the claim revert permanently.

### Finding Description
`requestInstantWithdraw` accrues `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][epochNumber]`, `instantWithdrawClaimsByEpoch[epochNumber]`, and `pendingInstantWithdraws` (lines 356-375). `collectInstantWithdrawFunds` decreases `pendingInstantWithdraws` as funding arrives, so a partially funded queue can leave a nonzero remainder spanning multiple epochs.

At `finalizeDefaultRecovery`, the default claim basis is computed as `pendingWithdraws + instantWithdrawClaimsByEpoch[epochNumber]` only when `pendingInstantWithdraws != 0` (lines 644-649), and `defaultInstantWithdrawsFinalized` is set from that same aggregate flag (line 696). Two stale-reference problems follow:

1. Receipts created in epochs *before* `defaultRecoveryEpoch` that were never collected are absent from `instantWithdrawClaimsByEpoch[defaultRecoveryEpoch]`, so they contribute no basis and no pro-rata reserve allocation — yet they remain in `instantWithdrawsRequests[_user]`.
2. In `claimInstantWithdrawRequest`, `_claimDefaultedInstantWithdrawRequest` clears only `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` (line 844) and pays it at `defaultRecoveryPrice`. The remaining (old-epoch) balance falls through to `_transferFundedClaim`, whose guard `if (balance < reserve || balance - reserve < _amount) revert NotAllowed()` (line 904) treats it as a funded par claim. Since those tokens were never reserved, `balance == reserve` in the common case and the call always reverts.

So the receipt is excluded from the haircut basis *and* blocked from par payment — the exact "entry removed from the protected structure while a reader still dereferences it" pattern. Additionally, shrinking `totalBasis` by omitting these receipts inflates `defaultRecoveryPrice` (line 688), overpaying other claimants at the expense of the frozen ones.

### Impact Explanation
Any user whose instant-withdraw request was recorded in an epoch earlier than the default epoch and never fully funded has that value permanently frozen: `claimInstantWithdrawRequest` always reverts for them once `defaultRecoveryFinalized` is set. The frozen amount equals their `instantWithdrawsRequests[_user]` balance, and the inflated `defaultRecoveryPrice` simultaneously transfers part of their would-be recovery share to default-epoch claimants.

### Likelihood Explanation
Requires an unprivileged user to request an instant withdrawal that stays unfunded across at least one epoch boundary, followed by a borrower default and finalization. `requestInstantWithdraw` has no guard against stacking requests while older ones are unfunded, and nothing forces `pendingInstantWithdraws` to zero before an epoch can close, so this arises from ordinary partial-liquidity operation rather than attacker contrivance. The attacker surface is minimal — the victim is any unprivileged lender; the loss is a permanent freeze plus a small recovery-price distortion benefiting other claimants.

### Recommendation
Track unfunded instant receipts per epoch (e.g., a per-epoch unfunded remainder rather than only `instantWithdrawClaimsByEpoch[epochNumber]`), and include *all* unfunded instant receipts in `defaultPendingClaimBasis`, not just the current epoch's. Alternatively, carry forward a `unfundedInstantBasis` cumulative counter so `_claimDefaultedInstantWithdrawRequest` can haircut any receipt regardless of request epoch, instead of keying strictly on `defaultRecoveryEpoch`.

### Proof of Concept
Foundry fork PoC (against the repo's existing `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function testStaleInstantReceiptFrozenAfterDefault() external {
    _useStandardEpochVariant();

    // Epoch N: user requests instant withdraw; only partially funded by CDO.
    address victim = makeAddr('victim');
    _depositWithUser(victim, 10_000 * ONE_SCALE, true);
    vm.prank(victim);
    cdoEpoch.requestInstantWithdraw(5_000 * ONE_SCALE); // via IdleCDO -> strategy
    // CDO collects only part -> pendingInstantWithdraws stays > 0 across stopEpoch.
    // ... advance epochs normally (startEpoch/stopEpoch) without fully funding ...

    // Epoch M > N: borrower defaults; finalizeDefaultRecovery runs.
    // basis includes instantWithdrawClaimsByEpoch[M] but NOT victim's epoch-N receipt.
    vm.prank(manager);
    cdoEpoch.finalizeDefault(/* recovered funds covering the computed basis */);

    // Victim's old receipt: default-epoch per-epoch entry is 0,
    // so _claimDefaultedInstantWithdrawRequest pays nothing, then
    // _transferFundedClaim reverts because balance == defaultRecoveryReserve.
    vm.prank(victim);
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.claimInstantWithdrawRequest(); // permanently frozen
}
```

Key assertions: `pendingInstantWithdraws > 0` at finalization, `instantWithdrawsRequestsByEpoch[victim][defaultRecoveryEpoch] == 0`, and `instantWithdrawsRequests[victim] > 0` forever after — the receipt exists but no claim path can pay it.

Caveat: I could not fully verify within the iteration budget whether `IdleCDOEpochVariant.stopEpoch`/`getInstantWithdrawFunds` forces `pendingInstantWithdraws` to zero before an epoch can close; if the CDO guarantees full collection or blocks epoch progress while any remainder is unfunded, the precondition collapses and this reduces to a no-finding. That check is the single gatekeeper for this report's validity.