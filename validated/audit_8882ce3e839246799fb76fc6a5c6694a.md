### Title
Already-claimed instant-withdraw receipts are counted again in the default-recovery basis, inflating the reserve with phantom funds and leaving the last recovery claimants unpayable - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary

The Shardus bug is a **duplicate-counting bug**: a node's vote is recorded per receipt but never deduplicated against the voter, so the same actor is counted many times and corrupts the consensus outcome. The idle-tranches analog is in `IdleCreditVault`: `claimInstantWithdrawRequest` clears only the aggregate `instantWithdrawsRequests[_user]` but never removes the per-epoch entries `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` (contracts/strategies/idle/IdleCreditVault.sol:380-393). Later, `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` re-read those stale per-epoch counters and count the already-paid receipt a second time in the default-recovery accounting (lines 644-649, 716-723).

### Finding Description

`requestInstantWithdraw` records each request in three places: the per-user aggregate `instantWithdrawsRequests`, the per-epoch `instantWithdrawsRequestsByEpoch[_user][epochNumber]`, and the global per-epoch `instantWithdrawClaimsByEpoch[epochNumber]` (lines 366-374). When the CDO funds the queue via `collectInstantWithdrawFunds` and the user calls `claimInstantWithdrawRequest`, only the aggregate is zeroed (lines 387-391); the per-epoch records persist forever for non-default paths.

If the borrower defaults in the *same* epoch — i.e., `finalizeDefaultRecovery` runs while `epochNumber` still equals the epoch in which the instant request was made and already claimed — then:

- `defaultPendingClaimBasis()` adds `instantWithdrawClaimsByEpoch[epochNumber]` (line 647), which still includes the already-paid amount. The paid receipt is "voted" into the claim basis a second time.
- `_defaultPrefundedInstantReserve()` computes `instantBasis - pendingInstant` (lines 719-722) as underlying already held by the strategy. The stale entry inflates this too, so `defaultRecoveryReserve` (line 691) is set higher than the actual strategy balance — a phantom reserve, since the paid-out tokens already left the contract.

After finalization, honest claimants draw down `defaultRecoveryReserve` via `_transferDefaultRecovery` (lines 912-917), which decrements the counter but relies on the contract's real balance for the transfer. Because the reserve was overstated by the double-counted receipt, the strategy runs out of underlying before the reserve counter reaches zero: the last claimant's `safeTransfer` reverts and their recovery claim is permanently frozen (default recovery is terminal; there is no retry path or top-up mechanism). Meanwhile the stale entry also makes the already-paid user's own `claimInstantWithdrawRequest` revert — `instantWithdrawsRequests[_user] -= claimBasis` underflows on their now-zero aggregate (line 848) — though that user's claim is already spent.

### Impact Explanation

Double-counting a single paid instant receipt of size `X` inflates the recovery reserve counter by `X` (phantom) while inflating the basis by `X`. Since `_recoveredAmount` does not cover the phantom portion, recovery payouts proceed normally until the real balance is exhausted, at which point the final claimant(s) lose up to `X` of recovery funds — permanently, because `defaultRecoveryFinalized` cannot be undone and there is no way to withdraw the shortfall from elsewhere. This is a direct loss/permanent freezing of user funds caused solely by an unprivileged lender requesting an instant withdraw, claiming it once funded, and the borrower defaulting in the same epoch — all benign user actions.

### Likelihood Explanation

Requires (a) instant withdraws enabled, (b) a user requesting and claiming an instant withdrawal within an epoch, and (c) a borrower default finalized while `epochNumber` still equals that epoch and `pendingInstantWithdraws != 0` (i.e., other instant requests remain unfunded). Each condition is an ordinary protocol state; the conjunction is narrow but plausible, and the unprivileged lender triggering it needs no malicious intent beyond timing their claim — the corrupted accounting then affects every defaulted-epoch claimant.

### Recommendation

In `claimInstantWithdrawRequest` (and the funded-claim branch), also clear the per-epoch records for the request epochs being paid — e.g., zero `instantWithdrawsRequestsByEpoch[_user][epoch]` and decrement `instantWithdrawClaimsByEpoch[epoch]` — so a receipt cannot be counted once as a paid claim and again as recovery basis/reserve. Apply the same per-epoch cleanup to `withdrawsRequestsByEpoch` in `_claimFundedWithdrawRequest` for defense-in-depth, since it has the same stale-entry pattern.

### Proof of Concept

Foundry fork test sketch (against `IdleCreditVault` via `IdleCDOEpochVariant`):

1. `cdoEpoch.setInstantWithdrawParams(delay, minAprDelta, true)` as manager.
2. `attacker` deposits AA; `victim` deposits AA. `startEpoch()`.
3. Both call `cdoEpoch.requestInstantWithdraw(...)` in epoch E.
4. Borrower/manager funds enough for the attacker's request only; `collectInstantWithdrawFunds` reduces `pendingInstantWithdraws` to the victim's amount.
5. Attacker calls `cdoEpoch.claimInstantWithdrawRequest()` — paid at par; `instantWithdrawsRequests[attacker] == 0` but `instantWithdrawClaimsByEpoch[E]` still includes the attacker's amount.
6. Borrower defaults: `cdoEpoch.stopEpochWithDuration`/default path → `finalizeDefaultRecovery(recovered, source)` executes while `epochNumber == E` and `pendingInstantWithdraws != 0`.
7. Assert `defaultPendingClaimBasis()` and `defaultRecoveryReserve` exceed the true outstanding basis and real strategy balance by the attacker's claimed amount.
8. Victim calls `claimInstantWithdrawRequest`/`claimWithdrawRequest` — the recovery transfer reverts on insufficient underlying, permanently freezing the victim's claim, or pays less than the honest recovery price.

Caveat: I verified the stale per-epoch accounting and the finalization reads in `IdleCreditVault.sol`, but did not fully trace the `IdleCDOEpochVariant` default path to confirm `epochNumber` at finalization can equal the request epoch in the same transaction window; the PoC must confirm that timing (instant claims within the buffer/epoch boundary before `epochNumber` bumps).