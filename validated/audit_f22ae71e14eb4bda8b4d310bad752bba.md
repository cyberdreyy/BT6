### Title
Loss-adjusted withdraw receipts can be claimed at par by overwriting `lastWithdrawRequest` with a new request - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault._claimLossAdjustedWithdrawRequest` looks up the haircut price using only `lastWithdrawRequest[_user]` as the epoch key, while the actual claim basis is tracked per-epoch in `withdrawsRequestsByEpoch[_user][epoch]`. This mirrors CVE-2025-21904: the code checks/uses the wrong variable (the "latest request" marker instead of the epoch that actually carries `lossRecoveryPriceByEpoch`). A user with a loss-adjusted receipt from epoch N can call `requestWithdraw` again in a later epoch, which overwrites `lastWithdrawRequest[_user]` (line 282). The loss-adjusted lookup then reads `lossRecoveryPriceByEpoch[newEpoch] == 0` and returns 0, so the claim falls through to `_claimFundedWithdrawRequest`, which pays the *aggregate* `withdrawsRequests[_user]` — including the haircutted epoch's basis — at full par.

### Finding Description
In `stopEpochWithDuration` with a realized loss, `collectWithdrawFunds(_amount)` with `_amount < pendingBasis` stores `lossRecoveryPriceByEpoch[epochNumber]` and zeroes `pendingWithdraws`, meaning only the haircutted amount was ever funded by the borrower (lines 411-430). The intended payout for those receipts is `claimBasis * lossRecoveryPrice / RECOVERY_FULL` in `_claimLossAdjustedWithdrawRequest` (lines 789-801).

The bug: that function derives the loss epoch solely from `lastWithdrawRequest[_user]` (line 790). But `requestWithdraw` unconditionally sets `lastWithdrawRequest[_user] = currentEpoch` for every new request (line 282) and adds the new basis to the same aggregate `withdrawsRequests[_user]` (line 292). After a new request in a later epoch:

- `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` is 0 → loss path skipped.
- `_claimFundedWithdrawRequest` passes the `epochNumber > lastWithdrawRequest[_user]` gate one epoch later and transfers `withdrawsRequests[_user]` (the sum of the haircutted basis and the new request) at par via `_transferFundedClaim` (lines 319-349).
- `_clearWithdrawClaimForEpoch` (line 794) is never invoked for the loss epoch, so `withdrawsRequestsByEpoch[_user][lossEpoch]` is never selectively haircutted.

### Impact Explanation
Direct theft / insolvency. Only `pendingToFund = pendingBasis - pendingLoss` underlyings were collected for the loss epoch's receipts, but the attacker withdraws the full `pendingBasis` share at par. The excess is paid from strategy-held underlyings that back other users' funded claims or the default recovery reserve, breaking the "one receipt, one (haircutted) payout" and solvency invariants. Loss magnitude ≈ `attackerBasis * (1 - lossRecoveryPrice)`, bounded only by the attacker's share of pending receipts in the loss epoch.

### Likelihood Explanation
Requires a `stopEpochWithDuration` loss (realized borrower shortfall — a normal protocol event, not an attacker pre-condition), then two ordinary `requestWithdraw` calls by the same unprivileged KYC'd user in different epochs, plus honest manager/owner sequencing (`stopEpoch`, `startEpoch`) which occurs routinely. No privileged misbehavior needed. The guard at line 326 only enforces a one-epoch wait and does not prevent the overwrite.

### Recommendation
Track loss-adjusted claimability per epoch instead of via the single `lastWithdrawRequest` marker: iterate/check `withdrawsRequestsByEpoch` epochs with a non-zero `lossRecoveryPriceByEpoch`, or store per-user a bitmap/list of loss epochs, or block new `requestWithdraw` while the user has an uncleared loss-adjusted receipt (`lossRecoveryPriceByEpoch[lastWithdrawRequest[user]] != 0 && withdrawsRequestsByEpoch[user][lastWithdrawRequest[user]] != 0`).

### Proof of Concept
Foundry fork sketch (USDC-like underlying, `ONE_TRANCHE = 1e18`):

```solidity
// Epoch N: user deposits via cdoEpoch, then requests withdraw of `trancheAmt`
cdoEpoch.requestWithdraw(trancheAmt, address(tranche)); // lastWithdrawRequest[user] = N

// Borrower underfunds: manager stops epoch with loss L so recovery price = 50%
uint256 pending = strategy.pendingWithdraws();
uint256 lossAmount; // chosen so pendingToFund = pending/2
deal(address(underlying), borrower, interest + pending/2);
vm.prank(manager);
cdoEpoch.stopEpochWithDuration(apr, 0, duration, lossAmount);
// strategy.collectWithdrawFunds(pending/2) sets lossRecoveryPriceByEpoch[N] = RECOVERY_FULL/2

// Epoch N+1 buffer: user makes a NEW tiny withdraw request -> overwrites marker
cdoEpoch.requestWithdraw(1, address(tranche)); // lastWithdrawRequest[user] = N+1

// Epoch N+1 runs and ends normally (borrower funds interest + new request)
// ... startEpoch, warp, stopEpoch ...

// Claim: loss path reads lossRecoveryPriceByEpoch[N+1] == 0 and skips;
// funded path pays withdrawsRequests[user] (loss-epoch basis + 1) at par.
uint256 balPre = underlying.balanceOf(user);
cdoEpoch.claimWithdrawRequest();
// Received = lossEpochBasis + 1 instead of lossEpochBasis/2 + 1
// => drains pendingEpochBasis/2 from strategy reserve
assertGt(underlying.balanceOf(user) - balPre, lossEpochBasis / 2 + 1);
```

Note: I could not fully verify whether a cleanup elsewhere (e.g., the CDO-side claim wrapper or a guard forcing claim-before-new-request) already prevents re-requesting with an uncleared loss receipt; `requestWithdraw` itself contains no such check in the code reviewed. The arithmetic path (marker overwrite → par payout of haircutted basis) follows directly from lines 282, 790-792, and 338-349.