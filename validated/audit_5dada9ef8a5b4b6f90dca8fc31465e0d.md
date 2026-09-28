### Title
A second `requestWithdraw` overwrites `lastWithdrawRequest`, permanently shadowing a prior loss-adjusted epoch receipt so it is later paid at par instead of at its haircut — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` records loss-adjusted (partially funded) withdraw receipts per epoch via `lossRecoveryPriceByEpoch`, but it locates a user's loss-adjusted receipt through a single mutable marker, `lastWithdrawRequest[_user]` (`_claimLossAdjustedWithdrawRequest`, lines 789-800). When a user makes a second withdraw request in a later epoch, `requestWithdraw` overwrites that marker (line 282) without clearing the earlier per-epoch entry. The shadowed epoch's `withdrawsRequestsByEpoch` / `withdrawsRequests` amounts are never cleared, so `_claimFundedWithdrawRequest` pays the full un-haircutted basis at par while the strategy only collected the reduced funded amount. This mirrors CVE-2022-0675's bug class: a new entry ("rule") keys the same identity while an older unmanaged entry silently persists and later gets treated as fully authoritative.

### Finding Description
Relevant code:

- `requestWithdraw` (lines ~271-295): mints receipt tokens, sets `lastWithdrawRequest[_user] = currentEpoch`, and accumulates `withdrawsRequests[_user] += _amount` and `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount`. There is no guard against a pre-existing unclaimed receipt; the comment at lines 323-324 explicitly documents that re-requesting before claiming is supported.
- `collectWithdrawFunds` (lines 411-430): when the borrower funds `_amount < pendingBasis`, it stores `lossRecoveryPriceByEpoch[epochNumber] = _amount * RECOVERY_FULL / pendingBasis`, zeroes `pendingWithdraws`, and pulls only `_amount` of underlying into the strategy. The shortfall is a real loss assigned to that epoch's receipts.
- `_claimLossAdjustedWithdrawRequest` (lines 789-800): computes `lossEpoch = lastWithdrawRequest[_user]` and looks up `lossRecoveryPriceByEpoch[lossEpoch]`. If a second request moved `lastWithdrawRequest` to a fully funded epoch E+1, the lookup returns 0, the epoch-E entry is never cleared by `_clearWithdrawClaimForEpoch`, and the haircut is never applied.
- `_claimFundedWithdrawRequest` (lines 319-350): after `epochNumber > lastWithdrawRequest[_user]`, it pays `withdrawsRequests[_user]` (which still includes the shadowed epoch-E full basis) at par via `_transferFundedClaim`.

Attack trace (fixed-APR mode, honest manager):

1. Epoch E (running): attacker calls `requestWithdraw` for amount X. `lastWithdrawRequest[attacker] = E`, `withdrawsRequests[attacker] = X`, `withdrawsRequestsByEpoch[attacker][E] = X`.
2. Manager calls `stopEpochWithDuration`/`stopEpoch` and the borrower only funds part of `pendingWithdraws` → `lossRecoveryPriceByEpoch[E] = r < RECOVERY_FULL`; strategy holds only `r * X / RECOVERY_FULL` for the attacker (plus other users' funded shares).
3. Attacker does not claim. In epoch E+1 (buffer), attacker calls `requestWithdraw` again for a dust amount Y. `lastWithdrawRequest[attacker] = E+1`; the epoch-E per-epoch entry survives.
4. Epoch E+1 ends fully funded (`lossRecoveryPriceByEpoch[E+1] == 0`). One more epoch passes so `epochNumber > lastWithdrawRequest`.
5. Attacker calls `claimWithdrawRequest`. `_claimLossAdjustedWithdrawRequest` checks only epoch E+1 → 0 → returns early without clearing epoch E. `_claimFundedWithdrawRequest` pays `X + Y` at par from strategy underlyings.

The attacker receives `X * r / RECOVERY_FULL + Y` worth of real backing but withdraws `X + Y`, i.e. steals `X * (1 - r)` belonging to other funded claimants (or leaves later claimants' transfers reverting — permanent freezing of their unclaimed claims).

### Impact Explanation
Direct theft / insolvency. The strategy's funded-claim reserve is intentionally under-collateralized by exactly the loss applied in step 2; paying the shadowed receipt at par overdraws that reserve by `X * (1 - lossRecoveryPrice)`. Loss is bounded by the attacker's receipt size, but the attacker controls X arbitrarily (KYC-passing lender, any tranche holder), so up to the entire epoch-E loss socialized across pending receipts can be extracted by the first user to re-request and claim, breaking both "one receipt one payout" and solvency invariants.

### Likelihood Explanation
Requires a `stopEpochWithDuration`/`stopEpoch` partial-funding event (borrower shortfall loss allocated to pending receipts, which requires `defaultRecoveryInitialized`) plus a user who re-requests instead of claiming. No privileged misbehavior is needed — manager calls are honest protocol sequencing, and any ordinary lender can trigger the shadowing with a dust re-request. The sequence is entirely within allowed phases (running → stopped with loss → buffer → funded → claim).

### Recommendation
In `_claimLossAdjustedWithdrawRequest` (and the claim pipeline generally), iterate all epochs with a nonzero `lossRecoveryPriceByEpoch` for which `withdrawsRequestsByEpoch[_user][epoch] != 0`, rather than trusting `lastWithdrawRequest` to identify the single loss epoch. Alternatively, prevent shadowing at the source: in `requestWithdraw`, revert or force-claim if `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]] != 0` and the user's per-epoch entry for that epoch is still nonzero, so an unresolved loss-adjusted receipt can never be shadowed by a new request epoch.

### Proof of Concept
A Foundry test (extending the existing `test/foundry/IdleCreditVault.t.sol` harness, which already exercises `stopEpochWithDuration` loss funding and `lossRecoveryPriceByEpoch`) would be:

```solidity
// assumes: deposit, epoch E started, user requests withdraw of X
// manager: stopEpochWithDuration funding only 50% of pendingWithdraws
//   -> lossRecoveryPriceByEpoch[E] = RECOVERY_FULL/2, strategy gets 0.5*X
// epoch E+1 buffer: user calls requestWithdraw(Y dust) -> lastWithdrawRequest = E+1
// epoch E+1 fully funded by borrower; warp past epoch E+2 start
// user calls cdoEpoch.claimWithdrawRequest()
assertEq(underlyingReceived, X + Y);          // BUG: expected X/2 + Y
// subsequent claimant's claim now reverts / is underpaid by X/2
```

Uncertainty note: I could not fully verify in the remaining budget whether `requestWithdraw` calls `_ensureDefaultRecoveryInitialized` (required so that `collectWithdrawFunds` accepts partial funding) or whether any other path clears stale `withdrawsRequestsByEpoch` entries on re-request; the snippets at lines 271-294 show no such clearing. If `defaultRecoveryInitialized` is never set outside the default flow, the loss path reverts and the bug is unreachable — that should be confirmed before finalizing the finding.