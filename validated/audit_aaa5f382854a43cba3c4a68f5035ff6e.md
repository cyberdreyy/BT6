### Title
`lastWithdrawRequest` single-slot pointer orphans older loss-adjusted withdraw receipts — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` tracks each user's "latest" withdraw-request epoch in a single slot `lastWithdrawRequest[_user]`, while loss-adjusted funding prices are stored per epoch in `lossRecoveryPriceByEpoch[epochNumber]` (`collectWithdrawFunds`, L421). `_claimLossAdjustedWithdrawRequest` resolves the claimable loss epoch exclusively via `lastWithdrawRequest[_user]` (L790), and `_clearWithdrawClaimForEpoch` unconditionally resets that pointer to `0` when the cleared epoch is the stored one (L832-836). This is the direct analog of the splay-tree `remove_node` bug: clearing the "root" (latest) node without re-linking the still-present older node orphans `withdrawsRequestsByEpoch[_user][olderEpoch]`, whose receipt keeps a nonzero balance and `withdrawsRequests[_user]` aggregate, but can never again be matched to its loss-adjusted price.

### Finding Description
The storage layout supports multiple pending receipts per user across epochs (`withdrawsRequestsByEpoch[_user][epoch]`, `apr0Users`, `instantWithdrawsRequestsByEpoch`), and `collectWithdrawFunds` can record a haircut price for each stop-epoch-with-loss via `lossRecoveryPriceByEpoch[epochNumber]`. However the claim path only ever consults `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` (L790-791). After the latest epoch's claim is cleared, `lastWithdrawRequest[_user] = 0` is written even though an older `withdrawsRequestsByEpoch` entry for the same user remains nonzero. The older receipt becomes unreachable through `_claimLossAdjustedWithdrawRequest` (lookup returns 0 → early return), while its basis is still counted in `withdrawsRequests[_user]`/`_hasWithdrawRequest` (L886-892). The structure either permanently freezes the user's older receipt or, if it is later claimed through the aggregate funded path, pays it without its epoch haircut — breaking the loss waterfall since the reserve only contains the haircutted amount for it.

### Impact Explanation
A tranche holder with pending receipts in two distinct epochs where both stops were loss-adjusted (or the earlier was loss-adjusted and the latest funded) ends up with an orphaned claim basis: the older receipt's share of the funded reserve is stranded or mis-priced. Quantified loss equals `withdrawsRequestsByEpoch[user][oldEpoch] * lossRecoveryPriceByEpoch[oldEpoch] / RECOVERY_FULL` (frozen), or the corresponding over-draw from the reserve harming other claimants (insolvency). Broken invariant: "one receipt one payout" / loss waterfall conservation.

### Likelihood Explanation
Requires two user requests across epochs separated by a `stopEpochWithDuration(_lossAmount > 0)`, which is an honest manager/borrower flow explicitly supported by `previewLossAdjustedWithdrawFunds`/`collectWithdrawFunds` and exercised in the test suite. The attacker is merely a tranche-token holder calling `requestWithdraw` in two epochs — fully within the unprivileged model. No existing guard (pendingClaims gating, Default revert, KYC) prevents the sequence.

### Recommendation
Track all open request epochs per user (e.g., a per-user epoch set or a "previous epoch" link written when `lastWithdrawRequest` is overwritten), or iterate `withdrawsRequestsByEpoch` rather than relying on a single-slot pointer; on clearing the latest epoch, restore `lastWithdrawRequest` to the next older epoch that still has a nonzero claim.

### Proof of Concept
Foundry fork sketch:
1. User requests withdraw in epoch N; manager calls `stopEpochWithDuration(apr, 0, dur, loss)` with `loss > 0` → `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch[N]`.
2. User requests again in epoch N+1; another loss stop stores `lossRecoveryPriceByEpoch[N+1]` and `lastWithdrawRequest[user] = N+1`.
3. User calls `claimWithdrawRequest` → `_claimLossAdjustedWithdrawRequest` clears epoch N+1 and sets `lastWithdrawRequest[user] = 0`, leaving `withdrawsRequestsByEpoch[user][N] != 0`.
4. Subsequent `claimWithdrawRequest` finds `lossRecoveryPriceByEpoch[0] == 0` and early-returns → epoch-N payout is unreachable (assert `withdrawsRequests[user] > 0` persists while no path pays it).

Caveat: I verified the pointer-clearing and per-epoch price storage at L421, L789-801, and L811-837, but did not fully trace the aggregate `withdrawsRequests` funded-claim path, so whether the orphan manifests as freezing versus un-haircutted overpayment depends on that path's exact ordering — either outcome breaks the stated invariants.