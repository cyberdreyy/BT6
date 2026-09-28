### Title
Loss-adjusted withdraw haircut is keyed to the wrong epoch scope, letting stale pending receipts escape the loss and claim at par - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
Analog mapping: the path traversal bug class (accessing resources outside the intended scope via an unvalidated lookup key) maps to `IdleCreditVault`'s per-epoch recovery accounting. `collectWithdrawFunds` records the loss haircut under `lossRecoveryPriceByEpoch[epochNumber]` — the epoch at stop time — but claims look the haircut up via `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]`, the user's own request epoch. Any pending receipt whose `lastWithdrawRequest` differs from the stop epoch resolves a zero price, takes no haircut, and is paid at par from an underfunded pool.

### Finding Description
In `requestWithdraw` (IdleCreditVault.sol:281-294) each user's request epoch is saved in `lastWithdrawRequest[_user]` and their basis per epoch in `withdrawsRequestsByEpoch[_user][currentEpoch]`. The code and its own comments confirm pending receipts can span multiple epochs ("if a user does not claim a withdraw request and instead requests another withdraw"). When the borrower funds fewer underlyings than `pendingWithdraws`, `collectWithdrawFunds` (lines 411-430) computes a single `lossRecoveryPrice = _amount * 1e18 / pendingBasis` covering the aggregate of all pending receipts, then stores it under only one key: `lossRecoveryPriceByEpoch[epochNumber]`, and zeroes `pendingWithdraws`.

On claim, `_claimLossAdjustedWithdrawRequest` (lines 789-801) reads `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]`. Only users whose last request epoch equals the stored key take the haircut. Any user holding a pending receipt from a different epoch — e.g. a lender who requested in epoch N, never claimed, and never requested again — reads price 0, falls through to `_claimFundedWithdrawRequest` (lines 319-350), and withdraws `withdrawsRequests[_user]` at full par via `_transferFundedClaim`. The epoch-scoping guard in `requestWithdraw` (lines 262-271) only blocks making a *new* request when the user's own epoch has a nonzero price; it does not normalize which key the haircut was stored under, nor does it aggregate all pending receipts under one epoch at funding time.

This reproduces the traversal shape: the payout lookup uses an attacker-influenceable key (the request epoch) to escape the scope (the loss-adjusted epoch bucket) the funds were actually sourced for.

### Impact Explanation
Direct theft / insolvency: the strategy only receives `_amount` underlyings for the whole pending bucket, yet receipts outside the keyed epoch claim at par. Haircutted-epoch claimants are left underfunded (last claimant reverts or gets dust), or the deficit is silently absorbed by `defaultRecoveryReserve` or residual liquidity that belongs to other claimants. Loss is bounded by `pendingBasis - _amount` allocated to non-matching epochs — e.g. 60% of `pendingWithdraws` sitting in stale epochs escapes a 30% haircut and drains ~18% of the funded pool from rightful claimants.

### Likelihood Explanation
Requires only ordinary user behavior: request a withdraw, don't claim, let a later `stopEpochWithDuration(_lossAmount)` (borrower is honest; loss is a legitimate partial repayment) record the haircut under a different epoch. The attacker (any KYC'd lender) simply fails to claim in the interim. No privileged cooperation needed. The only precondition is multi-epoch pending basis, which the code explicitly supports.

Uncertainty not fully verified: the exact ordering of `epochNumber` increment in `deposit()` (lines 607-610) relative to `collectWithdrawFunds` inside `stopEpoch` in `IdleCDOEpochVariant` — if the increment happens before funding, even current-epoch requesters may escape the haircut; either ordering produces a mismatch for some claimants.

### Recommendation
Store and consume the loss haircut under a scope covering the aggregate pending bucket rather than a single epoch key: e.g. record `lossRecoveryPriceByEpoch` for every epoch present in the pending basis, or track a global `pendingLossPrice` consumed at claim regardless of `lastWithdrawRequest`, or normalize all pending receipts into `epochNumber` basis entries inside `collectWithdrawFunds` before applying the haircut. Alternatively require claims of all stale receipts before a loss-adjusted `stopEpoch` can close the bucket.

### Proof of Concept
Foundry fork PoC outline:

```solidity
// Epoch N: lender A calls requestWithdraw(100e6) via CDO -> lastWithdrawRequest[A] = N,
//          withdrawsRequestsByEpoch[A][N] = 100e6, pendingWithdraws = 100e6
// Epoch N ends; A does not claim.
// Epoch N+1: lender B calls requestWithdraw(400e6) -> lastWithdrawRequest[B] = N+1,
//          pendingWithdraws = 500e6
// stopEpochWithDuration(loss): borrower funds _amount = 350e6 (< 500e6)
//   -> lossRecoveryPriceByEpoch[epochNumber_at_stop] = 0.7e18 (only one key written)
// Assert: lossRecoveryPriceByEpoch[N] == 0
// vm.prank(cdo): vault.claimWithdrawRequest(A)
//   -> _claimLossAdjustedWithdrawRequest reads price 0 -> returns 0
//   -> _claimFundedWithdrawRequest pays A 100e6 at par (no haircut)
// Remaining funded pool = 250e6 for B's claimBasis 400e6 * 0.7 = 280e6 owed
// Assert: B's claim underflows/reverts or is short ~30e6+ -> insolvency / theft of A's unpaid haircut
```