### Title
Loss-adjusted withdraw receipts are paid at par after a second `requestWithdraw` overwrites `lastWithdrawRequest` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The Bluetooth deadlock fix is a concurrency/state-ordering bug: a lock taken in the wrong context lets one path block or corrupt another. The closest analog in this codebase is the ordered claim-state machine in `IdleCreditVault`: `_claimLossAdjustedWithdrawRequest` locates a user's haircutted receipt via `lastWithdrawRequest[_user]`, but `requestWithdraw` blindly overwrites that marker with the newest request epoch. A user who requests a second withdrawal before claiming a loss-adjusted one permanently erases the link to `lossRecoveryPriceByEpoch[lossEpoch]`, so the haircutted receipt is later paid in full at par through `_claimFundedWithdrawRequest`, draining funds that belong to other claimants.

### Finding Description
When `stopEpochWithDuration` realizes a loss that partially hits pending withdraw receipts, `collectWithdrawFunds` funds only `pendingToFund < pendingBasis`, zeroes `pendingWithdraws`, and stores the haircut in `lossRecoveryPriceByEpoch[epochNumber]` (IdleCreditVault.sol:411-430). The haircut is meant to be applied later in `_claimLossAdjustedWithdrawRequest`, which reads the loss epoch from `lastWithdrawRequest[_user]` (lines 789-800).

However, `requestWithdraw` sets `lastWithdrawRequest[_user] = currentEpoch` on every new request (line 282) and only adds to `withdrawsRequests[_user]` / `withdrawsRequestsByEpoch[_user][currentEpoch]` (lines 292-293). If a user with an unclaimed, loss-adjusted receipt from epoch E makes a new request in a later epoch E+1:

- `lastWithdrawRequest[_user]` becomes E+1, so `_claimLossAdjustedWithdrawRequest` looks up `lossRecoveryPriceByEpoch[E+1] == 0` and clears nothing; the epoch-E entry in `withdrawsRequestsByEpoch[_user][E]` survives.
- After the next epoch boundary, `_claimFundedWithdrawRequest` passes the `epochNumber <= lastWithdrawRequest` gate (line 326), computes `normalAmount = withdrawsRequests[_user]` which still includes the epoch-E haircutted basis (line 338), burns the receipt, and pays the full aggregate via `_transferFundedClaim` at par.

The strategy only ever received `pendingToFund` for the epoch-E bucket, so the excess paid out is taken from underlying reserved for other users' funded receipts and default-recovery accounting.

### Impact Explanation
Direct theft / insolvency: the attacker receives `(1 - lossRecoveryPrice) * claimBasis_E` more underlying than funded for epoch E. Since the strategy's underlying balance is the sole backing for all outstanding funded receipts (`_transferFundedClaim`), other withdraw claimants — including the `IdleCDOEpochQueue` on behalf of queued users — are left unpayable or paid short, i.e., a permanent loss up to the attacker's haircut amount. Repeating across epochs compounds the drain.

### Likelihood Explanation
Requires a `stopEpochWithDuration` with `_lossAmount > 0` while the attacker has a pending receipt — an honest manager action, in scope since the attacker only sequences around it. The attacker then performs two ordinary, permitted actions: a second `requestWithdraw` (explicitly supported; comments note unclaimed requests can be re-requested, line 323-324) and a later `claimWithdrawRequest`. No guard recomputes the haircut per epoch at claim time; `withdrawsRequestsByEpoch` is only cleared when the keyed path runs, so nothing stops the par payout.

### Recommendation
In `_claimLossAdjustedWithdrawRequest`, iterate or track all epochs with a nonzero `lossRecoveryPriceByEpoch` for which the user has `withdrawsRequestsByEpoch[_user][epoch] != 0`, rather than keying solely off `lastWithdrawRequest[_user]`. Alternatively, at `requestWithdraw` time, settle/claim any existing loss-adjusted receipt before overwriting `lastWithdrawRequest`, or keep a per-user list of unclaimed loss epochs. A minimal fix: subtract `withdrawsRequestsByEpoch[_user][lossEpoch]` from the par-funded aggregate in `_claimFundedWithdrawRequest` whenever `lossRecoveryPriceByEpoch[lossEpoch] != 0`.

### Proof of Concept
Foundry fork PoC sketch (based on `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
// Setup: standard epoch variant, user holds AA tranches, borrower honest.
// 1. Buffer phase: attacker requests withdraw of T tranches -> request epoch E.
cdoEpoch.requestWithdraw(trancheAmount, address(AAtranche));

// 2. Manager stops epoch E with a realized loss covering part of pendingWithdraws.
//    collectWithdrawFunds funds only pendingToFund and sets
//    lossRecoveryPriceByEpoch[E] = pendingToFund * 1e18 / pendingBasis (e.g. 0.5e18).
cdoEpoch.stopEpochWithDuration(newApr, 0, duration, lossAmount); // borrower funds pendingToFund

// 3. Buffer of epoch E+1: attacker requests a second (small) withdraw.
//    lastWithdrawRequest[attacker] = E+1, erasing the E link.
cdoEpoch.requestWithdraw(1, address(AAtranche));

// 4. Epoch E+1 ends normally; borrower funds new pendingWithdraws.
cdoEpoch.stopEpoch(newApr2, interest);

// 5. Attacker claims. _claimLossAdjustedWithdrawRequest reads epoch E+1 -> price 0 -> skip.
//    _claimFundedWithdrawRequest pays withdrawsRequests[attacker] (incl. epoch-E basis) at par.
uint256 balPre = underlying.balanceOf(attacker);
cdoEpoch.claimWithdrawRequest();
// assert: received == fullBasis_E + request2 > funded amount for E
// assert: strategy underlying balance < sum of remaining funded receipts -> insolvency
```

Uncertainty note: I could not run the test suite, so the exact token amounts depend on how `previewLossAdjustedWithdrawFunds` splits the loss; the state-flow above (marker overwrite at line 282 → keyed lookup at line 790 → aggregate par payout at line 338) is what breaks the "haircutted receipt is paid at haircut" invariant.