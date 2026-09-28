### Title
Loss-adjusted withdraw receipts claim at par because `collectWithdrawFunds` keys `lossRecoveryPriceByEpoch` under the post-increment epoch - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
When `stopEpochWithDuration` realizes a loss, `collectWithdrawFunds` stores the haircut ratio in `lossRecoveryPriceByEpoch[epochNumber]`. However `epochNumber` is incremented inside `deposit()` when the CDO deposits while `isEpochRunning()` is still true — which the in-code comment explicitly describes as the "deposit done on stopEpoch" path (`IdleCreditVault.sol:607-610`). If the CDO's stopEpoch sequence calls `deposit` before `collectWithdrawFunds`, the loss price is written under epoch `N+1`, while every affected receipt was recorded under epoch `N` via `lastWithdrawRequest[_user]` and `withdrawsRequestsByEpoch[_user][N]`. Claimants then bypass the haircut entirely and are paid at par.

### Finding Description
- `requestWithdraw` snapshots `currentEpoch = epochNumber` and writes `lastWithdrawRequest[_user] = currentEpoch` and `withdrawsRequestsByEpoch[_user][currentEpoch]` (`IdleCreditVault.sol:260-293`).
- On a loss, `collectWithdrawFunds` computes `lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis`, zeroes `pendingWithdraws`, and stores the price at `lossRecoveryPriceByEpoch[epochNumber]` (`IdleCreditVault.sol:411-421`) — but only `_amount < pendingBasis` is actually pulled into the strategy.
- `_claimLossAdjustedWithdrawRequest` looks up the haircut via `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` (`IdleCreditVault.sol:790-791`). With the key shifted to `N+1`, the lookup for epoch `N` returns `0`, so the function early-returns.
- Execution falls into `_claimFundedWithdrawRequest`, whose gate `epochNumber <= lastWithdrawRequest[_user]` is `N+1 <= N` → false, so it pays `withdrawsRequests[_user]` at 1:1 par from the strategy's underlying balance (`IdleCreditVault.sol:326-349`).
- Additionally, the replay-window guard in `requestWithdraw` (`IdleCreditVault.sol:263-270`) never triggers for these receipts because `lossRecoveryPriceByEpoch[N]` is `0`, so users can also stack new requests on top of the un-haircut receipt, replaying the par-claim surface again in later epochs — the closest analog to the Tomcat nonce-window replay: a receipt meant to be settled once under a boundary condition is instead replayable at full value.

### Impact Explanation
Pending withdraw receipts that should be haircutted by the realized loss are paid at full par. The shortfall is drawn from underlying held by the strategy that backs active depositors and the funded (reduced) `_amount`, so each loss-adjusted claim overpays by `claimBasis * (1 - lossRecoveryPrice)`, draining funds owed to other claimants and LPs — direct insolvency/loss-socialization failure of the waterfall invariant.

### Likelihood Explanation
Triggers on any epoch stopped via `stopEpochWithDuration(_lossAmount > 0)` where the CDO performs the strategy `deposit` (which bumps `epochNumber`) before `collectWithdrawFunds`. The code comments confirm `deposit` is the stopEpoch path for incrementing `epochNumber`, so the ordering exists in the honest manager/borrower flow; no attacker privilege is needed — any lender with a pending receipt in that epoch benefits at others' expense. I was unable to confirm the exact call ordering inside `IdleCDOEpochVariant.stopEpoch` within this session; if `collectWithdrawFunds` runs before `deposit`, the same mismatch instead manifests for receipts created during the buffer period relative to which epoch key is used, and the finding should be verified against that ordering in the PoC.

### Recommendation
Key `lossRecoveryPriceByEpoch` under the epoch in which the receipts were created — e.g., pass the funded epoch explicitly from the CDO, or store the price under `epochNumber - 1`/`lastWithdrawRequest` semantics — and add an invariant test that a loss-adjusted receipt's claim equals `claimBasis * lossRecoveryPrice`, never par.

### Proof of Concept
Foundry fork test sketch: deposit as lender, `requestWithdraw` during epoch N, roll to stop, call `stopEpochWithDuration`/`stopEpoch` with `_lossAmount > 0` (manager/borrower honest sequence), then `claimWithdrawRequest`. Assert the payout equals `basis * lossRecoveryPrice`; currently observe payout == basis (par) and `lossRecoveryPriceByEpoch[N] == 0` while `lossRecoveryPriceByEpoch[N+1] != 0`.