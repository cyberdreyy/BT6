I'll dig into the default-claim path and where per-epoch receipt bookkeeping is written/cleared, since stale epoch data is the closest analog to an out-of-bounds/stale read.### Title
Stale `instantWithdrawClaimsByEpoch` after funded instant claims inflates default recovery reserve, causing overpayment to early claimants and permanent freeze of later recovery claims — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug (CVE-2016-7410) is an out-of-bounds/stale read: `_dwarf_read_loc_section` reads data past the intended buffer, so the program acts on bytes it should have discarded. The analog in `IdleCreditVault` is a stale per-epoch read: `instantWithdrawClaimsByEpoch[epoch]` is incremented in `requestInstantWithdraw` but is only decremented in `_claimDefaultedInstantWithdrawRequest`. A *normal* (funded, pre-default) `claimInstantWithdrawRequest` pays the user and clears `instantWithdrawsRequests[_user]` but never removes the epoch-level basis. If the epoch later ends in a borrower default while `pendingInstantWithdraws` is still non-zero, `defaultPendingClaimBasis()` and `_defaultPrefundedInstantReserve()` re-read that stale epoch bucket and count already-paid-out underlyings as recovery reserve and claim basis.

### Finding Description
`requestInstantWithdraw` records each receipt in the aggregate `instantWithdrawClaimsByEpoch[currentEpoch]` (`contracts/strategies/idle/IdleCreditVault.sol:371-374`). When a user claims a funded instant request, `claimInstantWithdrawRequest` burns the receipt and calls `_transferFundedClaim`, but leaves `instantWithdrawsRequestsByEpoch` and `instantWithdrawClaimsByEpoch` untouched (lines 387-392). Only the post-default path `_claimDefaultedInstantWithdrawRequest` decrements `instantWithdrawClaimsByEpoch` (line 853).

On default finalization, `finalizeDefaultRecovery` computes:

```solidity
uint256 prefundedReserve = _defaultPrefundedInstantReserve();
uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;
```

(lines 685-688), where `_defaultPrefundedInstantReserve` returns `instantWithdrawClaimsByEpoch[epochNumber] - pendingInstantWithdraws` whenever the stale epoch bucket exceeds the unfunded remainder (lines 716-723), and `defaultPendingClaimBasis` adds the full stale `instantWithdrawClaimsByEpoch[epochNumber]` to `totalBasis` (lines 644-649).

Instant withdrawals are funded via `collectInstantWithdrawFunds`, which pulls underlyings from the CDO into the strategy and reduces `pendingInstantWithdraws` (lines 398-403). Once the user claims, those underlyings leave the strategy — but the epoch bucket still counts them. If the borrower then defaults at `stopEpoch` of the same epoch with part of the instant queue still unfunded, finalization treats the already-paid amount `A` as phantom reserve: `reserveAmount` includes `A` while `underlyingToken.balanceOf(strategy)` does not.

Broken invariant: "one receipt one payout" and recovery solvency — the same `A` underlyings are paid once via `_transferFundedClaim` and counted again inside `defaultRecoveryReserve`.

### Impact Explanation
`defaultRecoveryPrice` is computed against a reserve that is `A` larger than the real balance. Every defaulted-epoch and post-default claim calls `_transferDefaultRecovery`, which does `defaultRecoveryReserve -= _amount` and `safeTransfer` (lines 912-917). Early claimants (the attacker) therefore receive `claimBasis * inflatedPrice / RECOVERY_FULL` — a larger share of the real recovery pool than entitled — draining actual underlyings. When the reserve accounting has been consumed but real balance is exhausted by the phantom `A`, the last claimants' `safeTransfer` reverts: their recovery is permanently frozen. Quantitatively, the total excess paid out / deficit equals the stale instant-claim amount `A` from the default epoch, which an attacker maximizes by requesting the largest instant withdrawal that will be funded before default.

### Likelihood Explanation
Requires: (a) instant withdrawals enabled (`setInstantWithdrawParams`), (b) an epoch in which instant requests are only partially collected by the CDO (so `pendingInstantWithdraws != 0` at finalization), and (c) a borrower default in that same epoch — all reachable with honest privileged actors. The attacker is an unprivileged tranche holder who makes an instant request, claims it once funded, and lets the stale basis stand; or simply benefits passively as any early recovery claimant. The `defaultInstantWithdrawsFinalized` path exists precisely for the partially-funded instant queue, so the preconditions are an intended mode, not an edge case. No existing guard stops it: `_ensureDefaultRecoveryInitialized` reverts only on non-zero `pendingInstantWithdraws` at upgrade time, and the `_transferFundedClaim` reserve check (lines 897-905) only guards the funded side, not the inflated reserve itself.

### Recommendation
In `claimInstantWithdrawRequest`, decrement `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` for the epoch(s) being paid, mirroring `_claimDefaultedInstantWithdrawRequest` (store the request epoch per user, or decrement the current bucket when claims are only valid within the funding epoch). Alternatively, track funded instant claims separately so `_defaultPrefundedInstantReserve` counts only instant receipts that are still unclaimed, not the gross epoch total.

### Proof of Concept
Foundry fork test against `IdleCreditVault` + `IdleCDOEpochVariant` (instant-withdraw mode):

```solidity
// Setup: standard epoch variant, instant withdrawals enabled.
cdoEpoch.setInstantWithdrawParams(delay, 1e18, false);   // manager
// Epoch N running. attacker and victim both hold tranches / can request instant.

// 1) Attacker requests instant withdraw A; victim requests instant withdraw B.
cdoEpoch.requestInstantWithdraw(A);   // attacker
cdoEpoch.requestInstantWithdraw(B);   // victim
// instantWithdrawClaimsByEpoch[N] == A + B; pendingInstantWithdraws == A + B

// 2) CDO collects only enough to fund A (partial funding).
strategy.collectInstantWithdrawFunds(A); // pendingInstantWithdraws == B

// 3) Attacker claims A normally. BUG: instantWithdrawClaimsByEpoch[N] still == A + B.
cdoEpoch.claimInstantWithdrawRequest(); // attacker paid A; balance leaves strategy

// 4) Borrower defaults at stopEpoch of epoch N; recovery R pulled.
// finalizeDefaultRecovery:
//   prefundedReserve = (A + B) - B = A        // phantom: A was already paid out
//   reserveAmount    = R + A
//   totalBasis       = activeBasis + pendingWithdraws + (A + B)
//   defaultRecoveryPrice uses inflated numerator over inflated denominator.
// Real strategy balance = R (not R + A).

// 5) Attacker (or earliest claimant) draws recovery at the inflated price,
//    draining real underlyings. When victim claims last:
vm.expectRevert(); // safeTransfer fails: balance exhausted by phantom A
cdoEpoch.claimInstantWithdrawRequest(); // victim's recovery permanently frozen
```

Key assertions: after step 3, `strategy.instantWithdrawClaimsByEpoch(N) == A + B` despite `A` having been paid out; after step 4, `defaultRecoveryReserve > underlying.balanceOf(strategy)` by exactly `A`.