### Title
Settled APR0 withdraw receipts escape the `lossRecoveryPriceByEpoch` haircut because `requestWithdraw`'s guard ignores `settledPrincipal`/`settledInterest` - (contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The kernel bug (CVE-2016-2547) is a locking scheme that forgot "slave" instances — the analog here is that `requestWithdraw` protects `lastWithdrawRequest` from being moved past a loss-adjusted epoch, but the guard only checks `withdrawsRequestsByEpoch[_user][lossEpoch]` and `apr0Users[_user].principal`. Once an APR0 request settles, its principal moves to `settledPrincipal`/`settledInterest`, the guard passes, a new request moves the `lastWithdrawRequest` pointer, and the old loss-epoch receipt is later paid **at par** by `_claimFundedWithdrawRequest` instead of being haircutted. One receipt is paid more than its funded share, draining the loss-epoch funding from other claimants.

### Finding Description
When `collectWithdrawFunds` receives less than `pendingWithdraws`, it stores `lossRecoveryPriceByEpoch[epochNumber]` and zeroes `pendingWithdraws` (lines 411-426). Receipts from that epoch must then be claimed through `_claimLossAdjustedWithdrawRequest`, which pays `claimBasis * lossRecoveryPrice / RECOVERY_FULL` — but it only resolves the loss epoch via `lastWithdrawRequest[_user]` (line 790).

The guard in `requestWithdraw` (lines 263-271) prevents moving `lastWithdrawRequest` when a loss-epoch receipt exists, but it checks:
- `withdrawsRequestsByEpoch[_user][lossEpoch]` — never set for APR0 requests (line 285-294 routes them to `_requestWithdrawApr0`), and
- `apr0Users[_user].principal` — which `_settleApr0` clears to 0 while moving the value into `settledPrincipal`/`settledInterest` (lines 555-565).

So after an APR0 receipt from a loss epoch settles (which happens lazily inside `requestWithdraw` via `_requestWithdrawApr0` → `_settleApr0`, or inside `_claimFundedWithdrawRequest`), the guard sees all zeros and lets the user open a new request in a later epoch, overwriting `lastWithdrawRequest`. On the next `claimWithdrawRequest`, `_claimLossAdjustedWithdrawRequest` reads the *new* epoch (price 0) and returns 0; `_claimFundedWithdrawRequest` then pays `settledPrincipal + settledInterest` **at par** (lines 338-349). Notably `_hasWithdrawRequest` (lines 886-892) does account for `settledPrincipal`/`settledInterest`, so the loss-epoch guard is inconsistent with the post-default guard — the "slave" buckets were considered in one place but not the other.

### Impact Explanation
The borrower only funded `pendingBasis - pendingLoss` for that epoch (`previewLossAdjustedWithdrawFunds`, lines 454-459) and `pendingWithdraws` was zeroed. Paying any settled APR0 receipt at par spends more than the funded reserve for that epoch: it directly steals the haircut difference from the strategy's funded balance, i.e., other loss-epoch claimants (or active LPs' claimable value) are shorted by `settledAmount * (1 - lossRecoveryPrice/RECOVERY_FULL)`. With a large APR0 position and a material loss, this is a direct theft/insolvency, not a rounding artifact.

### Likelihood Explanation
Requires: an APR0-mode vault (`unscaledApr == 0`), a user with an open APR0 withdraw request in the epoch where `stopEpochWithDuration`/`collectWithdrawFunds` applies a partial loss, and a subsequent withdraw request after the APR0 receipt settles. All attacker steps are unprivileged `requestWithdraw`/`claimWithdrawRequest` calls sequenced around honest manager/borrower epoch calls. The precondition (a partial loss epoch on an APR0 vault) is an edge case but fully within normal protocol operation.

### Recommendation
In `requestWithdraw`, extend the loss-epoch guard to also revert when `apr0Users[_user].settledPrincipal != 0` or `settledInterest != 0` originate from a loss epoch (e.g., store `principalEpoch` alongside settled amounts, or check `apr0Users[_user].settledPrincipal != 0 && lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]] != 0`). Alternatively, apply the `lossRecoveryPrice` multiplier to settled APR0 amounts inside `_claimFundedWithdrawRequest`/`_settleApr0` when their request epoch has a nonzero recovery price.

### Proof of Concept
Foundry fork PoC sketch (APR0 vault, i.e., `unscaledApr == 0` configured on `IdleCreditVault`):

```solidity
// Setup: APR0-mode IdleCreditVault + IdleCDOEpochVariant, attacker is KYC'd lender.
// 1. Epoch N running: attacker deposits, then cdo.requestWithdraw(amount, tranche)
//    -> IdleCreditVault.requestWithdraw routes to _requestWithdrawApr0,
//       apr0Users[attacker].principal = amount, principalEpoch = N,
//       lastWithdrawRequest[attacker] = N.
// 2. Honest manager calls stopEpochWithDuration with a partial _lossAmount;
//    borrower funds pendingToFund < pendingWithdraws.
//    -> collectWithdrawFunds sets lossRecoveryPriceByEpoch[N] < RECOVERY_FULL,
//       pendingWithdraws = 0.
// 3. Attacker calls requestWithdraw again (epoch N+1 buffer):
//    _requestWithdrawApr0 -> _settleApr0 moves principal into settledPrincipal/settledInterest
//    and clears principal. Guard at line 263 sees withdrawsRequestsByEpoch[N]==0 and
//    apr0Users.principal==0 -> passes; lastWithdrawRequest = N+1.
// 4. After epoch N+1 ends, attacker calls cdo.claimWithdrawRequest():
//    _claimLossAdjustedWithdrawRequest reads lastWithdrawRequest = N+1 -> price 0 -> returns 0.
//    _claimFundedWithdrawRequest pays settledPrincipal + settledInterest AT PAR.
// 5. Assert: payout > claimBasis * lossRecoveryPriceByEpoch[N] / RECOVERY_FULL,
//    and strategy's funded balance is short vs. other N-epoch claimants' entitlements.
```

Caveat I could not fully verify within this analysis: whether `_settleApr0`'s `apr0RateByEpoch` interaction or another guard elsewhere (e.g., inside the CDO's claim wrapper) blocks step 4 — the PoC must confirm that the par payout actually executes. The inconsistency between the `requestWithdraw` loss guard and `_hasWithdrawRequest` (which does check settled buckets) strongly indicates the settled-APR0 path was not covered for loss epochs.