### Title
Instant-withdraw epoch claim counters are never decremented on a funded claim, inflating the default-recovery basis and permanently stranding recovered funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`requestInstantWithdraw` increments three "refcount" ledgers: `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][epoch]`, and the aggregate `instantWithdrawClaimsByEpoch[epoch]`. On the normal funded-claim path, `claimInstantWithdrawRequest` burns the user's receipt and clears `instantWithdrawsRequests[_user]`, but never releases the two per-epoch counters — the exact analog of a missing `of_node_put()` on an incremented refcount. Those leaked counters are later read by `defaultPendingClaimBasis()` and `_defaultPrefundedInstantReserve()` during `finalizeDefaultRecovery`, which inflates the recovery claim basis, depresses `defaultRecoveryPrice` for every claimant, and leaves a portion of `defaultRecoveryReserve` unclaimable forever.

### Finding Description
- Request path (`requestInstantWithdraw`, `contracts/strategies/idle/IdleCreditVault.sol:366-374`): `instantWithdrawsRequests[_user] += _amount`, `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount`, `instantWithdrawClaimsByEpoch[currentEpoch] += _amount`, `pendingInstantWithdraws += _amount`.
- Funded claim path (`claimInstantWithdrawRequest`, lines 380-393): burns `instantWithdrawsRequests[_user]`, sets it to 0, and pays via `_transferFundedClaim`. Neither `instantWithdrawsRequestsByEpoch[_user][epoch]` nor `instantWithdrawClaimsByEpoch[epoch]` is decremented — a missing put on two incremented references.
- Funding path (`collectInstantWithdrawFunds`, lines 398-403): `pendingInstantWithdraws -= _amount` as the CDO prefunds the strategy. So a user can fully claim while `pendingInstantWithdraws != 0` (another user's request in the same epoch is still unfunded).
- Default path: if the pool defaults in that same epoch while `pendingInstantWithdraws != 0`, `defaultPendingClaimBasis()` (lines 644-649) adds `instantWithdrawClaimsByEpoch[epochNumber]` — including the already-paid, leaked amount — to `basis`. `finalizeDefaultRecovery` (lines 679-692) then computes `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` on the inflated `totalBasis`.
- `_defaultPrefundedInstantReserve` (lines 716-723) also computes `instantBasis - pendingInstant` from the same stale counter, but it only *adds* strategy-held cash to the reserve — it does not remove the inflated basis, so the mismatch persists.
- Only the defaulted-epoch clearing path `_claimDefaultedInstantWithdrawRequest` (lines 842-856) decrements `instantWithdrawClaimsByEpoch`, and only for the default epoch — there is no compensating decrement for a funded claim.

### Impact Explanation
- `defaultRecoveryPrice` is computed on a `totalBasis` that includes claims already paid in full. Every legitimate claimant — active AA/BB tranche holders (via `defaultBBNav`/`activeFinalNAV` at lines 699-705) and pending/instant receipt holders — receives a strictly lower payout per unit of basis.
- The share of `defaultRecoveryReserve` corresponding to the leaked basis is never claimed (no user owns those phantom claims), so `_transferDefaultRecovery` can never drain it. Recovered underlying equal to roughly `leakedBasis * recoveryPrice / RECOVERY_FULL` is permanently frozen in the strategy — a direct, quantified loss of recovery value attributable to the missing decrement, matching the "leaked reference → leaked resource" shape of CVE-2022-49473.

### Likelihood Explanation
Requires an unprivileged lender to (1) request an instant withdraw, (2) have the CDO/manager fund and the user claim it via `claimInstantWithdrawRequest`, while (3) another instant request in the same epoch remains unfunded (`pendingInstantWithdraws != 0`) when the borrower defaults. Partial instant funding is explicitly modeled by `_defaultPrefundedInstantReserve`, so the sequencing is a supported flow, not an edge case. Attacker needs only a KYC-passing wallet making an instant request and claiming early; no privileged cooperation beyond honest manager epoch calls. The bug is passive — it also harms honest users without any attacker, which lowers the bar further.

### Recommendation
In `claimInstantWithdrawRequest`, mirror the release done in `_claimDefaultedInstantWithdrawRequest`: for each epoch in which the user still has `instantWithdrawsRequestsByEpoch[_user][e] != 0` (at minimum the user's outstanding entries up to `epochNumber`), subtract the claimed amount from `instantWithdrawsRequestsByEpoch[_user][e]` and `instantWithdrawClaimsByEpoch[e]`, so aggregate per-epoch claim counters always equal the sum of unclaimed receipts. Alternatively, derive the default basis from `pendingInstantWithdraws` plus explicitly funded receipts rather than a never-cleared counter.

### Proof of Concept
Foundry fork sketch (mirroring `test/foundry/IdleCDOEpochQueue.t.sol` and `test/foundry/IdleCreditVault.t.sol` setups):

```solidity
function testLeakInstantClaimCounter() external {
    // Epoch N running, instant withdrawals enabled.
    // 1) userA and userB each requestInstantWithdraw(amount) in epoch N.
    //    pendingInstantWithdraws = amountA + amountB
    //    instantWithdrawClaimsByEpoch[N] = amountA + amountB
    // 2) Manager/IdleCDO funds only userA's share:
    //    deal underlying to CDO; CDO transfers so strategy calls
    //    collectInstantWithdrawFunds(amountA) -> pendingInstantWithdraws = amountB
    // 3) userA claims via CDO -> claimInstantWithdrawRequest(userA)
    //    userA paid; instantWithdrawsRequests[userA] = 0
    //    BUG: instantWithdrawClaimsByEpoch[N] still = amountA + amountB
    // 4) Borrower defaults in epoch N (stopEpoch fails to pull funds;
    //    cdoEpoch.defaulted() == true), owner calls finalizeDefaultRecovery.
    //    defaultPendingClaimBasis() = pendingWithdraws
    //        + instantWithdrawClaimsByEpoch[N]  // includes paid amountA
    //    => totalBasis inflated by amountA
    //    => defaultRecoveryPrice < reserveAmount * RECOVERY_FULL / trueBasis
    // 5) Assert: sum of all legitimate claims * recoveryPrice
    //    < defaultRecoveryReserve - dust, where dust ~= amountA * recoveryPrice.
    //    That dust can never be withdrawn: permanent freezing of recovered funds.
    // Before fix: assertEq(instantWithdrawClaimsByEpoch[N], amountB) fails;
    // after fix it holds and recoveryPrice is no longer diluted.
}
```

Key assertion demonstrating the leak: after step 3, `strategy.instantWithdrawClaimsByEpoch(epochN)` equals `amountA + amountB` even though `amountA` was already paid out, and `strategy.instantWithdrawsRequestsByEpoch(userA, epochN)` remains nonzero despite `instantWithdrawsRequests(userA) == 0` and userA's receipt tokens being burned.

Caveat: I could not fully verify the exact CDO-side orchestration call sequence that allows a single user's instant claim to settle while sibling requests remain pending; if `claimInstantWithdrawRequest` is only reachable atomically with full funding of the epoch's instant bucket, the exploitable window narrows but the counter leak itself is still present in the code as written.