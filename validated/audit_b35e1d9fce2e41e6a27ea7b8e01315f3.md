### Title
Loss-adjusted withdraw receipts paid at par when a newer request overwrites `lastWithdrawRequest` - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`IdleCreditVault._claimLossAdjustedWithdrawRequest` only inspects the epoch stored in `lastWithdrawRequest[_user]`. If a user has a haircutted (loss-adjusted) receipt from an earlier epoch and then makes a new withdraw request in a later epoch, the loss-epoch lookup returns `lossRecoveryPriceByEpoch[newEpoch] == 0`, so the haircutted receipt falls through to `_claimFundedWithdrawRequest`, which pays the full aggregate `withdrawsRequests[_user]` at par. The user escapes the `stopEpochWithDuration` haircut and drains funded underlyings belonging to other claimants.

### Finding Description
- `requestWithdraw` records per-epoch basis in `withdrawsRequestsByEpoch[_user][currentEpoch]`, aggregates into `withdrawsRequests[_user]`, and unconditionally overwrites `lastWithdrawRequest[_user] = currentEpoch` (contracts/strategies/idle/IdleCreditVault.sol:282-293).
- When the borrower underfunds an epoch, `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch[epoch] < 1e18` for the pending receipts (lines 411-420).
- `claimWithdrawRequest` calls `_claimLossAdjustedWithdrawRequest`, which reads `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` — only the *latest* request epoch (lines 789-801). If that epoch has no loss price, it returns 0 without clearing the older haircutted basis.
- `_claimFundedWithdrawRequest` then pays `withdrawsRequests[_user]` (which still includes the old loss-epoch basis, since `pendingWithdraws` was cleared globally at funding time, not per-epoch) at full value and burns all receipt tokens (lines 338-349).
- This is the analog of the reported bug: a stale bookkeeping entry (the old loss-epoch receipt keyed only by `lastWithdrawRequest`) is not cleaned/separated when a subsequent lifecycle event occurs, so the victim path silently bypasses the penalty applied to that epoch.

### Impact Explanation
Direct theft / insolvency. A user who holds a receipt haircutted to e.g. 50% (`lossRecoveryPrice = 0.5e18`) can claim 100% of its basis simply by making any new withdraw request in a later, fully-funded epoch before claiming. The overpayment `basis * (1 - lossRecoveryPrice)` is paid from funded underlyings reserved for other users' claims or from `defaultRecoveryReserve`-adjacent balances, leaving later claimants unable to withdraw in full — a one-receipt-one-payout and loss-waterfall invariant break.

### Likelihood Explanation
Requires a `stopEpochWithDuration` partial-loss epoch followed by a subsequent normal epoch in which the same user requests a new withdraw — an ordinary sequence for an active lender in a prefunded credit vault. No privileged misbehavior needed; the borrower funding shortfall is an honest-manager scenario. The user only needs to not claim between the two requests, which the code explicitly anticipates ("if a user does not claim... he will have to wait for another epoch to claim both requests", lines 323-324) — meaning the multi-epoch unclaimed-receipt state is a supported flow.

### Recommendation
Track loss-adjusted basis per epoch instead of a single `lastWithdrawRequest` marker: e.g., iterate `lossRecoveryPriceByEpoch` for all epochs with nonzero `withdrawsRequestsByEpoch[_user][epoch]` and a nonzero loss price (store a per-user list or a `lossRequestEpochs` bitmap), or subtract loss-epoch basis from `withdrawsRequests[_user]` at `collectWithdrawFunds` time so it can never reach the par-funded path.

### Proof of Concept
```solidity
// Foundry fork PoC sketch (setup mirrors test/foundry/IdleCreditVault.t.sol helpers)
// Epoch N: user requests withdraw of 100e6
vm.prank(cdo); strategy.requestWithdraw(100e6, user);
// stopEpochWithDuration: borrower funds only 50% -> lossRecoveryPriceByEpoch[N] = 0.5e18
_stopEpochWithLoss(50e6);
// Epoch N+1: user deposits/requests again (allowed; just delays claim)
vm.prank(cdo); strategy.requestWithdraw(10e6, user); // lastWithdrawRequest[user] = N+1
// stopEpoch N+1 fully funded
_stopEpochFullyFunded();
// Claim: _claimLossAdjustedWithdrawRequest sees lossRecoveryPriceByEpoch[N+1] == 0 -> returns 0
// _claimFundedWithdrawRequest pays withdrawsRequests[user] = 110e6 at par
vm.prank(cdo); uint256 paid = strategy.claimWithdrawRequest(user);
assertEq(paid, 110e6);        // BUG: expected 60e6 (50e6 haircutted + 10e6)
// The extra 50e6 comes from underlyings reserved for other users' receipts.
```