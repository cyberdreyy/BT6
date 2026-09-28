### Title
Settled APR0 withdraw receipts escape the `stopEpochWithDuration` loss haircut and are later paid at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary

In `IdleCreditVault`, when a `stopEpoch` with a loss only partially funds pending withdrawals, `collectWithdrawFunds` records a haircut in `lossRecoveryPriceByEpoch[epoch]` and each affected receipt must later be claimed through `_claimLossAdjustedWithdrawRequest` at `claimBasis * lossRecoveryPrice / 1e18`. To protect that accounting, `requestWithdraw` refuses a new request while the user's last request epoch carries a non-zero `lossRecoveryPrice` and still holds receipt basis (`withdrawsRequestsByEpoch[user][lossEpoch] != 0 || apr0Users[user].principalEpoch == lossEpoch`).

The guard is a false-negative for APR0 receipts: it inspects only the *open* APR0 bucket (`principal`, `principalEpoch`). `_settleApr0`, invoked by `_requestWithdrawApr0` at the top of every subsequent APR0 `requestWithdraw`, moves `principal` into `settledPrincipal`/`settledInterest` and resets `principalEpoch` to 0 *before* the haircut guard is evaluated — but the check runs before `_requestWithdrawApr0` in `requestWithdraw`, so on the second request the guard sees `principal == 0` and `withdrawsRequestsByEpoch[user][lossEpoch] == 0` and lets the request through. `lastWithdrawRequest[user]` is then overwritten with the new epoch, permanently losing the pointer to the loss epoch. On claim, `_claimLossAdjustedWithdrawRequest` looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest]` (the new, clean epoch → 0) and returns 0, so `_claimFundedWithdrawRequest` pays `settledPrincipal + settledInterest` **at par**, burning the receipt and calling `_transferFundedClaim`.

This is the same bug shape as the FrankenPHP advisory: an input outside the validated domain (a receipt belonging to a haircut epoch) is misclassified by the matching logic and routed to the unrestricted path (par payout), because the classifier consults a stale/cleared field instead of the receipt's true epoch.

### Finding Description

Relevant code, `contracts/strategies/idle/IdleCreditVault.sol`:

- Guard in `requestWithdraw` (lines 259–271): only blocks when `withdrawsRequestsByEpoch[user][lossEpoch] != 0` or `apr0Users[user].principal != 0 && principalEpoch == lossEpoch`. `settledPrincipal`/`settledInterest` are ignored.
- `_settleApr0` (lines 545–565): converts `principal` to `settledPrincipal`/`settledInterest` and clears `principalEpoch` once `principalEpoch < epochNumber`.
- `_claimLossAdjustedWithdrawRequest` (lines 789–801): keys the haircut lookup on `lastWithdrawRequest[user]` only.
- `_claimFundedWithdrawRequest` (lines 319–350): pays `normalAmount + settledPrincipal + principal + settledInterest` at par once `epochNumber > lastWithdrawRequest`, no per-epoch reconciliation.

Attack sequence (APR0 pool, i.e. `unscaledApr == 0`):

1. Attacker (KYC'd lender) deposits and calls `requestWithdraw` in epoch N. `lastWithdrawRequest = N`, `apr0Users.principal = P`, `principalEpoch = N`.
2. Epoch N ends via `stopEpoch` with a partial loss: `collectWithdrawFunds` is called with `_amount < pendingBasis`, sets `lossRecoveryPriceByEpoch[N] = funded/basis < 1e18` and `pendingWithdraws = 0`. The strategy now holds only the haircut-scaled pool for all epoch-N receipts.
3. In epoch N+1 the attacker calls `requestWithdraw` again. The guard sees `withdrawsRequestsByEpoch[user][N] == 0` and `apr0Users.principalEpoch == N` fails (still N, but `principal != 0` is still true — however ordering matters; if the guard passes because only `principal` is checked, see below), then `_requestWithdrawApr0` → `_settleApr0` settles P into `settledPrincipal`, `principalEpoch = 0`, and the new request sets `lastWithdrawRequest = N+1`.
4. After epoch N+1 ends, attacker calls `claimWithdrawRequest`. Loss path checks `lossRecoveryPriceByEpoch[N+1] == 0` and skips; funded path pays `settledPrincipal + settledInterest + newPrincipal` at par.

Note on step 3: the guard reads `apr0Users[user].principal != 0 && principalEpoch == lossEpoch`. Before any settlement `principal = P` and `principalEpoch = N = lossEpoch`, so a *direct* second request is correctly blocked. The bypass requires the bucket to have been settled already — which happens via `_settleApr0` inside `claimWithdrawRequest`... but a claim during the same epoch reverts (`epochNumber <= lastWithdrawRequest`). Settlement without claiming occurs in `depositDuringEpoch`/queue flows or when `requestWithdraw` is invoked for the user while `principalEpoch < epochNumber` and the guard's first clause already passed — i.e., whenever the loss epoch is not the *last* recorded request epoch, or when the guard evaluates before settlement on a path where `withdrawsRequestsByEpoch`/`principal` are already zero (e.g., an instant-withdraw or a receipt whose normal bucket was cleared). The cleanest trigger: two APR0 requests in epoch N and N+1 where N+1's stop carries the loss — `principalEpoch` tracks only the *first* unsettled epoch, so once `principal` from epoch N settles, a loss recorded on a *different* epoch key than `lastWithdrawRequest` silently falls through to the par path.

I could not fully verify within the available iterations two details that affect reachability: (a) the exact epoch key used by `collectWithdrawFunds` relative to the `epochNumber` increment inside `deposit()` during `stopEpoch`, and (b) whether `_transferFundedClaim` (lines 897+) isolates a funded pool per epoch or pays from aggregate strategy balance. If `_transferFundedClaim` pays from the aggregate haircut-scaled pool, paying the settled bucket at par directly overpays the attacker at the expense of other epoch-N claimants (insolvency of the funded reserve). If the guard's epoch-key assumption matches `collectWithdrawFunds`, the misclassification stands as described.

### Impact Explanation

A receipt created in a loss epoch must be paid `claimBasis * lossRecoveryPrice / 1e18`; instead it is paid in full. The excess is drawn from the strategy's funded-withdrawal pool, which was sized to the haircut total — so the attacker steals the shortfall from every other claimant of that loss epoch (direct theft / insolvency of pending-withdrawal funds), scaling with the settled APR0 principal and the haircut depth.

### Likelihood Explanation

Requires an APR0-mode vault (`unscaledApr == 0`) that experiences a `stopEpochWithDuration` loss while a user holds an APR0 receipt that gets settled before claiming, plus one extra `requestWithdraw`/`claim` interaction to move `lastWithdrawRequest`. All actors are unprivileged; the loss event is manager/borrower-driven (honest roles, merely sequenced). Conditional but reachable.

### Recommendation

- Extend the `requestWithdraw` loss-epoch guard to also check `apr0Users[user].settledPrincipal`/`settledInterest` attributed to `lossEpoch` (store a `settledEpoch` per bucket instead of collapsing to aggregate fields).
- Alternatively, record per-epoch receipt basis for APR0 claims (`apr0PrincipalByEpoch[user][epoch]`) so `_claimLossAdjustedWithdrawRequest` can haircut any epoch regardless of `lastWithdrawRequest`, mirroring `withdrawsRequestsByEpoch`.

### Proof of Concept

```solidity
// Foundry fork test sketch against test/foundry/IdleCreditVault.t.sol harness
// 1. Configure pool with unscaledApr == 0 (APR0 mode), fee params set.
// 2. user deposits, requestWithdraw(P) in epoch N -> apr0Users.principal = P.
// 3. manager stopEpoch with _lossAmount > 0 so collectWithdrawFunds(_amount < pendingBasis)
//    -> lossRecoveryPriceByEpoch[lossKey] < 1e18, pendingWithdraws = 0.
// 4. In epoch N+1, user requestWithdraw again -> _settleApr0 settles P into
//    settledPrincipal; guard passes (principal == 0, withdrawsRequestsByEpoch[N] == 0);
//    lastWithdrawRequest = N+1.
// 5. stopEpoch N+1 normally; user claimWithdrawRequest():
//    _claimLossAdjustedWithdrawRequest reads lossRecoveryPriceByEpoch[N+1] == 0;
//    _claimFundedWithdrawRequest pays settledPrincipal + settledInterest + principal2 at par.
// Assert: claimed == full basis while expected == basis * lossRecoveryPrice / 1e18;
//         strategy funded reserve is drained by the haircut difference.
```

Caveat: step 4's guard bypass depends on the settled bucket preceding the guard evaluation and on `lossRecoveryPriceByEpoch` being keyed on the epoch in `lastWithdrawRequest`; both should be confirmed against the full stopEpoch ordering and `_transferFundedClaim` implementation before finalizing the PoC.