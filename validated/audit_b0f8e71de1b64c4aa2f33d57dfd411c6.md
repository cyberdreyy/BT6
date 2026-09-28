### Title
Loss-adjusted withdraw receipt escapes its haircut when a second request overwrites `lastWithdrawRequest` - (contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault._claimLossAdjustedWithdrawRequest` looks up the loss-adjusted epoch exclusively via `lastWithdrawRequest[_user]`. A user whose epoch-`E` receipt was haircut by `stopEpochWithDuration(_lossAmount)` can simply submit a second withdraw request in a later epoch `E2`, overwriting `lastWithdrawRequest[_user]` to `E2`. Because `lossRecoveryPriceByEpoch[E2] == 0`, the loss-adjusted claim path is skipped and the epoch-`E` basis remains inside the aggregate `withdrawsRequests[_user]`, which `_claimFundedWithdrawRequest` then pays out **at par**. The user redeems the defaulted/lossy receipt in full, draining funded underlying that belongs to other receipt holders — a direct, quantifiable theft matching the "stale/out-of-range index read" bug class of the OOB-read report.

### Finding Description
- `requestWithdraw` records the request epoch unconditionally: `lastWithdrawRequest[_user] = currentEpoch` and `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount` (lines 282–293). There is no merge with a prior unclaimed loss-adjusted receipt.
- On a partial stop loss, `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice` and zeroes `pendingWithdraws`, but per-user `withdrawsRequests`/`withdrawsRequestsByEpoch` are intentionally left for lazy clearing (lines 411–421).
- `_claimLossAdjustedWithdrawRequest` derives the epoch to clear from `lastWithdrawRequest[_user]` only (line 790). Any older loss epoch becomes unreachable once `lastWithdrawRequest` is overwritten.
- `_claimFundedWithdrawRequest` gates only on `epochNumber <= lastWithdrawRequest[_user]` (line 326) and pays `withdrawsRequests[_user]` in full via `_transferFundedClaim` (lines 338–349). The aggregate still contains the uncleared epoch-`E` basis, so it is paid without the `lossRecoveryPrice` haircut.
- `_clearWithdrawClaimForEpoch` would have removed `withdrawsRequestsByEpoch[_user][E]` and decremented the aggregate, but it is only invoked for `lossEpoch = lastWithdrawRequest[_user]` (line 794), i.e. `E2`, never `E`.

### Impact Explanation
The attacker receives `receipt_E * (1 - lossRecoveryPrice)` more than entitled, paid from the strategy's funded-withdraw underlying pool. Since loss-adjusted funding is exact (`pendingBasis - pendingLoss` collected), the excess comes directly out of other users' funded claims or subsequent honest withdrawals, breaking the "one receipt one payout" and loss-socialization invariants. The same stale-key read also permanently bricks the orphaned `withdrawsRequestsByEpoch[_user][E]` entry, but the dominant impact is theft of the haircut delta, which can approach 100% of the receipt when `lossRecoveryPrice` is small.

### Likelihood Explanation
Requires only a normal (non-privileged) sequence: a withdrawal request in an epoch that ends with `_lossAmount > 0` (manager's honest `stopEpochWithDuration`), then a second `requestWithdraw` in a later epoch — both fully permissionless actions by any KYC-passing tranche holder. No privileged misbehavior is needed; `allowAAWithdrawRequest`/`allowBBWithdrawRequest` are re-enabled after each normal stop. The entire exploit is two user calls plus a claim.

### Recommendation
Resolve all epochs bearing a nonzero `lossRecoveryPriceByEpoch` for the user inside `claimWithdrawRequest` (iterate or track a per-user set of loss epochs), or clear the pending loss-adjusted receipt before accepting a new request in `requestWithdraw` — e.g. revert/`_settleApr0`-style settlement when `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]] != 0`. Alternatively, store the loss epoch per receipt so `lastWithdrawRequest` cannot alias it.

### Proof of Concept
```solidity
// Foundry fork PoC (schematic against deployed vault/queue):
// Setup: user holds tranche tokens; pool in buffer or running epoch.
// 1) Request withdraw of A tranche tokens in epoch E.
vault.requestWithdraw(A, user);           // via cdoEpoch.requestWithdraw
// 2) Owner/manager stops epoch E with realized loss L so that
//    collectWithdrawFunds stores lossRecoveryPriceByEpoch[E] < RECOVERY_FULL
//    and pendingWithdraws = 0 (funds collected cover only haircutted basis).
cdo.stopEpochWithDuration(newApr, interest, duration, lossAmount);
// 3) In next epoch E+1 (buffer or running), attacker requests again:
vault.requestWithdraw(A2, user);          // lastWithdrawRequest[user] := E+1
// 4) Manager stops epoch E+1 normally; borrower funds pendingWithdraws in full.
// 5) epochNumber > E+1. Attacker claims:
cdo.claimWithdrawRequest();               // -> vault.claimWithdrawRequest(user)
//    _claimLossAdjustedWithdrawRequest reads lossRecoveryPriceByEpoch[E+1] == 0 -> skipped
//    _claimFundedWithdrawRequest pays withdrawsRequests[user] = A_E + A_{E+1} AT PAR
// Assert: received > A_E * lossRecoveryPrice / RECOVERY_FULL + A_{E+1}
// The stolen delta = A_E * (1 - lossRecoveryPrice/RECOVERY_FULL) drains other claimants' funds.
```