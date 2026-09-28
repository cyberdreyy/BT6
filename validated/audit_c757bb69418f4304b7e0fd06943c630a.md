### Title
Loss and APR0 rate keyed to post-increment epoch while receipts are keyed to request epoch, breaking haircut/rate lookups - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
Withdraw receipts are recorded under the epoch in which the request was made (`epochNumber` at `requestWithdraw` time), but the per-epoch loss recovery price and APR0 rate are written under `epochNumber` at `stopEpoch` time. Because `epochNumber` is incremented *inside* `stopEpoch` by `deposit()` (line 608–610: "deposit done on stopEpoch … epochNumber += 1"), any `stopEpoch`/`stopEpochWithDuration` that calls `collectWithdrawFunds` or `prepareStopEpochWithApr0` after that first deposit writes `lossRecoveryPriceByEpoch` / `apr0RateByEpoch` under `epochNumber + 1`, while every affected receipt still points at the pre-increment epoch via `lastWithdrawRequest` / `apr0Users.principalEpoch`. This is the same bug class as M-08: a boundary index computed on the wrong side of a counter, so lookups read an empty slot.

### Finding Description
`requestWithdraw` stamps `lastWithdrawRequest[_user] = currentEpoch` and `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount` (lines 281–293). `_claimLossAdjustedWithdrawRequest` then looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` (lines 790–791), and `_settleApr0` / `_withdrawClaimAmountsForEpoch` look up `apr0RateByEpoch[principalEpoch]` (lines 558, 873).

`collectWithdrawFunds` stores the haircut as `lossRecoveryPriceByEpoch[epochNumber]` (line 421) and `prepareStopEpochWithApr0` stores `apr0RateByEpoch[epochNumber]` (line 537). `deposit()` increments `epochNumber` when invoked while `isEpochRunning()` is still true — i.e., during `stopEpoch` before the flag is cleared (lines 607–610). If the epoch-CDO orders its stopEpoch as (a) deposit borrower repayment → `epochNumber` N becomes N+1, then (b) `collectWithdrawFunds(loss-adjusted amount)` / `prepareStopEpochWithApr0`, the haircut and the rate land under key N+1, but all requests in flight were stamped N.

Consequences:
- `_claimLossAdjustedWithdrawRequest` reads `lossRecoveryPriceByEpoch[N] == 0` and returns 0; the receipt falls through to `_claimFundedWithdrawRequest`, whose gate `epochNumber <= lastWithdrawRequest` now passes (N+1 > N), so the user claims the **full** basis from a strategy that only received the haircuted amount. Early claimants drain the funded cash at par; later claimants revert — insolvency, violating "one receipt one payout".
- `_settleApr0` reads `apr0RateByEpoch[N] == 0`, so APR0 users permanently lose their pro-rata interest even though `pendingWithdraws` was increased by `_apr0NetInterest` (line 535) and funded by the borrower — stranded yield.
- The guard in `requestWithdraw` (lines 263–271) that forces claiming a loss-adjusted receipt before a new request never fires, because it checks `lossRecoveryPriceByEpoch[lastWithdrawRequest]` = key N, which stays zero.

### Impact Explanation
Direct insolvency / unfair payout: loss-adjusted receipts pay out at par until the funded reserve is exhausted, then all remaining receipts of that epoch are permanently unclaimable (revert on insufficient strategy balance). Loss equals `pendingBasis * (1 - lossRecoveryPrice)` plus stranded APR0 interest, paid by later claimants. Additionally `pendingWithdraws` was zeroed (line 420), so accounting shows nothing owed while receipt tokens still exist.

### Likelihood Explanation
Triggers whenever a `stopEpochWithDuration` realizes a loss on an epoch with pending withdrawals, or a normal stopEpoch runs with APR0 principal, **provided** the CDO calls `strategy.deposit` before `collectWithdrawFunds`/`prepareStopEpochWithApr0` inside the same stop flow — which is the natural ordering (repayment arrives, then withdrawal funding is collected). Attacker needs only a normal withdraw request in the affected epoch (any KYC'd lender/tranche holder); faster claimants take the excess.

### Recommendation
Key `lossRecoveryPriceByEpoch` and `apr0RateByEpoch` by the epoch that owns the receipts (e.g., capture `epochNumber` before the incrementing deposit, or store under `epochNumber - 1`/`pendingEpoch`), and have `_claimLossAdjustedWithdrawRequest`/`requestWithdraw` scan the same key. Alternatively increment `epochNumber` only after all per-epoch accounting in stopEpoch is written.

### Proof of Concept
```solidity
// Foundry fork PoC sketch (extend test/foundry/IdleCreditVault.t.sol harness)
// 1. user1 deposits, starts epoch N, calls cdoEpoch.requestWithdraw(amt, AAtranche)
//    => withdrawsRequestsByEpoch[user1][N] = amt; lastWithdrawRequest[user1] = N
// 2. Manager calls stopEpochWithDuration(loss) on IdleCDOEpochVariant; inside it:
//    a) strategy.deposit(repayment) is invoked while isEpochRunning() == true
//       => epochNumber becomes N+1
//    b) strategy.collectWithdrawFunds(fundedAmt < pendingBasis)
//       => lossRecoveryPriceByEpoch[N+1] = price; pendingWithdraws = 0
// 3. user1 calls cdoEpoch.claimWithdrawRequest()
//    _claimLossAdjustedWithdrawRequest: lossRecoveryPriceByEpoch[N] == 0 → returns 0
//    _claimFundedWithdrawRequest: epochNumber(N+1) > lastWithdrawRequest(N) → passes
//    => user1 paid `amt` at par though only `amt*price` was funded
// 4. user2's identical epoch-N receipt now reverts on transfer (insolvency)
//    assertGt(user1Received, fundedAmt * amt / pendingBasis); // overpaid
```
The exact ordering inside `IdleCDOEpochVariant.stopEpoch`/`stopEpochWithDuration` must be confirmed (whether `strategy.deposit` precedes `collectWithdrawFunds`/`prepareStopEpochWithApr0`); I was unable to inspect that call sequence before finalizing. If repayment deposit occurs first, the finding holds as written; if `collectWithdrawFunds` runs strictly before any `deposit()` in the same transaction, the keys coincide and the issue does not manifest.