### Title
Funded instant-withdraw claims leave stale per-epoch entries that inflate default-recovery basis and reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest()` pays a funded instant-withdraw receipt and zeroes the aggregate counter `instantWithdrawsRequests[_user]`, but never clears the per-epoch back-pointers `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]`. This is the same bug class as CVE-2026-63894: the "completion" path frees the request but leaves a stale pointer that a later "cancel/finalize" path dereferences. When a borrower default is finalized in that same epoch (`defaultRecoveryEpoch == epochNumber`), `defaultPendingClaimBasis()` and `_defaultPrefundedInstantReserve()` read the stale epoch aggregates, counting already-paid claims as outstanding basis and already-spent funds as reserve. Recovery price and reserve are computed on phantom tokens, so the promised recovery exceeds the underlying actually held: later claimants are diluted or their `_transferDefaultRecovery` payouts revert on insufficient balance (permanent freezing of unclaimed recovery).

### Finding Description
The funded-claim path clears only the aggregate:

- `claimInstantWithdrawRequest` burns `instantWithdrawsRequests[_user]` and sets `instantWithdrawsRequests[_user] = 0`, but leaves `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` untouched (`IdleCreditVault.sol:387-392`).
- At request time both the per-user epoch map and the global epoch map are incremented (`IdleCreditVault.sol:366-374`).
- On default finalization, `defaultPendingClaimBasis()` adds `instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0` (`IdleCreditVault.sol:644-649`), and `_defaultPrefundedInstantReserve()` computes `instantWithdrawClaimsByEpoch[epochNumber] - pendingInstantWithdraws` as already-held reserve (`IdleCreditVault.sol:716-723`).
- `finalizeDefaultRecovery` then sets `defaultRecoveryEpoch = epochNumber`, `defaultRecoveryReserve = _recoveredAmount + prefundedReserve + defaultRecoveryReserve`, and `defaultRecoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` (`IdleCreditVault.sol:679-700`).

Because epoch-tagged claims are never cleared on the funded path, an attacker's already-paid claim `A` is still included in `instantWithdrawClaimsByEpoch[E]` when the borrower defaults during epoch `E`:

1. `totalBasis` is inflated by `A` (double-counted basis).
2. `prefundedReserve` is inflated by `A` (the cash backing `A` was already transferred out to the attacker, so `reserveAmount` overstates held underlying by `A`).
3. Every subsequent defaulted-claim payout via `_claimDefaultedInstantWithdrawRequest` / `_claimDefaultedWithdrawRequest` draws from `_transferDefaultRecovery` against `defaultRecoveryReserve`, which is short by `A`. Final claimants either receive less than the stated `defaultRecoveryPrice` or their transfers revert — permanently freezing their recovery.

The same stale-pointer asymmetry exists for normal withdraws: `_claimFundedWithdrawRequest` clears `withdrawsRequests[_user]` and `lastWithdrawRequest[_user]` but not `withdrawsRequestsByEpoch[_user][epoch]` (`IdleCreditVault.sol:338-348`). It is not exploitable the same way because defaulted claims key on `defaultRecoveryEpoch`, and an epoch's receipt cannot be claimed until `epochNumber` has advanced past it — so a stale normal entry can never alias a future claim epoch. The instant-claim path has no such gating: instant receipts are funded and claimed within the same epoch that later becomes `defaultRecoveryEpoch`, which is exactly the window where the stale entry is re-dereferenced.

### Impact Explanation
An unprivileged tranche-token holder requests an instant withdraw of amount `A` in epoch `E`, the borrower/CDO funds it, and the attacker claims at par via `claimInstantWithdrawRequest`. If the borrower defaults in the same epoch while another user's instant request remains unfunded (`pendingInstantWithdraws != 0`, triggering `defaultInstantWithdrawsFinalized`), finalization counts `A` twice: once already paid at par, once again inside `totalBasis` and `prefundedReserve`. Loss is bounded below by `min(A, shortfall)`: the last `A` worth of recovery claims are unbacked, producing direct theft from other claimants or permanent freezing of their unclaimed recovery. No existing guard stops this — the skim, default reverts, and `_onlyIdleCDO` gating are irrelevant; the corruption happens inside `_onlyIdleCDO`-trusted accounting that assumes epoch aggregates only ever contain unpaid claims.

### Likelihood Explanation
Requires an instant-withdraw-enabled vault (instant mode), attacker liquidity to place an instant request, and a borrower default in the same epoch while at least one other instant request is still unfunded — all ordinary operating conditions, no privileged misbehavior needed. The attacker does not even need to time the default; the stale state persists and any default in that epoch triggers the miscounting. Caveat I could not fully verify in the available index: whether `requestInstantWithdraw` is reachable by arbitrary tranche holders on all instant-mode deployments, and whether `epochNumber` can advance between the funded claim and the default declaration (if it advances, `instantWithdrawClaimsByEpoch[epochNumber]` keys on a different epoch and the inflation vanishes). If claims/funding and default always share the epoch counter, the bug is deterministic.

### Recommendation
Mirror the kernel fix's "clear the pointer at the same place that frees the object" rule: in `claimInstantWithdrawRequest`, delete `instantWithdrawsRequestsByEpoch[_user][epoch]` and decrement `instantWithdrawClaimsByEpoch[epoch]` for the request's epoch alongside `instantWithdrawsRequests[_user]`. Since the funded path doesn't currently track which epoch the aggregate belongs to, record the epoch at request time (e.g., store `lastInstantRequestEpoch[_user]` or clear per-epoch entries for all epochs ≤ current). Equivalently, make `defaultPendingClaimBasis()` and `_defaultPrefundedInstantReserve()` compute instant basis from `pendingInstantWithdraws`-consistent state only, so already-paid claims can never re-enter `totalBasis` or `reserveAmount`.

### Proof of Concept
Foundry fork sketch (vault in instant mode, `unscaledApr` arbitrary):

```solidity
// setup: user B has an unfunded instant request so pendingInstantWithdraws > 0
cdoEpoch.requestInstantWithdraw(amountB);              // user B, stays unfunded

// attacker
cdoEpoch.requestInstantWithdraw(A);                    // attacker, epoch E
// borrower/CDO funds A only: collectInstantWithdrawFunds(A) pulls cash in
vm.prank(address(cdoEpoch));
strategy.collectInstantWithdrawFunds(A);               // pendingInstantWithdraws -= A (B still pending)
vm.prank(attacker);
cdoEpoch.claimInstantWithdrawRequest();                // pays A at par; leaves
// instantWithdrawsRequestsByEpoch[attacker][E] = A and
// instantWithdrawClaimsByEpoch[E] still including A   <-- stale pointer

// borrower defaults during epoch E; honest manager/guardian finalize
cdoEpoch._handleBorrowerDefault();                     // defaulted() = true
strategy.finalizeDefaultRecovery(recovered, source);   // defaultRecoveryEpoch = E

// asserts:
// defaultPendingClaimBasis() includes A again (inflated totalBasis)
// _defaultPrefundedInstantReserve() = instantWithdrawClaimsByEpoch[E] - pendingInstantWithdraws
//   counts A as held reserve, but A's cash was already paid to attacker
// defaultRecoveryReserve > underlying.balanceOf(strategy) - activeNAV backing
// last claimant's claimWithdrawRequest/claimInstantWithdrawRequest reverts
// or pays less than defaultRecoveryPrice implies: shortfall == A
```

Key assertion: after `finalizeDefaultRecovery`, `underlyingToken.balanceOf(strategy)` is exactly `A` short of what `defaultRecoveryReserve` claims, because `instantWithdrawClaimsByEpoch[E]` was never decremented when the funded claim paid out.