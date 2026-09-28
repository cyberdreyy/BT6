### Title
Loss-adjusted withdraw receipts escape their haircut when `lastWithdrawRequest` is overwritten by a newer request — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` decides whether a user has a loss-adjusted (partially funded) withdraw receipt by looking up `lossRecoveryPriceByEpoch[lastWithdrawRequest[user]]` in `_claimLossAdjustedWithdrawRequest`. `lastWithdrawRequest` is a single-slot marker overwritten on every `requestWithdraw`. If a user has a receipt in a loss epoch and then submits any new `requestWithdraw` before claiming, the marker moves to the newer epoch, `_claimLossAdjustedWithdrawRequest` no longer sees the loss epoch, and `_claimFundedWithdrawRequest` pays the entire `withdrawsRequests[user]` aggregate — including the haircutted receipt — at par. The analog to "accessing `k` after `kfree_rcu()`" is exact: the cleared/superseded epoch key is what routes the claim, and once the pointer is overwritten the stale aggregate is used.

### Finding Description
In `requestWithdraw` (contracts/strategies/idle/IdleCreditVault.sol:271-295) the strategy:
- mints the user a 1:1 strategy-token receipt,
- adds `_amount` to `withdrawsRequests[user]` and `withdrawsRequestsByEpoch[user][currentEpoch]`,
- sets `lastWithdrawRequest[user] = currentEpoch` unconditionally (line 282), and
- adds `_amount` to `pendingWithdraws` when the pool is not closed.

On a `stopEpochWithDuration(_lossAmount)` the borrower funds only `pendingToFund < pendingBasis`; `collectWithdrawFunds` (lines 411-430) zeroes `pendingWithdraws` and stores `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice` — the haircut all pending receipts of that epoch must take.

At claim time (`claimWithdrawRequest`, lines 301-314):
1. `_claimLossAdjustedWithdrawRequest` (lines 789-801) reads `lossEpoch = lastWithdrawRequest[user]` and only pays the haircutted amount if `lossRecoveryPriceByEpoch[lossEpoch] != 0`.
2. `_claimFundedWithdrawRequest` (lines 319-350) then pays `withdrawsRequests[user]` **in full** and burns the receipt.

The flaw: `requestWithdraw` overwrites `lastWithdrawRequest` but never migrates or preserves the previous request's loss-epoch linkage. `withdrawsRequests[user]` still contains the old loss-epoch amount, while `lastWithdrawRequest` now points to an epoch with no recovery price. `_claimLossAdjustedWithdrawRequest` returns 0, and the funded path pays the haircutted basis at 100% even though the reserve only ever received `pendingToFund`.

A second, simpler variant needs no second epoch: on a closed pool (`epochEndDate == 0`), the `epochNumber <= lastWithdrawRequest` wait check in `_claimFundedWithdrawRequest` (line 326) is skipped entirely, so the aggregate — including a loss-epoch receipt — is immediately claimable at par.

### Impact Explanation
Direct theft / insolvency. The strategy reserve received only `pendingToFund` for the loss epoch, yet the claimant drains `pendingBasis` (the pre-haircut amount). The difference `pendingBasis - pendingToFund` is paid out of underlyings belonging to other users (other pending receipts, instant-withdraw funding, or recovery reserve), breaking the "one receipt, haircut-proportional payout" invariant. The magnitude is bounded only by the user's share of the pending bucket and the loss size; on a large loss the theft approaches the full unfunded remainder.

### Likelihood Explanation
Requirements are all achievable by an unprivileged lender:
- hold tranche tokens, `requestWithdraw` during a buffer/running phase,
- borrower realizes a loss via `stopEpochWithDuration` (a normal protocol path, honest manager action),
- user submits any second `requestWithdraw` in the following epoch (or the pool closes, `epochEndDate == 0`), then calls `cdoEpoch.claimWithdrawRequest()`.

No privileged collusion is needed — the sequence is driven entirely by the attacker around honest manager/borrower calls. The bug is deterministic once the ordering occurs.

### Recommendation
Do not rely on the single mutable `lastWithdrawRequest` to route loss-adjusted claims. Either:
- iterate/check `lossRecoveryPriceByEpoch` against all epochs where `withdrawsRequestsByEpoch[user][e] > 0` (or store the loss-epoch request's haircut directly on the per-epoch bucket at `collectWithdrawFunds` time), or
- revert in `requestWithdraw` when the user has an unclaimed receipt in an epoch with `lossRecoveryPriceByEpoch != 0`, forcing claim first.

Per-epoch clearing (`_clearWithdrawClaimForEpoch`) already exists; extend it so that every claim sweeps all epochs with a non-zero recovery price before the par-funded path runs.

### Proof of Concept
Foundry fork scenario (setup mirrors `test/foundry/IdleCreditVault.t.sol` and `IdleCDOEpochQueue.t.sol`):

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

function testLossReceiptPaidAtParAfterOverwrite() public {
    // epoch N running; user deposits via AA tranche
    uint256 dep = 100_000e6;
    uint256 tr = _depositWithUser(attacker, dep);       // helper: depositAA + requestWithdraw

    vm.prank(manager);
    cdoEpoch.startEpoch();

    cdoEpoch.requestWithdraw(tr, address(AAtranche));   // lastWithdrawRequest[atk] = E, pendingWithdraws += amt

    // stop with a 50% realized loss on pending bucket
    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 pendingBasis = strategy.pendingWithdraws();
    uint256 loss = pendingBasis / 2;                    // 50% haircut on the pending bucket
    (uint256 pendingToFund,) = strategy.previewLossAdjustedWithdrawFunds(loss);
    uint256 repay = cdoEpoch.expectedEpochInterest() + pendingToFund;
    deal(underlying, borrower, repay, true);
    vm.prank(borrower); IERC20(underlying).approve(address(cdoEpoch), repay);
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(apr, 0, duration, loss);
    // lossRecoveryPriceByEpoch[E] = 0.5e5 ; pendingWithdraws = 0 ; reserve += pendingToFund

    // overwrite the marker in the next buffer phase
    vm.prank(manager); cdoEpoch.startEpoch();
    cdoEpoch.requestWithdraw(smallTranche, address(AAtranche)); // lastWithdrawRequest[atk] = E+1

    // wait one epoch so funded claim allowed, then claim
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager); cdoEpoch.stopEpoch(apr, 0);

    uint256 balPre = IERC20(underlying).balanceOf(attacker);
    cdoEpoch.claimWithdrawRequest(); // -> strategy.claimWithdrawRequest(attacker)
    uint256 paid = IERC20(underlying).balanceOf(attacker) - balPre;

    // attacker receives FULL basis for the loss epoch instead of 50%
    assertEq(paid, fullBasisIncludingLossEpoch);        // > pendingToFund + smallRequest
    // theft = (lossEpochBasis - lossEpochBasis * lossRecoveryPrice / RECOVERY_FULL)
}
```

Key assertions: `strategy.lossRecoveryPriceByEpoch(E) != 0`, yet `_claimLossAdjustedWithdrawRequest` reads `lossRecoveryPriceByEpoch[E+1] == 0` and returns 0, so `_claimFundedWithdrawRequest` pays `withdrawsRequests[attacker]` (which still includes the E-epoch amount) at par from underlyings that were never funded.