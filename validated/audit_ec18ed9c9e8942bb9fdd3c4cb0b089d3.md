### Title
Unfunded instant-withdraw receipts from non-default epochs are excluded from recovery basis in `defaultPendingClaimBasis` — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The TensorFlow bug was a missing shape-consistency check: code assumed every inner element was fully specified and wrote/paid out on that assumption. The analog in `IdleCreditVault` is `defaultPendingClaimBasis`, which assumes that whenever `pendingInstantWithdraws != 0`, *all* unfunded instant receipts belong to `instantWithdrawClaimsByEpoch[epochNumber]` (the current epoch). `pendingInstantWithdraws` is an aggregate across epochs, while the recovery basis and the prefunded-reserve estimate are indexed to a single epoch. A partially funded instant queue carried over from an earlier epoch breaks this assumption, miscomputing both the claim basis and the reserve, so unfunded receipt holders from older epochs are excluded from recovery and recovery funds are misallocated.

### Finding Description
Instant receipts are recorded per epoch (`instantWithdrawsRequestsByEpoch[user][epoch]`, `instantWithdrawClaimsByEpoch[epoch]`), but funding state is only tracked by the aggregate `pendingInstantWithdraws`, decremented in `collectInstantWithdrawFunds` (lines 398–403). Nothing forces the instant queue to be fully funded before the next epoch: `getInstantWithdrawFunds` can collect only part of `pendingInstantWithdraws`, and `stopEpoch` can still proceed because the guard only checks `_pendingInstant()` on the CDO side, which is a different counter.

At default finalization:
- `defaultPendingClaimBasis` (lines 644–649) adds `instantWithdrawClaimsByEpoch[epochNumber]` only — i.e., receipts issued in the *default* epoch. Unfunded instant receipts carried in `pendingInstantWithdraws` from an earlier epoch contribute to the unfunded remainder but are excluded from `totalBasis`, so `defaultRecoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` is computed against a smaller basis and their claims are never priced into recovery.
- `_defaultPrefundedInstantReserve` (lines 716–723) computes `instantBasis - pendingInstant` for the *current* epoch. If `pendingInstant` includes an older-epoch remainder larger than the current-epoch basis, the prefunded reserve is computed as 0 even when the strategy actually holds cash prefunded for current-epoch receipts — those underlyings are neither pulled nor reserved, while the aggregate still blocks `_ensureDefaultRecoveryInitialized`-style assumptions.
- `_claimDefaultedInstantWithdrawRequest` (lines 842–856) only clears `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]`. An older-epoch unfunded receipt can never take this path; its only path is `claimInstantWithdrawRequest` → `_transferFundedClaim`, which either reverts against the `defaultRecoveryReserve` guard (permanent freeze) or, if balance allows, pays at par out of funds that form the recovery backing of other claimants.
- Additionally, `pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis` (line 852) lets one current-epoch claim zero out the aggregate remainder belonging to other epochs' claims.

The broken invariant is "one receipt, one haircut, one payout": the code assumes a homogeneous single-epoch claim shape and silently drops non-conforming (older-epoch unfunded) receipts, exactly like the OOB write assuming uniform inner shapes.

### Impact Explanation
Direct loss/misallocation of recovery funds and permanent freezing of unclaimed yield:
- Users holding unfunded instant receipts from the epoch preceding the default epoch get **no recovery claim at all** — their basis is omitted from `totalBasis`, so the same recovered dollars are divided among fewer claims and their receipt tokens become unpayable (claim either reverts on the reserve guard or, if it succeeds, steals par value from other claimants' recovery backing).
- When the carried-over `pendingInstantWithdraws` exceeds the current-epoch `instantWithdrawClaimsByEpoch`, genuinely prefunded instant backing is not added to `defaultRecoveryReserve`, understating the reserve while `_transferDefaultRecovery`/`_transferFundedClaim` accounting assumes otherwise — insolvency for late claimants.

No privileged attacker is required beyond sequencing around honest owner/manager calls: the attacker is any lender who requests an instant withdraw that is only partially funded, then lets a new epoch start; the loss materializes automatically on borrower default + `finalizeDefaultRecovery`. Existing guards do not stop it: `defaultRecoveryFinalized`/`_onlyIdleCDO` gating is correct, and `_transferFundedClaim`'s reserve guard at best converts the theft into a permanent freeze.

### Likelihood Explanation
Requires (1) an instant-withdraw request partially funded via `getInstantWithdrawFunds` (a normal manager action when borrower cash is short), (2) a subsequent epoch with new instant requests, and (3) a borrower default finalized via `finalizeDefaultRecovery`. All are ordinary protocol flows; no malicious privileged role is needed. Likelihood is moderate — it needs the specific partial-funding-then-default sequence, which is precisely the stressed-liquidity scenario instant withdrawals exist for.

### Recommendation
Make the recovery accounting epoch-shape-consistent instead of assuming all unfunded instant claims live in `epochNumber`:
- Track unfunded instant basis explicitly (e.g., a `pendingInstantBasis` aggregate updated on request/collect/claim) rather than deriving it from `instantWithdrawClaimsByEpoch[epochNumber]` vs `pendingInstantWithdraws`.
- In `defaultPendingClaimBasis`, include the full unfunded instant remainder across epochs, not just the current-epoch claim map.
- In `_claimDefaultedInstantWithdrawRequest`, iterate or aggregate per-epoch bases for `claimBasis <= defaultRecoveryEpoch` instead of clearing a single epoch and clamping `pendingInstantWithdraws` to zero.
- In `_defaultPrefundedInstantReserve`, bound prefunded backing by actual strategy balance attributable to instant claims, not by `instantBasis - pendingInstant` for one epoch.

### Proof of Concept
Foundry fork sketch (against `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
// Setup: KYC'd users Alice (instant) and Bob (instant), epoch running.
// Epoch N:
alice.requestInstantWithdraw via CDO;          // instantWithdrawsRequestsByEpoch[alice][N] = A
// Manager partially funds the instant queue:
vm.prank(manager); cdo.getInstantWithdrawFunds(); // borrower sends only A/2
// collectInstantWithdrawFunds: pendingInstantWithdraws = A - A/2 = A/2 (still != 0)

// stopEpoch -> epochNumber = N+1, epoch N+1 starts
// Epoch N+1:
bob.requestInstantWithdraw via CDO;            // instantWithdrawClaimsByEpoch[N+1] = B
// pendingInstantWithdraws = A/2 + B

// Borrower defaults during epoch N+1:
vm.prank(manager); cdo.stopEpochWithDuration(...); // getFundsFromBorrower reverts -> _handleBorrowerDefault
cdo.finalizeDefault(...); // -> strategy.finalizeDefaultRecovery(recovered, source)

// Assertions:
// defaultPendingClaimBasis() == pendingWithdraws + B   <-- Alice's A/2 excluded
// _defaultPrefundedInstantReserve == B > (A/2 + B) ? 0 : B - (A/2 + B) = 0
//   => cash prefunded for Bob's receipts is not reserved
// defaultRecoveryPrice uses totalBasis missing A/2

// Alice calls claimInstantWithdrawRequest via CDO:
//   - _claimDefaultedInstantWithdrawRequest: instantWithdrawsRequestsByEpoch[alice][N+1] == 0 -> skip
//   - falls to _transferFundedClaim(A) -> either reverts (balance - reserve < A) = permanent freeze,
//     or pays par from recovery backing = theft from Bob/active-LP recovery.
assertTrue(aliceClaimRevertedOrOverpaid);
```