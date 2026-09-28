### Title
Instant-withdraw receipts from epochs before the default epoch escape recovery accounting and are paid at par or permanently frozen - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` tracks instant-withdraw receipts in two structures of different "length": the aggregate `instantWithdrawsRequests[_user]`/`pendingInstantWithdraws` (all epochs) and the per-epoch `instantWithdrawsRequestsByEpoch[_user][epoch]`/`instantWithdrawClaimsByEpoch[epoch]` (single epoch). Like CVE-2019-20387 (an over-read where the last stored element is shorter than the consumed input), `defaultPendingClaimBasis` reads only the current-epoch slice while the aggregate is larger, so pre-default-epoch unfunded instant receipts are missing from the recovery basis yet still claimable.

### Finding Description
- `requestInstantWithdraw` credits both `instantWithdrawsRequests[_user]` and `instantWithdrawsRequestsByEpoch[_user][epochNumber]` (`contracts/strategies/idle/IdleCreditVault.sol:366-372`). Nothing forces a user to claim before the epoch rolls over; a partially funded instant request can remain unfunded across `epochNumber` increments.
- On default finalization, `defaultPendingClaimBasis` adds only `instantWithdrawClaimsByEpoch[epochNumber]` — the *last* epoch — to the haircut basis, while `pendingInstantWithdraws` is the global unfunded aggregate (`IdleCreditVault.sol:644-649`). `_defaultPrefundedInstantReserve` similarly compares only the current-epoch `instantBasis` against global `pendingInstant` (`IdleCreditVault.sol:716-723`).
- `finalizeDefaultRecovery` therefore computes `recoveryPrice = reserveAmount / totalBasis` over a `totalBasis` that excludes old-epoch instant claims (`IdleCreditVault.sol:680-692`), inflating `defaultRecoveryPrice` for everyone else.
- After finalization, `claimInstantWithdrawRequest` calls `_claimDefaultedInstantWithdrawRequest`, which clears only `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` (`IdleCreditVault.sol:842-855`). The user's remaining pre-default-epoch receipts stay in `instantWithdrawsRequests[_user]`, are burned at par, and paid via `_transferFundedClaim` (`IdleCreditVault.sol:387-392`). Those tokens were never funded into the reserve: `_transferFundedClaim` only succeeds if `balance - defaultRecoveryReserve >= amount` (`IdleCreditVault.sol:899-905`), so the claim either (a) spends underlying belonging to funded normal receipts/strategy liquidity that was not earmarked for them, or (b) reverts permanently once only the reserve remains.

### Impact Explanation
Direct theft or permanent freezing of funds. In case (a) the user withdraws at 100% while every defaulted claimant receives `recoveryPrice < 1`, draining funded liquidity owed to other receipt holders and breaking the "one receipt one payout at the finalized ratio" invariant. In case (b) the claim reverts forever once the strategy balance equals `defaultRecoveryReserve`, permanently freezing the user's underlying. The loss is the full pre-default-epoch unfunded instant amount.

### Likelihood Explanation
Requires a borrower default with an instant-withdraw receipt requested in an epoch earlier than the default epoch and still unfunded (achievable whenever `collectInstantWithdrawFunds` covered only part of the queue and the user did not claim before `stopEpoch`/`startEpoch`). All steps use only unprivileged user calls plus honest privileged calls (`stopEpoch`, `finalizeDefaultRecovery`) in their normal sequence. No existing guard stops it: `_ensureDefaultRecoveryInitialized` only handles legacy upgrades, and the reserve guard in `_transferFundedClaim` protects the reserve, not the non-reserve liquidity the stale receipt consumes.

### Recommendation
Include all outstanding instant receipt epochs in `defaultPendingClaimBasis` (iterate or track a cumulative `instantWithdrawClaimsTotal`), or revert/route old-epoch instant claims through `defaultRecoveryPrice` by checking `instantWithdrawsRequests[_user]` after clearing the default-epoch slice instead of paying the remainder at par via `_transferFundedClaim`.

### Proof of Concept
Foundry fork-style sequence (structure; exact CDO helpers per `test/foundry/IdleCreditVault.t.sol`):

```solidity
// setup: credit vault running, borrower funded, epoch E1 active
cdo.depositAA(...); // attacker supplies liquidity
cdo.requestInstantWithdraw(attacker, amt); // -> instantWithdrawsRequestsByEpoch[attacker][E1]
// borrower funds only part; collectInstantWithdrawFunds covers < amt
// attacker does NOT claim; honest manager calls stopEpoch + startEpoch -> epoch E2
cdo.requestInstantWithdraw(attacker, amt2); // -> recorded under epoch E2
// borrower defaults during E2; guardian calls _handleBorrowerDefault/finalizeDefaultRecovery
// totalBasis excludes attacker's E1 receipt -> recoveryPrice inflated
// attacker calls claimInstantWithdrawRequest:
//   _claimDefaultedInstantWithdrawRequest clears only E2 slice
//   remaining E1 amount in instantWithdrawsRequests burns at par via _transferFundedClaim
//   -> attacker paid 100% while other defaulted claimants get recoveryPrice
// or the call reverts permanently once balance == defaultRecoveryReserve (frozen funds)
```

Uncertainty: whether the CDO's epoch flow always funds or clears `pendingInstantWithdraws` before an epoch rolls was not fully verified; if the epoch machine guarantees instant claims are fully funded within their request epoch, the leftover state may be unreachable and the finding reduces to a latent accounting fragility.