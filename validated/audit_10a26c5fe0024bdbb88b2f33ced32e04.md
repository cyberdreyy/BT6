### Title
Post-default instant withdraws mint receipts that drain the default recovery reserve — unsubmitted-claim double-put analog - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.requestWithdraw` contains an explicit `defaultRecoveryFinalized` branch that rejects users with open receipts and routes new requests into the isolated `postDefaultRequests` bucket, which is paid 1:1 from `defaultRecoveryReserve` only because the haircut was already applied at request time. `requestInstantWithdraw` has **no** such branch: after default finalization it still mints a full receipt and pushes it into `instantWithdrawsRequestsByEpoch[epochNumber]`, `instantWithdrawClaimsByEpoch[epochNumber]` and `pendingInstantWithdraws` — where `epochNumber` is frozen at `defaultRecoveryEpoch` forever. On claim, `_claimDefaultedInstantWithdrawRequest` then treats this never-submitted, never-accounted receipt as a defaulted-epoch claim and pays it out of `defaultRecoveryReserve`, a reserve sized exactly as `recoveryPrice * totalBasis` at `finalizeDefaultRecovery`. This is the same shape as the kernel bug: an allocation path (`_claimDefaultedInstantWithdrawRequest` / `transport_generic_free_cmd`) operates on a "resource" whose accounting entry was created *after* the books were closed, corrupting the fixed reserve.

### Finding Description
- `requestWithdraw` post-default path: `contracts/strategies/idle/IdleCreditVault.sol:247-257` — checks `_hasWithdrawRequest`, `instantWithdrawsRequests`, `postDefaultRequests`, and stores the request in `postDefaultRequests` paid at par.
- `requestInstantWithdraw` (`IdleCreditVault.sol:356-375`): no `defaultRecoveryFinalized` check. It calls `_ensureDefaultRecoveryInitialized()` (no-op once initialized), burns the CDO's strategy tokens, mints the receipt to the user, and increments `instantWithdrawsRequestsByEpoch[currentEpoch]`, `instantWithdrawClaimsByEpoch[currentEpoch]` and `pendingInstantWithdraws` where `currentEpoch == epochNumber == defaultRecoveryEpoch`.
- On `claimInstantWithdrawRequest` (`IdleCreditVault.sol:380-393`), when `defaultInstantWithdrawsFinalized` is true, `_claimDefaultedInstantWithdrawRequest` (`IdleCreditVault.sol:842-856`) reads `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]` — which now includes the post-default receipt — and executes `defaultRecoveryReserve -= claimBasis * defaultRecoveryPrice / RECOVERY_FULL` via `_transferDefaultRecovery` (`IdleCreditVault.sol:912-917`).
- The reserve was fixed in `finalizeDefaultRecovery` (`IdleCreditVault.sol:686-692`) as `reserveAmount` against `totalBasis` computed by `defaultPendingClaimBasis` (`IdleCreditVault.sol:644-649`). Every post-default instant receipt consumes reserve it was never sized for, so the last legitimate defaulted-epoch claimants (normal `withdrawsRequestsByEpoch[defaultEpoch]` and pre-default instant receipts) find `defaultRecoveryReserve` exhausted and their `_transferDefaultRecovery`/`_burn` reverts — permanent freezing of unclaimed recovery.
- When `defaultInstantWithdrawsFinalized` is false (all instant claims were prefunded at finalization), the post-default receipt instead falls through to the funded path and hits the guard at `IdleCreditVault.sol:899-905` (`balance - reserve < _amount`), because no `collectInstantWithdrawFunds`/`stopEpoch` can ever run post-default — so the receipt is permanently frozen and `pendingInstantWithdraws`/`instantWithdrawClaimsByEpoch` remain permanently inflated.
- Existing guards do not stop it: `_ensureDefaultRecoveryInitialized` only runs once; the `defaultInstantWithdrawsFinalized` flag is computed at finalization and cannot be re-evaluated; `requestWithdraw`'s post-default checks (`instantWithdrawsRequests[_user] != 0` revert at line 249) actually *worsen* it — a user who makes a post-default instant request can never open a post-default normal request again and can never recover the instant receipt's full value.

### Impact Explanation
Insolvency of `defaultRecoveryReserve` and permanent freezing of unclaimed default-recovery payouts for honest defaulted-epoch receipt holders, plus permanent freezing of the attacker's/post-default instant receipts themselves when `defaultInstantWithdrawsFinalized == false`. Loss is bounded by `defaultRecoveryReserve` consumed by unauthorized post-default claims — quantitatively `Σ postDefaultInstantAmount * defaultRecoveryPrice / RECOVERY_FULL`, which can approach the full remaining reserve if the attacker holds a large tranche position. The attacker's cost is burning tranche tokens valued near `recoveryPrice` per unit, so this is primarily a reserve-insolvency / griefing-with-fund-impact vector rather than profitable theft; the protocol harm (honest claimants' payouts frozen) is real regardless of attacker profit.

### Likelihood Explanation
Requires the vault to reach a finalized default (`defaulted()` + `finalizeDefault`), an epoch phase the codebase explicitly supports post-default UX for (`postDefaultRequests`). Requires the attacker to hold tranche tokens post-default and `IdleCDOEpochVariant.requestInstantWithdraw` to remain callable after `defaulted()` — I was unable to fully verify the CDO-side gating in `IdleCDOEpochVariant.sol` (search results were truncated), so if the CDO reverts instant requests once defaulted, the strategy-side asymmetry is unreachable. Likelihood therefore hinges on that entry point being open; the strategy clearly *expects* to be callable, since it has no default check of its own while its sibling function does.

### Recommendation
Mirror the `requestWithdraw` post-default handling in `requestInstantWithdraw`: after `defaultRecoveryFinalized`, reject if the user has any open receipt (`_hasWithdrawRequest`, `instantWithdrawsRequests`, `postDefaultRequests`), and either revert outright or record the request in `postDefaultRequests` (par-paid from the reserve) instead of `instantWithdrawsRequestsByEpoch`. Alternatively revert on `defaultRecoveryFinalized` at the top of `requestInstantWithdraw`, matching the closed-book semantics of the reserve.

### Proof of Concept
Foundry fork PoC sketch (modeled on `test/foundry/IdleCreditVault.t.sol` `testFinalizeDefaultDistributesOverRecoveryProRata`):

```solidity
// 1. Users A (attacker) and B (victim) deposit via depositAA.
// 2. B calls cdoEpoch.requestWithdraw(...) -> pending receipt in epoch N.
// 3. startEpoch, then stopEpoch with 0 repayment -> defaulted.
// 4. finalizeDefault(recovered, manager) with recovered < basis -> defaultRecoveryPrice < 1e18,
//    defaultInstantWithdrawsFinalized depends on pendingInstantWithdraws (set true via a
//    partially funded instant request for full exploit path).
// 5. Attacker: cdoEpoch.requestInstantWithdraw(trancheBal, AATranche)  // NO revert - bug
//    -> mints receipt, instantWithdrawsRequestsByEpoch[attacker][defaultRecoveryEpoch] += amt
// 6. Attacker: cdoEpoch.claimInstantWithdrawRequest()
//    -> _claimDefaultedInstantWithdrawRequest pays amt * defaultRecoveryPrice / 1e18
//       from defaultRecoveryReserve.
// 7. Victim: cdoEpoch.claimWithdrawRequest()
//    -> _transferDefaultRecovery reverts (reserve underflow / insufficient balance)
//       => victim's defaulted-epoch recovery permanently frozen.
```