### Title
Loss haircut written to wrong epoch index lets pending withdrawers escape `stopEpochWithDuration` losses and drain funded receipts - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.collectWithdrawFunds` stores the pending-receipt recovery price under `lossRecoveryPriceByEpoch[epochNumber]`, but `epochNumber` is incremented inside `deposit()` during the same `stopEpoch`/`stopEpochWithDuration` transaction. The haircut is therefore keyed to the *new* epoch, while every pending requester has `lastWithdrawRequest` pointing at the *old* epoch — a direct analog of an OOB/controlled-index write: the accounting entry lands one slot away from where the claim path reads it. Pending withdrawers then claim at par via `_claimFundedWithdrawRequest` even though the borrower only funded the haircutted amount, socializing the shortfall onto other claimants and making the strategy insolvent.

### Finding Description
The claim path resolves a user's loss-adjusted receipt exclusively through `lastWithdrawRequest[_user]`, which is set to `epochNumber` at request time (line 282) and read in `_claimLossAdjustedWithdrawRequest` (lines 789-794):

```
uint256 lossEpoch = lastWithdrawRequest[_user];
uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
if (lossRecoveryPrice == 0) return amount;
```

During `stopEpochWithDuration`, the CDO first pushes funds into the strategy via `deposit()`, which executes `epochNumber += 1` precisely when `isEpochRunning()` is still true (lines 607-611). `collectWithdrawFunds(_amount)` is then invoked by the CDO and, on partial funding, writes the recovery price under the already-incremented counter (line 421):

```
lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;
```

Every user who requested during the just-ended epoch has `lastWithdrawRequest == oldEpoch`, while the price is stored under `oldEpoch + 1`. When they call `claimWithdrawRequest`:

1. `_claimLossAdjustedWithdrawRequest` reads `lossRecoveryPriceByEpoch[oldEpoch] == 0` and returns 0 without clearing anything.
2. `_claimFundedWithdrawRequest` passes the epoch-wait gate (`epochNumber = oldEpoch+1 > lastWithdrawRequest = oldEpoch`, line 326) and pays the full `withdrawsRequests[_user]` at par via `_transferFundedClaim`.

The same mis-keying corrupts the re-request guard at lines 263-271: a new `requestWithdraw` in the post-loss epoch checks `lossRecoveryPriceByEpoch[oldEpoch]` (zero) and proceeds, and the *new* request's epoch then gets haircutted on claim while the old receipt was already paid unhaircutted.

### Impact Explanation
Direct theft / insolvency. The borrower funds `pendingBasis - pendingLoss`, yet pending requesters collectively withdraw `pendingBasis` at par. The deficit is paid out of strategy-held underlying that belongs to funded receipts and, indirectly, to AA/BB holders — i.e., pending withdrawers escape their pro-rata share of a realized credit loss, which the `previewLossAdjustedWithdrawFunds`/`collectWithdrawFunds` design explicitly assigns to them. For a pool with P pending receipts and loss L, the stolen amount is `P * L / (activeBasis + P)` (the `pendingLoss` term, line 457). No attacker privilege is needed beyond being an ordinary KYC'd lender with a pending withdraw request in the loss epoch — the mispricing is triggered automatically by the epoch-stop flow itself.

### Likelihood Explanation
High if the CDO's stop sequence deposits strategy funds (incrementing `epochNumber`) before calling `collectWithdrawFunds`, which matches the documented comment "deposit done on stopEpoch (before setting the var to false) so we reset the counter" and the single-transaction `stopEpochWithDuration` design in `IdleCDOEpochVariant`. I could not confirm the exact call order inside `IdleCDOEpochVariant.stopEpoch` within this pass — that ordering is the one point to verify. If `collectWithdrawFunds` were invoked strictly before any `deposit()` in the same transaction, the key would be correct and this collapses to a non-issue; the code as written is fragile either way because the epoch key and the request key are derived at different lifecycle phases.

### Recommendation
Key `lossRecoveryPriceByEpoch` by the epoch in which the receipts were created, not the live counter: e.g., store the price under `epochNumber - 1` (or pass the request epoch explicitly from the CDO) inside `collectWithdrawFunds`, and/or snapshot the "pending epoch" when `pendingWithdraws` is accrued. Add a regression test that requests a withdraw, calls `stopEpochWithDuration` with a partial loss, and asserts the user's claim is haircutted and `lastWithdrawRequest` resolves to the written key.

### Proof of Concept
```solidity
// Foundry fork PoC — pool in APR > 0 mode, epoch N running
// 1. Alice (KYC'd lender) deposits and calls requestWithdraw during epoch N.
//    lastWithdrawRequest[alice] = N; withdrawsRequestsByEpoch[alice][N] = amt;
//    pendingWithdraws = amt;
uint256 amt = strategyToken.balanceOf(alice);
vm.prank(alice);
cdoEpoch.requestWithdraw(amt, AAtranche);

// 2. Epoch ends; manager stops with a realized loss partially borne by pending receipts.
//    Inside stopEpochWithDuration the CDO calls strategy.deposit(...) -> epochNumber = N+1,
//    then strategy.collectWithdrawFunds(fundedAmt) with fundedAmt < pendingWithdraws.
(uint256 pendingToFund, ) = strategy.previewLossAdjustedWithdrawFunds(loss);
deal(underlying, borrower, needed);
vm.warp(cdoEpoch.epochEndDate() + 1);
vm.prank(manager);
cdoEpoch.stopEpochWithDuration(apr, loss, duration);

// Mis-keyed write: haircut stored under N+1
assertEq(strategy.lossRecoveryPriceByEpoch(N + 1), pendingToFund * 1e18 / amt);
assertEq(strategy.lossRecoveryPriceByEpoch(N), 0); // where the claim path reads

// 3. Alice claims. _claimLossAdjustedWithdrawRequest reads epoch N -> 0, then
//    _claimFundedWithdrawRequest pays full amt despite only pendingToFund funded.
uint256 balPre = underlying.balanceOf(alice);
vm.prank(alice);
cdoEpoch.claimWithdrawRequest();
assertEq(underlying.balanceOf(alice) - balPre, amt); // escapes haircut; amt > pendingToFund
```