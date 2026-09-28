### Title
Stale `lastWithdrawRequest` epoch marker lets a loss-adjusted receipt bypass its haircut and claim at par — ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
Analogous to the forkchoice bug — where `currentSlot` silently rotates while the balance stays in the old accumulator, so a later subtraction hits the wrong bucket — `IdleCreditVault` tracks a user's loss-adjusted claim solely via the single-slot marker `lastWithdrawRequest[_user]`. When a user holds a receipt from an epoch that was funded at a loss (`lossRecoveryPriceByEpoch[E1] > 0`) and then submits a new `requestWithdraw` in a later epoch `E2`, the marker rotates to `E2` while the haircut basis remains recorded under `withdrawsRequestsByEpoch[_user][E1]`. The loss-adjusted claim path keys off `lastWithdrawRequest`, so it no longer finds `E1`'s recovery price, and `_claimFundedWithdrawRequest` then pays the aggregate `withdrawsRequests[_user]` — which still includes `E1`'s basis — at par.

### Finding Description
In `requestWithdraw` (`IdleCreditVault.sol:281-294`), each request overwrites `lastWithdrawRequest[_user] = currentEpoch` and adds the amount to both `withdrawsRequests[_user]` and `withdrawsRequestsByEpoch[_user][currentEpoch]`. When `stopEpochWithDuration` funds pending receipts at a loss, `collectWithdrawFunds` (`IdleCreditVault.sol:411-430`) zeroes `pendingWithdraws` and stores `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice`, but per-user basis is left in `withdrawsRequestsByEpoch`/`withdrawsRequests` for later clearing.

At claim time (`IdleCreditVault.sol:301-314`), `_claimLossAdjustedWithdrawRequest` (`:789-801`) computes `lossEpoch = lastWithdrawRequest[_user]` and looks up `lossRecoveryPriceByEpoch[lossEpoch]`. Sequence:

1. Epoch E1 buffer: attacker calls `requestWithdraw(amount)` → `lastWithdrawRequest = E1`, `withdrawsRequestsByEpoch[attacker][E1] += amount`.
2. Epoch E1 stops via `stopEpochWithDuration` with partial borrower funding → `lossRecoveryPriceByEpoch[E1] = p < RECOVERY_FULL`; `pendingWithdraws` cleared.
3. Epoch E2 buffer: attacker calls `requestWithdraw(amount2)` → marker rotates to `lastWithdrawRequest = E2`; `withdrawsRequests[attacker]` now aggregates E1+E2 basis.
4. Epoch E2 stops fully funded; `epochNumber > E2`, so `_claimFundedWithdrawRequest`'s gate (`:326`) passes.
5. `claimWithdrawRequest` runs `_claimLossAdjustedWithdrawRequest`: `lossRecoveryPriceByEpoch[E2] == 0` → returns 0, leaving the E1 haircut basis uncleared. `_claimFundedWithdrawRequest` (`:338-349`) then pays `normalAmount = withdrawsRequests[attacker]` — the full E1 basis included — at par via `_transferFundedClaim`.

The trigger condition for the loss path (nonzero `lossRecoveryPriceByEpoch[lastWithdrawRequest]`) does not fire after marker rotation, exactly mirroring the upstream bug where the unchanged root/payload-status check lets `currentSlot` rotate and the subtraction then targets the wrong accumulator.

### Impact Explanation
Direct theft from the funded-claim reserve: the attacker recovers the `1 - p` haircut portion of their E1 receipt that was supposed to be socialized as a loss. Loss magnitude is `basis_E1 * (RECOVERY_FULL - lossRecoveryPriceByEpoch[E1]) / RECOVERY_FULL` per request; since the same E1 basis is also still claimable bookkeeping-wise, every LP's subsequent funded claim draws on a reserve short by that amount — an insolvency/fair-redemption violation of the "loss adjusted means haircut" invariant.

### Likelihood Explanation
Requires only an unprivileged tranche holder: request a withdraw, wait for a loss-adjusted `stopEpochWithDuration` (manager/borrower are honest actors in the sequence), then re-request in the next buffer and claim after it ends. One caveat I could not fully verify within available context: whether `IdleCDOEpochVariant.requestWithdraw` additionally reverts when the caller has an unclaimed receipt whose epoch has a nonzero `lossRecoveryPriceByEpoch` (a `NotAllowed` guard is confirmed for the post-default path per `IdleCreditVault.t.sol:4644-4646`; an equivalent guard for the loss-adjusted path would mitigate this). If no such guard exists, the path is fully reproducible on a Foundry fork by following the test scaffolding in `test/foundry/IdleCDOEpochQueue.t.sol:811-870` and `IdleCreditVault.t.sol:3331-3450`.

### Recommendation
Track loss-adjusted claims per epoch rather than via the single `lastWithdrawRequest` marker: e.g., iterate/lookup `lossRecoveryPriceByEpoch` for each epoch present in `withdrawsRequestsByEpoch[_user]`, or record a `lossEpochs` list per user, so rotating `lastWithdrawRequest` cannot orphan an uncleared haircut. Alternatively, revert `requestWithdraw` when the user has any unclaimed receipt in an epoch with a nonzero `lossRecoveryPriceByEpoch`, mirroring the post-default open-receipt guard.

### Proof of Concept
```solidity
// Foundry fork PoC sketch (pattern follows test/foundry/IdleCDOEpochQueue.t.sol)
// 1) buffer of epoch E1: attacker requests withdraw
_requestWithdrawWithUser(attacker, trancheAmt);   // lastWithdrawRequest[attacker] = E1

// 2) manager stops E1 with a loss; borrower funds only pendingToFund < pendingBasis
cdoEpoch.stopEpochWithDuration(apr, 0, duration, lossAmount);
//    -> collectWithdrawFunds sets lossRecoveryPriceByEpoch[E1] = p < 1e18, pendingWithdraws = 0

// 3) manager starts E2; attacker requests again WITHOUT claiming E1 receipt
cdoEpoch.startEpoch();
_requestWithdrawWithUser(attacker, trancheAmt2);  // marker rotates: lastWithdrawRequest = E2

// 4) E2 ends fully funded
cdoEpoch.stopEpoch(apr, interest);                // epochNumber > E2

// 5) attacker claims once
cdoEpoch.claimWithdrawRequest();
//    _claimLossAdjustedWithdrawRequest: lossRecoveryPriceByEpoch[E2] == 0 -> pays 0
//    _claimFundedWithdrawRequest: pays withdrawsRequests[attacker] (E1+E2) at PAR
//    assert: attacker received E1 basis un-haircut; vault reserve short by basis*(1-p)
```