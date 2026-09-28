### Title
APR0 withdraw receipts from earlier epochs escape the `stopEpochWithDuration` loss haircut and are paid at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`collectWithdrawFunds` applies a shortfall loss to the *entire* `pendingWithdraws` aggregate and records a single per-epoch `lossRecoveryPriceByEpoch[epochNumber]`. The loss-adjusted claim path, however, only looks up the epoch pointed to by `lastWithdrawRequest[_user]` and only clears receipts keyed to that one epoch. APR0 receipts created in an earlier epoch (`apr0Users[_user].principalEpoch < lossEpoch`) are part of the haircutted `pendingWithdraws` basis but are never routed through `_claimLossAdjustedWithdrawRequest`; they are paid at par by `_claimFundedWithdrawRequest`, letting the user claim more than was funded and draining other claimants' recovery.

### Finding Description
This mirrors the upstream bug class: accounting "precision tracking" only follows the canonical marker (`lastWithdrawRequest`, one epoch pointer), while alternate-path receipts (APR0 principal from a different epoch) are part of the same loss bucket but invisible to the haircut.

- `collectWithdrawFunds` sets `lossRecoveryPriceByEpoch[epochNumber] = funded / pendingBasis` over the *global* `pendingWithdraws`, which includes all unclaimed APR0 principal and settled APR0 interest accumulated via `prepareStopEpochWithApr0` (`pendingWithdraws += _apr0NetInterest`).
- In `requestWithdraw`, the guard that is supposed to force claiming a loss-adjusted receipt first checks only `withdrawsRequestsByEpoch[_user][lossEpoch]` and `apr0Users[_user].principal != 0 && apr0Users[_user].principalEpoch == lossEpoch`. An APR0 receipt whose `principalEpoch` is an *earlier* epoch slips through, so the user may open a new request and move `lastWithdrawRequest` to the loss epoch.
- In `claimWithdrawRequest`, `_claimLossAdjustedWithdrawRequest` clears only claims for `lastWithdrawRequest[_user]` via `_clearWithdrawClaimForEpoch`. The APR0 bucket is cleared only when `apr0User.principalEpoch == _claimEpoch`, so old-epoch APR0 principal/interest survives and falls through to `_claimFundedWithdrawRequest`, which pays `settledPrincipal + principal + settledInterest` at par through `_transferFundedClaim`.

Result: the borrower funds `pendingWithdraws * lossRecoveryPrice`, but the APR0 user withdraws old-epoch basis unhaircutted. The haircut shortfall is socialized onto other pending claimants or leaves the strategy underfunded.

### Impact Explanation
Direct fund loss / insolvency. An unprivileged KYC-passing lender who requested an APR0 withdraw in an earlier epoch and a fresh normal withdraw in the loss epoch claims `(newBasis * lossRecoveryPrice) + oldApr0Basis`, while only `totalPending * lossRecoveryPrice` was transferred in. The excess equals `oldApr0Basis * (1 - lossRecoveryPrice)`, taken from funds reserved for other pending/default claimants. With e.g. 100k APR0 basis and a 50% loss price, ~50k underlying is overpaid.

### Likelihood Explanation
Requires: a vault operating in APR0 mode (`unscaledApr == 0`), the attacker holding an unclaimed APR0 receipt from epoch N, and the manager later calling `stopEpochWithDuration`/`stopEpoch` with a realized loss in epoch M > N while the attacker also has a normal request in epoch M (or makes one before `collectWithdrawFunds`). All attacker steps are ordinary user calls; the loss event is an honest manager action. No privileged misbehavior needed.

### Recommendation
Track the loss-epoch claim basis per user rather than relying on `lastWithdrawRequest`. Concretely:
- In `collectWithdrawFunds` (or a helper), snapshot per-epoch pending basis, and in `_claimLossAdjustedWithdrawRequest` compute the user's claim against *all* unclaimed receipt basis (normal + APR0 principal + APR0 interest) instead of only `withdrawsRequestsByEpoch[_user][lossEpoch]` and `principalEpoch == lossEpoch`.
- Extend the `requestWithdraw` guard to revert whenever `apr0Users[_user].principal != 0 || settledPrincipal != 0 || settledInterest != 0` while `lossRecoveryPriceByEpoch[epochNumber]` (or the latest loss epoch) is pending, regardless of `principalEpoch` matching.

### Proof of Concept
Foundry fork scenario (against `IdleCreditVault.t.sol` harness):
1. Set APR0 mode: `cdoEpoch.setIsAYSActive(false)`, `strategy.setAprs(0,0)`; deposit AA via `cdoEpoch.depositAA`.
2. Epoch N: `cdoEpoch.requestWithdraw(x, AAtranche)` → APR0 bucket (`apr0Users[user].principalEpoch = N`). Start epoch N+1; `stopEpoch` funds `pendingWithdraws` at par (borrower repays). Do not claim.
3. Epoch M (>N): `cdoEpoch.requestWithdraw(y, AAtranche)` — guard passes because `principalEpoch == N != M`. `lastWithdrawRequest[user] = M`.
4. `stopEpochWithDuration` with `_lossAmount` such that `collectWithdrawFunds(funded < pendingWithdraws)` sets `lossRecoveryPriceByEpoch[M] = p < 1e18`.
5. `cdoEpoch.claimWithdrawRequest()`: `_claimLossAdjustedWithdrawRequest` pays `y * p`; `_claimFundedWithdrawRequest` then pays `apr0 principal + settled interest` unhaircutted.
6. Assert `received > (x + y + interest) * p` and that strategy balance is short relative to remaining claims (or a second claimant's claim reverts/underpays).