### Title
Older loss-adjusted withdraw receipts become permanently unclaimable because `lastWithdrawRequest` tracks only the latest epoch - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`IdleCreditVault` locates a user's loss-adjusted (partially funded) withdraw receipt through a single-slot marker, `lastWithdrawRequest[_user]`, which stores only one epoch number. When `_clearWithdrawClaimForEpoch` clears the claim at that epoch it unconditionally resets `lastWithdrawRequest[_user]` to `0` (`IdleCreditVault.sol:832-836`), even though older per-epoch receipts in `withdrawsRequestsByEpoch[_user][olderEpoch]` may still exist. Because `_claimLossAdjustedWithdrawRequest` (`IdleCreditVault.sol:789-792`) can only discover a funded loss epoch via `lastWithdrawRequest`, any older loss-adjusted receipt is orphaned: its bookkeeping still counts in `withdrawsRequests[_user]`, but no code path can ever locate its epoch again.

### Finding Description
This is the direct analog of the kernel bug: a cleanup (`_clearWithdrawClaimForEpoch` resetting the marker) invalidates a resource (the older epoch's funded receipt) that is still "in use" (still owed to the user and still counted in the aggregate `withdrawsRequests` balance and in strategy-held underlying).

- `collectWithdrawFunds` (`IdleCreditVault.sol:411-430`) can store `lossRecoveryPriceByEpoch[epochNumber]` on any partially funded stop and zeroes `pendingWithdraws`. There is no restriction to a single loss epoch, so epochs N and N+k can both carry loss-adjusted receipts.
- A user who requests a withdraw in epoch N (haircut-funded) and, without claiming, requests again in epoch N+k (also haircut-funded) has two entries in `withdrawsRequestsByEpoch`, but only N+k is recorded in `lastWithdrawRequest`.
- Claiming clears epoch N+k via `_clearWithdrawClaimForEpoch`, hits `lastWithdrawRequest[_user] == _claimEpoch`, and sets the marker to `0` (`IdleCreditVault.sol:832-836`).
- `_claimLossAdjustedWithdrawRequest` then reads `lossRecoveryPriceByEpoch[0] == 0` and returns early (`IdleCreditVault.sol:790-792`). The epoch-N receipt in `withdrawsRequestsByEpoch[_user][N]` is unreachable forever, while the underlying funding it sits locked in the strategy.

### Impact Explanation
Permanent freezing of the user's funded loss-adjusted claim. The underlying backing the orphaned receipt remains in the strategy with no withdrawal path, and the user's `withdrawsRequests` aggregate is permanently inflated by an amount they cannot claim. Loss equals the full funded value of the orphaned receipt (`claimBasis * lossRecoveryPriceByEpoch[N] / RECOVERY_FULL`).

### Likelihood Explanation
Requires two separate partially funded epoch stops (`stopEpochWithDuration`/`collectWithdrawFunds` shortfall) while the same user leaves the first receipt unclaimed and submits a new request. Whether this is reachable depends on whether `requestWithdraw` blocks a new request while a prior funded receipt is open — the post-default path does enforce this (`testPostDefaultWithdrawRequiresClaimingOpenPriorReceipt`), but I could not fully verify the pre-default request path within this analysis. If no such guard exists before default, the scenario only needs ordinary borrower underfunding on two distinct epochs plus user inaction, both plausible events.

### Recommendation
Replace the single `lastWithdrawRequest` slot with a per-user epoch list or a `previousWithdrawRequestEpoch` chain so all funded loss epochs remain discoverable, or revert `requestWithdraw` whenever `lastWithdrawRequest[_user] != 0` to force claim-before-requeue uniformly (mirroring the post-default guard).

### Proof of Concept
Conceptual Foundry sequence (fork test): user deposits AA → `requestWithdraw` → epoch runs → `stopEpochWithDuration` with `_lossAmount > 0` so `collectWithdrawFunds` sets `lossRecoveryPriceByEpoch[N]` and `lastWithdrawRequest[user] = N` → user does not claim → new epoch starts → user `requestWithdraw` again → second `stopEpochWithDuration` with loss sets `lastWithdrawRequest[user] = N+1` → `claimWithdrawRequest` pays only epoch N+1 and resets the marker to 0 → assert `withdrawsRequestsByEpoch[user][N] > 0` and that every subsequent claim returns 0 for it (frozen).