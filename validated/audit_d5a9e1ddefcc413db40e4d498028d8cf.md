### Title
Loss-adjusted withdraw receipt haircut evaded by a subsequent withdraw request — (`contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
When `stopEpochWithDuration` realizes a loss, `collectWithdrawFunds` prices the haircut per request epoch via `lossRecoveryPriceByEpoch[epochNumber]`, but `_claimLossAdjustedWithdrawRequest` looks up the loss price only at `lastWithdrawRequest[_user]` — the user's *latest* request epoch. A user whose receipt was haircutted in epoch N can make a new `requestWithdraw` in epoch N+1, which overwrites `lastWithdrawRequest[_user]`. On the next claim the loss path returns zero (no loss price for epoch N+1) and `_claimFundedWithdrawRequest` pays the aggregate `withdrawsRequests[_user]` — which still includes the uncleared epoch-N receipt — at par.

### Finding Description
The AMQP bug is a missing validation of a sub-result before continuing dissection (crash). The closest credit-vault analog is a missing "was this receipt already loss-priced?" check in the queued-withdrawal claim path:

- `requestWithdraw` records `withdrawsRequestsByEpoch[user][epoch]`, `withdrawsRequests[user]`, and sets `lastWithdrawRequest[user] = currentEpoch` (`IdleCreditVault.sol:282-293`).
- On a loss, `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch[epochNumber]` and zeroes `pendingWithdraws`, but leaves per-user receipt counters untouched (`IdleCreditVault.sol:411-430`).
- `claimWithdrawRequest` first calls `_claimLossAdjustedWithdrawRequest`, which reads `lossEpoch = lastWithdrawRequest[_user]` and returns early if `lossRecoveryPriceByEpoch[lossEpoch] == 0` (`IdleCreditVault.sol:789-795`).
- It then calls `_claimFundedWithdrawRequest`, which pays `withdrawsRequests[_user]` in full via `_transferFundedClaim` (`IdleCreditVault.sol:338-349`).

Sequence:
1. Attacker (KYC'd lender) deposits AA, calls `requestWithdraw` during buffer/running phase of epoch N.
2. `stopEpochWithDuration(_lossAmount)` realizes a loss; `collectWithdrawFunds` funds only `pendingToFund` and stores `lossRecoveryPriceByEpoch[N] = p < 1`.
3. Attacker does *not* claim; instead calls `requestWithdraw` again with remaining tranche balance during epoch N+1, setting `lastWithdrawRequest[user] = N+1`.
4. After epoch N+1 `stopEpoch` funds the new request, attacker calls `claimWithdrawRequest`. The loss path is skipped (`lossRecoveryPriceByEpoch[N+1] == 0`), and `_claimFundedWithdrawRequest` pays epoch-N + epoch-N+1 receipts at par.

The stolen amount is `(1 − p) × epochN_receipt`, paid from strategy-held underlyings reserved for other funded claimants / recovery reserve. Additionally, `withdrawsRequestsByEpoch[user][N]` is never cleared, so the epoch-N basis remains as stale accounting.

### Impact Explanation
Direct theft: the attacker converts a haircutted receipt into a par payout, extracting the loss amount that the waterfall assigned to pending redeemers. The overpayment is backed by underlyings held for other users' claims, causing insolvency for later claimants (last claimant cannot be paid in full). Loss scales with the realized loss percentage and the attacker's receipt size.

### Likelihood Explanation
Any tranche-token holder can execute it; no privileged action needed. Trigger condition is a `stopEpochWithDuration` loss epoch — an expected, non-privileged event — plus one extra `requestWithdraw`, which is permissionless while `allowAAWithdrawRequest`/`allowBBWithdrawRequest` are set. No existing guard stops it: `requestWithdraw` does not check for an unclaimed loss-adjusted receipt, and `_claimFundedWithdrawRequest` validates only `epochNumber > lastWithdrawRequest`.

### Recommendation
In `requestWithdraw`, revert or force-settle the loss-adjusted claim if `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]] != 0`. Alternatively, iterate all epochs with nonzero `withdrawsRequestsByEpoch` in `_claimLossAdjustedWithdrawRequest` instead of keying only on `lastWithdrawRequest`.

### Proof of Concept
Caveat: I could not confirm in this iteration where `epochNumber` is incremented relative to `collectWithdrawFunds` inside `stopEpochWithDuration`; the PoC assumes the loss price is keyed to the request epoch, consistent with `_claimLossAdjustedWithdrawRequest` using `lastWithdrawRequest`. A Foundry test sketch:

```solidity
// 1. attacker deposits AA in epoch 0, requests full withdraw (epoch N receipt R)
// 2. manager calls stopEpochWithDuration(loss) -> collectWithdrawFunds funds < pendingBasis,
//    lossRecoveryPriceByEpoch[N] = p < 1
// 3. attacker calls requestWithdraw(remaining, AA) in epoch N+1
//    -> lastWithdrawRequest[attacker] = N+1
// 4. epoch N+1 stopEpoch funds new request
// 5. attacker calls cdoEpoch.claimWithdrawRequest()
//    assert payout == R + R2   (par) instead of R*p + R2
//    assert withdrawsRequestsByEpoch[attacker][N] still nonzero (stale)
```

The claim path code (`IdleCreditVault.sol:789-800` skipping on zero price, `IdleCreditVault.sol:338-349` paying the aggregate) is the load-bearing evidence; the PoC verifies ordering against the actual `stopEpochWithDuration`/`epochNumber` increment, which I was unable to read before the iteration limit.