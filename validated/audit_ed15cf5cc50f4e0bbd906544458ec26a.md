### Title
Pre-default-epoch unfunded instant-withdraw receipts are excluded from the recovery basis but still claimed at par, permanently freezing user funds - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`finalizeDefaultRecovery` computes the defaulted receipt basis via `defaultPendingClaimBasis()`, which — when `pendingInstantWithdraws != 0` — adds only `instantWithdrawClaimsByEpoch[epochNumber]` (the *current* epoch's instant claims). `pendingInstantWithdraws` is a global aggregate with no epoch attribution, and `collectInstantWithdrawFunds` decrements it globally as well, so unfunded instant receipts requested in *earlier* epochs can remain pending across an epoch boundary. Those stale-epoch receipts are never included in `totalBasis`, so no recovery reserve is allocated to them — the accounting "writes/reads" the wrong bucket, the direct analog of an out-of-bounds access corrupting a neighboring record. At claim time, `_claimDefaultedInstantWithdrawRequest` only clears the `defaultEpoch` bucket; the leftover `instantWithdrawsRequests[_user]` is then treated as fully funded and paid at par by `_transferFundedClaim`, which reverts whenever only recovery reserve is left.

### Finding Description
- `requestInstantWithdraw` increments the global `pendingInstantWithdraws` and the per-epoch `instantWithdrawsRequestsByEpoch[user][epochNumber]` / `instantWithdrawClaimsByEpoch[epochNumber]` (lines 366–374).
- `collectInstantWithdrawFunds` decrements `pendingInstantWithdraws` but never touches the per-epoch buckets (lines 398–403), so a partially funded instant queue can carry unfunded basis from epoch N into epoch N+1.
- `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` only for the *current* epoch (lines 644–649). Stale-epoch instant claims inside `pendingInstantWithdraws` inflate neither `basis` nor `reserveAmount`, so `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` allocates them zero recovery.
- After `defaultRecoveryFinalized`, `claimInstantWithdrawRequest` calls `_claimDefaultedInstantWithdrawRequest`, which clears only `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` (line 844) and saturates `pendingInstantWithdraws` to 0 when `claimBasis >= pending` (line 852). The remaining `instantWithdrawsRequests[_user]` — containing the stale-epoch unfunded receipts — is then paid at par via `_transferFundedClaim` (lines 387–392), which reverts at line 904 once the non-reserve balance is exhausted.

### Impact Explanation
Broken invariant: one receipt, one payout; loss socialization applies uniformly to all pending receipts. A user whose instant-withdraw request stayed unfunded across an epoch boundary has a valid receipt (strategy tokens minted to them at line 363) that (a) receives zero recovery allocation because its basis was omitted from `totalBasis`, and (b) cannot be paid at par because `_transferFundedClaim` forbids spending `defaultRecoveryReserve`. Their claim reverts permanently — permanent freezing of unclaimed yield/principal equal to their stale-epoch instant request amount. Additionally, because `pendingInstantWithdraws` is saturated to 0 by the first defaulted-epoch claim (line 852) while stale claims remain, subsequent accounting can misattribute reserve coverage, letting early defaulted-epoch claimants consume recovery pro-rata computed without the stale basis, diluting other claimants.

### Likelihood Explanation
Requires: instant withdrawals enabled (`setInstantWithdrawParams`), a user requesting an instant withdraw late in epoch N that is only partially funded (borrower cash shortfall at `startEpoch`, acknowledged as possible in the comment at lines 637–640), the epoch rolling over with `pendingInstantWithdraws != 0`, and a borrower default finalized in a later epoch via `finalizeDefaultRecovery`. All attacker actions are unprivileged (a KYC-passing lender requesting instant withdrawal); sequencing uses only honest manager/borrower calls. The omitted-basis path is reachable whenever `pendingInstantWithdraws` mixes multiple request epochs — no guard prevents instant requests in consecutive epochs.

### Recommendation
Track unfunded instant basis per epoch, or fold the entire `pendingInstantWithdraws` remainder into `defaultPendingClaimBasis` rather than only `instantWithdrawClaimsByEpoch[epochNumber]` (e.g., `basis += pendingInstantWithdraws` for the unfunded part plus the funded remainder). Correspondingly, `_claimDefaultedInstantWithdrawRequest` should clear and haircut *all* of a user's still-unfunded instant receipts, not only the `defaultRecoveryEpoch` bucket, and `_defaultPrefundedInstantReserve` should account for prefunded portions across epochs.

### Proof of Concept
Foundry fork PoC outline (mainnet fork per existing `test/foundry/IdleCreditVault.t.sol` setup):

```solidity
// test/foundry/InstantWithdrawCrossEpochDefault.t.sol
function testStaleEpochInstantReceiptFrozenAfterDefault() external {
    // Setup: instantiate IdleCDOEpochVariant + IdleCreditVault, enable instant
    // withdrawals via manager.setInstantWithdrawParams(delay, max, true).

    // Epoch N: attacker (KYC'd lender) deposits AA, then requests instant
    // withdraw of X during epoch N. Arrange borrower cash shortfall so
    // collectInstantWithdrawFunds funds only part -> pendingInstantWithdraws = X' > 0
    // with instantWithdrawsRequestsByEpoch[attacker][N] = X.

    // Epoch N+1: another user requests instant withdraw Y in epoch N+1.
    // Borrower defaults; CDO calls finalizeDefaultRecovery(recovered, source).

    // Observe: defaultPendingClaimBasis() = pendingWithdraws + instantWithdrawClaimsByEpoch[N+1]
    //          -> attacker's X excluded from totalBasis and reserve.

    // Attacker calls cdo.claimInstantWithdrawRequest():
    //  - _claimDefaultedInstantWithdrawRequest clears only bucket [N+1] (0 for attacker)
    //  - instantWithdrawsRequests[attacker] = X paid at par via _transferFundedClaim
    //  - balance - reserve < X -> revert NotAllowed()

    vm.expectRevert(NotAllowed.selector);
    // attacker claim permanently frozen; assert defaultRecoveryReserve untouched
    // and strategy balance holds only reserve backing other claimants.
}
```

Key assertions: `strategy.defaultPendingClaimBasis()` excludes `X`; `strategy.instantWithdrawsRequests(attacker)` remains `X` but the claim reverts; recovery `reserveAmount` was computed without `X`, so even if paid it would steal reserve belonging to other claimants.