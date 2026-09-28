### Title
`lossRecoveryPriceByEpoch` keyed by post-increment `epochNumber` lets haircutted withdraw receipts claim at par — ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`collectWithdrawFunds` stores the loss-recovery price under `epochNumber`, but `epochNumber` is incremented inside `deposit()` when an epoch is running. If `collectWithdrawFunds` executes after the epoch counter bumps (the normal stopEpoch flow), the haircut price lands on the *next* epoch index, so `lastWithdrawRequest[user]` (stored pre-increment) reads an empty slot — an index-mismatch read analogous to CVE-2017-7611's unchecked index returning attacker-favorable data. The loss-adjusted claim path sees `lossRecoveryPrice == 0` and falls through to the funded-claim path, paying the full pre-loss basis.

### Finding Description
- In `deposit()` the strategy increments `epochNumber` whenever `IIdleCDOEpochVariant(idleCDO).isEpochRunning()` is true (line 607–614).
- `collectWithdrawFunds(_amount)` computes `lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis` and writes `lossRecoveryPriceByEpoch[epochNumber]` (line 417–421). This runs during `stopEpochWithDuration`, after `deposit()` has already bumped `epochNumber`.
- `requestWithdraw` stores the user's request epoch in `lastWithdrawRequest[_user]` under the *pre-increment* epoch (line 282).
- On claim, `_claimLossAdjustedWithdrawRequest` reads `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` — the old index — finds `0`, and returns early (lines 790–792).
- Execution then reaches `_claimFundedWithdrawRequest`, whose only guard is `epochNumber <= lastWithdrawRequest[_user]` (line 326). Since `epochNumber` was incremented, the check passes and the user claims the *full* `withdrawsRequestsByEpoch[user][oldEpoch]` basis at par, even though the borrower only funded the haircutted `_amount`.

### Impact Explanation
Every holder of a pending withdraw receipt in a loss epoch is paid their full un-haircut basis, while the strategy only received `pendingBasis - lossShare` in underlying. The shortfall is paid out of strategy-held reserves meant for other claims / recovery, breaking the solvency and "one receipt, one loss-adjusted payout" invariants. Quantified loss equals `pendingBasis * pendingLossShare / totalBasis` — i.e., the entire haircut the waterfall intended to socialize, stolen by first claimers; later claimers (funded receipts, instant receipts, recovery reserve) are left undercollateralized or with nothing.

### Likelihood Explanation
Requires only an unprivileged tranche holder with a pending withdraw request and a `stopEpochWithDuration` call that realizes a loss — both within normal protocol operation; no privileged misbehavior needed. Severity hinges on call ordering in `IdleCDOEpochVariant.stopEpochWithDuration`: if `collectWithdrawFunds` is invoked *before* the `deposit()` that bumps `epochNumber`, the epoch key is correct and the bug does not trigger. I was unable to fully read `IdleCDOEpochVariant` within the available iterations, so that ordering should be verified before reporting. If ordering is safe, the analogous risk still exists for any future path that stores loss prices after epoch rollover.

### Recommendation
Key `lossRecoveryPriceByEpoch` by the epoch in which the receipts were created, not the current counter — e.g., pass the settled epoch explicitly from the CDO, or store `lossRecoveryPriceByEpoch[epochNumber - 1]` (with an explicit check that the pending receipts' epoch matches). Alternatively, record a `lossEpochByUser`/global `pendingWithdrawEpoch` so `requestWithdraw`, `collectWithdrawFunds`, and the claim paths cannot disagree on the index.

### Proof of Concept
```solidity
// Foundry fork PoC (concept; ordering of stopEpochWithDuration must be confirmed)
// 1. user deposits AA tranches, epoch running.
// 2. user calls cdoEpoch.requestWithdraw(...) -> lastWithdrawRequest[user] = E.
// 3. manager calls stopEpochWithDuration(apr, 0, duration, lossAmount):
//      - deposit() runs with isEpochRunning()==true -> epochNumber becomes E+1
//      - collectWithdrawFunds(funded < pendingBasis) writes
//        lossRecoveryPriceByEpoch[E+1] = funded*1e18/pendingBasis
// 4. user calls claimWithdrawRequest():
//      - _claimLossAdjustedWithdrawRequest reads
//        lossRecoveryPriceByEpoch[E] == 0 -> returns 0
//      - _claimFundedWithdrawRequest: epochNumber(E+1) > lastWithdrawRequest(E)
//        -> pays full withdrawsRequestsByEpoch[user][E] at par
// assert: user received claimBasis, strategy balance short by
//         claimBasis - claimBasis*funded/pendingBasis
```