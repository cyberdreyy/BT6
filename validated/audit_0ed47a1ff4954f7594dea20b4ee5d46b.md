### Title
Default recovery accounting keys instant-withdraw claims to the current epoch while instant balances are all-epoch aggregates — stale-epoch unfunded instant receipts escape the recovery haircut and become permanently unclaimable or drain funded holdings - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` tracks instant-withdraw obligations with two different key granularities: `pendingInstantWithdraws` and `instantWithdrawsRequests[user]` are global aggregates across all epochs, while `instantWithdrawClaimsByEpoch[epochNumber]` and the default-recovery path (`defaultPendingClaimBasis`, `_claimDefaultedInstantWithdrawRequest`) are keyed strictly to the current `epochNumber`. When an instant receipt created in an earlier epoch is still unfunded at default finalization (because `collectInstantWithdrawFunds` only partially covered the aggregate queue), it is excluded from the recovery basis yet remains inside the aggregate paid by `claimInstantWithdrawRequest`. This is the same defect class as the reference kernel bug: a lookup keyed on a shared identifier (here `epochNumber` / aggregate counters, there `sname`) matches or misses entities of the wrong "type" (stale-epoch vs. current-epoch receipts), producing a miscalculated count — the analogue of the kernel's miscounted ALH device count.

### Finding Description
- `requestInstantWithdraw` increments the aggregate `instantWithdrawsRequests[user]`, the per-epoch `instantWithdrawsRequestsByEpoch[user][currentEpoch]`, the per-epoch total `instantWithdrawClaimsByEpoch[currentEpoch]`, and the global `pendingInstantWithdraws` (contracts/strategies/idle/IdleCreditVault.sol:366-374).
- `collectInstantWithdrawFunds` decrements `pendingInstantWithdraws` by an arbitrary `_amount` chosen by the CDO, so a partially unfunded remainder can persist across epoch boundaries (lines 398-403). There is no per-epoch tracking of which receipts the remainder belongs to.
- At default finalization, `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` — only the *current* epoch's instant claim basis — and only when `pendingInstantWithdraws != 0` (lines 644-649). `_defaultPrefundedInstantReserve` uses the same single-epoch key (lines 716-723). `defaultInstantWithdrawsFinalized` is set from the global `pendingInstantWithdraws` (line 696).
- After finalization, `claimInstantWithdrawRequest` clears only `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` via `_claimDefaultedInstantWithdrawRequest` (lines 842-856), then pays the *entire remaining aggregate* `instantWithdrawsRequests[user]` at par via `_transferFundedClaim` (lines 387-392).
- A stale-epoch unfunded instant receipt therefore falls between the two paths: it was never added to `totalBasis` (so no recovery reserve was priced for it), but it is still in the aggregate that the funded-claim path tries to pay at full par.

Two concrete failure modes follow:

1. **Permanent freeze (most common):** post-finalization, essentially all strategy underlying is `defaultRecoveryReserve` (recovery + prefunded instant + reserved amounts, lines 685-691). `_transferFundedClaim` reverts when `balance - reserve < _amount` (lines 900-904), so the stale receipt holder's claim reverts forever — a claim that under the intended design should pay `defaultRecoveryPrice * basis` pays nothing, and the corresponding reserve portion stays locked as unclaimable dust.
2. **Theft from other funded claimants:** if the strategy holds any non-reserve underlying (e.g., borrower-funded amounts collected for other unfinalized claims, or a `recoveryPrice > RECOVERY_FULL` surplus), the stale receipt is paid 1:1 from those funds instead of being haircutted, directly diluting honest claimants.

### Impact Explanation
Invariant broken: "one receipt, one haircutted payout" and isolation of the recovery reserve. An unprivileged user (KYC'd tranche holder) who requests an instant withdrawal that is only partially funded before an epoch rollover creates a receipt that permanently escapes default-recovery accounting. Loss is quantified as `staleReceiptAmount` paid at par instead of `staleReceiptAmount * defaultRecoveryPrice / 1e18`, or equivalently `staleReceiptAmount * defaultRecoveryPrice / 1e18` permanently frozen when the reserve guard reverts. The freeze also wedges `claimInstantWithdrawRequest` for the affected user entirely, since the revert occurs inside the same call that would pay any legitimately funded remainder.

### Likelihood Explanation
Requires an instant-withdraw request whose funding is short at `stopEpoch`/`startEpoch` (borrower liquidity shortfall — an honest-borrower condition the code explicitly anticipates: "that cash covered only part of the instant queue", line 638-640), then a borrower default in a later epoch before the queue clears. Both are normal operating conditions, not attacker privilege. The attacker needs only to leave a partially funded instant receipt unclaimed across an epoch boundary — no privileged action involved. The gap exists because `_ensureDefaultRecoveryInitialized` reverts on nonzero `pendingInstantWithdraws` only at lazy-init time (line 926), after which carry-over is unrestricted.

### Recommendation
Track pending instant withdrawals per epoch (or track a per-epoch unfunded remainder) so `defaultPendingClaimBasis` includes all unfunded instant receipts, not just `instantWithdrawClaimsByEpoch[epochNumber]` — e.g., `basis += pendingInstantWithdraws` reconciled against per-epoch claim maps — and make `_claimDefaultedInstantWithdrawRequest` iterate/clear all epochs contributing to a user's `instantWithdrawsRequests`, analogous to the kernel fix that checks the widget type instead of matching on the stream name alone. Alternatively, enforce that `pendingInstantWithdraws` must be zero outside the current epoch's funding window.

### Proof of Concept
Foundry fork PoC sketch (setup mirrors `test/foundry/IdleCreditVault.t.sol` helpers `_depositWithUser`, `_startEpochAndCheckPrices`, `_stopCurrentEpoch`):

```solidity
// Epoch N: user A deposits AA, then requests instant withdraw of X.
cdoEpoch.requestInstantWithdraw(xAmount, address(AAtranche));
// Borrower liquidity shortfall: CDO collects only part via collectInstantWithdrawFunds,
// so pendingInstantWithdraws == X - fundedPart > 0 after the epoch rolls.
// Epoch N+1..M: normal operation; A never claims.
// Epoch M: borrower defaults; owner/manager finalize:
//   finalizeDefaultRecovery pulls recoveredAmount;
//   defaultPendingClaimBasis() = pendingWithdraws + instantWithdrawClaimsByEpoch[M]
//   -> A's stale receipt (epoch N) contributes to pendingInstantWithdraws
//      but NOT to instantWithdrawClaimsByEpoch[M], so no reserve is priced for it.
// Post-finalization:
vm.prank(manager); // or via cdoEpoch.claimInstantWithdrawRequest()
cdoEpoch.claimInstantWithdrawRequest();
// Case 1 (only reserve held): _transferFundedClaim reverts NotAllowed
//   -> A's claim frozen forever; reserve dust stranded.
// Case 2 (extra non-reserve balance exists): A is paid X at par,
//   stealing (X - X*defaultRecoveryPrice/1e18) from recovery claimants.
assert(strategy.defaultRecoveryReserve() /* decreased by unpriced payout or claim reverted */);
```

Note: I could not fully trace `IdleCDOEpochVariant`'s instant-funding path (only match counts were returned for it) to confirm every route that leaves `pendingInstantWithdraws` nonzero across an epoch boundary, but `collectInstantWithdrawFunds` accepting a partial `_amount` and the code comments describing partially covered instant queues establish that carry-over is possible by design.