### Title
Loss-adjusted withdraw receipt escapes its haircut when a later `requestWithdraw` overwrites `lastWithdrawRequest` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
In `IdleCreditVault`, a stop-epoch loss on pending withdrawals is recorded per epoch in `lossRecoveryPriceByEpoch[epochNumber]` and each user's loss-adjusted claim is looked up via `lastWithdrawRequest[_user]` — a single mutable marker that the user controls by submitting a new `requestWithdraw`. Analogous to the Vert.x `StaticHandler` traversal (where an unsanitized wildcard segment is used as a lookup key that escapes the intended scope), a user can make the haircut lookup point at a different epoch, letting a haircutted receipt be paid at par through the funded-claim path.

### Finding Description
When `stopEpochWithDuration` realizes a loss, `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch[epochNumber] = _amount * RECOVERY_FULL / pendingBasis` and clears `pendingWithdraws` (contracts/strategies/idle/IdleCreditVault.sol:411-430). At claim time, `_claimLossAdjustedWithdrawRequest` derives the epoch to check solely from `lastWithdrawRequest[_user]` (line 790) and pays `claimBasis * lossRecoveryPrice / RECOVERY_FULL` (lines 789-800). If no price is found for that epoch, the claim falls through to `_claimFundedWithdrawRequest`, which pays the entire `withdrawsRequests[_user]` aggregate at par (lines 338-349). Since `requestWithdraw` unconditionally sets `lastWithdrawRequest[_user] = currentEpoch` (line 282) and adds to `withdrawsRequests`/`withdrawsRequestsByEpoch` per epoch (lines 292-293), a second request in a later epoch permanently moves the lookup key away from the loss epoch, orphaning the per-epoch haircut while the basis remains claimable in full.

### Impact Explanation
Direct theft / solvency break. Only `claimBasis * lossRecoveryPrice` was actually funded by the borrower into the strategy at `collectWithdrawFunds`; paying the full `claimBasis` draws `(1 - lossRecoveryPrice) * claimBasis` extra underlying from strategy funds backing other receipt holders and active LPs. With e.g. a 20% haircut on a 100k receipt, the user steals 20k of underlying. Loss socialization (the BB-first/pro-rata loss waterfall) is broken for this receipt class.

### Likelihood Explanation
Requires a realized partial stop-epoch loss (`stopEpochWithDuration` with `_lossAmount > 0`, so `lossRecoveryPriceByEpoch[epoch]` is set) plus one extra `requestWithdraw` by the affected user in a later epoch before claiming — both unprivileged user actions. The funded-claim gate `epochNumber <= lastWithdrawRequest[_user]` is satisfied one epoch later, so no privileged cooperation is needed.

### Recommendation
Track per-epoch claim status independently of `lastWithdrawRequest`. In `_claimLossAdjustedWithdrawRequest`, iterate or record the user's loss-adjusted epochs (e.g. a `userLossEpochs` list or a flag on `withdrawsRequestsByEpoch`), or subtract any basis whose epoch has a `lossRecoveryPriceByEpoch` entry from the par-paid `withdrawsRequests` aggregate inside `_claimFundedWithdrawRequest`, so haircutted receipts can never be repaid at par.

### Proof of Concept
Foundry fork outline:

```solidity
// setup: deposit user U, startEpoch, requestWithdraw(amountU) in epoch N
// honest borrower/manager calls:
//   stopEpochWithDuration(_lossAmount > 0) -> epochNumber becomes N+1
//   collectWithdrawFunds(funded = pendingBasis * (1 - lossShare))
//   => lossRecoveryPriceByEpoch[N] = funded * RECOVERY_FULL / pendingBasis (< RECOVERY_FULL)
// attacker (U, unprivileged):
//   epoch N+1 buffer/running: cdo.requestWithdraw(dust) 
//   => lastWithdrawRequest[U] = N+1, withdrawsRequests[U] += dust
// wait one epoch (startEpoch/stopEpoch of N+1 with no loss)
//   epochNumber > lastWithdrawRequest[U] passes the gate
//   cdo.claimWithdrawRequest():
//     _claimLossAdjustedWithdrawRequest: lossRecoveryPriceByEpoch[N+1] == 0 -> skipped
//     _claimFundedWithdrawRequest: pays full amountU + dust at par
// assert underlying received == amountU + dust,
// while only amountU * lossRecoveryPriceByEpoch[N] / RECOVERY_FULL was funded
// => excess (amountU - fundedShare) drained from strategy balance
```

Uncertainty note: I verified the claim-path logic in `IdleCreditVault.sol` but did not fully trace every guard in `IdleCDOEpochVariant.requestWithdraw`/`claimWithdrawRequest` wrappers (e.g. whether a second request in the epoch immediately after the loss epoch is blocked). The core invariant break — haircut keyed by a user-overwritable `lastWithdrawRequest` while the basis is repaid via the par path — follows directly from lines 282, 789-800 and 326-349.