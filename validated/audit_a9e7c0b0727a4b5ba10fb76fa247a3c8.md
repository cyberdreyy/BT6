### Title
A second `requestWithdraw` overwrites `lastWithdrawRequest`, causing a prior loss-adjusted epoch receipt to be claimed at par - (contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.requestWithdraw` records each withdraw receipt under both an aggregate (`withdrawsRequests[_user]`) and a per-epoch bucket (`withdrawsRequestsByEpoch[_user][epoch]`), but only tracks the *latest* epoch in the single-slot `lastWithdrawRequest[_user]` marker. When `stopEpochWithDuration` applies a loss, the haircut for pending receipts is stored in `lossRecoveryPriceByEpoch[epoch]` and can only be reached through `_claimLossAdjustedWithdrawRequest`, which looks up `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]`. If the user files a new `requestWithdraw` in a later epoch before claiming, the earlier epoch's loss-adjusted entry is orphaned and its basis is instead paid at par through `_claimFundedWithdrawRequest` — the exact analog of the VoteEscrowDelegation bug where mutating the "current" record retroactively corrupts/duplicates the accounting of the previous checkpoint. Here, updating the latest-epoch pointer destroys the previous epoch's haircut record, breaking the loss waterfall.

### Finding Description
In `requestWithdraw` (`IdleCreditVault.sol` ~line 282) the code unconditionally sets `lastWithdrawRequest[_user] = currentEpoch` and adds `_amount` to `withdrawsRequests[_user]` and `withdrawsRequestsByEpoch[_user][currentEpoch]` (lines 292–293).

When a stop realizes a loss, `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch[epochNumber] = _amount * 1e18 / pendingBasis` and zeroes `pendingWithdraws` (lines 411–421), so the strategy only ever received the haircutted amount.

On claim, `claimWithdrawRequest` first calls `_claimLossAdjustedWithdrawRequest` (line 312), which uses `lossEpoch = lastWithdrawRequest[_user]` (line 790). If the user made any later request, `lastWithdrawRequest` points to the newer epoch, `lossRecoveryPriceByEpoch[newEpoch] == 0`, and the old epoch's claimBasis is never haircutted. Execution falls through to `_claimFundedWithdrawRequest`, which pays `withdrawsRequests[_user]` — the *aggregate* including the old loss-adjusted basis — at par via `_transferFundedClaim` (lines 338–349), even though the borrower only funded `basis * lossRecoveryPrice`.

Analogous to the report's `Checkpoint storage` bug: the new write (new request epoch) reaches back and silently alters how the previous record (the haircutted epoch) is settled.

### Impact Explanation
The attacker recovers the haircut portion of their earlier request that the protocol deliberately socialized as a loss. The strategy is only funded with `basis * lossRecoveryPrice` for that epoch, so paying the full basis either (a) steals underlying funded for other pending receipts/instant claims held in the vault, or (b) makes later legitimate claims revert for lack of balance — insolvency/permanent freezing for other users. Quantified loss equals `basis * (1e18 - lossRecoveryPrice)` per affected epoch; e.g., with a 50% `lossRecoveryPrice` and a 100k prior request, the user extracts 50k of other claimants' funded reserves.

### Likelihood Explanation
Requires only an unprivileged tranche holder: (1) `requestWithdraw` during a running epoch, (2) a `stopEpochWithDuration` partial-loss stop (honest manager action, in scope), (3) a second `requestWithdraw` in a subsequent epoch, (4) `claimWithdrawRequest` after that epoch stops. No privileged collusion, no reentrancy, and none of the existing guards (`_onlyIdleCDO`, epoch gating, `lossRecoveryPrice == 0` check) prevent it — the `== 0` check is exactly what lets the orphaned epoch slip through to the par path.

### Recommendation
Track loss-adjusted claims per epoch rather than via the single `lastWithdrawRequest` slot — e.g., iterate/store the set of epochs with entries in `withdrawsRequestsByEpoch`, or apply `lossRecoveryPriceByEpoch` when clearing each epoch's basis in `_clearWithdrawClaimForEpoch` instead of paying the aggregate at par. Alternatively, refuse a new `requestWithdraw` while an unclaimed loss-adjusted receipt exists (`lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]] != 0` and the epoch basis nonzero), mirroring the fix of using `memory`/separate records so the prior record's accounting is not retroactively altered.

### Proof of Concept
```solidity
// Foundry fork test sketch (test/foundry/IdleCreditVault exploit)
// Setup: standard IdleCDOEpochVariant + IdleCreditVault, KYC'd user.

// 1) Epoch 1 running: user deposits, then requests withdraw of X tranches.
vm.prank(user);
cdoEpoch.requestWithdraw(x, address(tranche)); // strategy.requestWithdraw
assertEq(strategy.lastWithdrawRequest(user), 1);
assertEq(strategy.withdrawsRequestsByEpoch(user, 1), X);

// 2) Manager stops epoch 1 with a loss: borrower funds only 50% of pendingWithdraws.
//    collectWithdrawFunds(0.5*X) -> lossRecoveryPriceByEpoch[1] = 0.5e18
vm.prank(manager);
cdoEpoch.stopEpochWithDuration(newApr, interest, duration, lossAmt);

// 3) Epoch 2 running: user files a second request of Y. lastWithdrawRequest is
//    overwritten to 2 BEFORE the epoch-1 receipt is claimed.
vm.prank(user);
cdoEpoch.requestWithdraw(y, address(tranche));
assertEq(strategy.lastWithdrawRequest(user), 2); // epoch-1 haircut now unreachable

// 4) Manager stops epoch 2 normally (fully funded).
vm.prank(manager);
cdoEpoch.stopEpoch(newApr, interest);

// 5) Claim. _claimLossAdjustedWithdrawRequest checks lossRecoveryPriceByEpoch[2] == 0
//    and skips; _claimFundedWithdrawRequest pays withdrawsRequests[user] = X + Y at par,
//    while the vault only received 0.5*X + Y from the borrower.
uint256 balPre = underlying.balanceOf(user);
vm.prank(user);
cdoEpoch.claimWithdrawRequest();
assertEq(underlying.balanceOf(user) - balPre, X + Y); // overpaid by 0.5*X
// The 0.5*X shortfall is taken from underlying held for other funded claims,
// or subsequent claimants' _transferFundedClaim calls revert (frozen funds).
```

Uncertainty note: the exact epoch index written into `lossRecoveryPriceByEpoch` depends on when `epochNumber` increments relative to `collectWithdrawFunds` inside `stopEpoch`; regardless of the index, the overwritten `lastWithdrawRequest` marker still orphans the haircutted epoch, which is the core of the bug.