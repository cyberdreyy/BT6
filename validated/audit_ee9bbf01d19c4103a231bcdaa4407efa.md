### Title
Stale `instantWithdrawClaimsByEpoch` / `instantWithdrawsRequestsByEpoch` are never decremented on a funded claim, inflating default-recovery basis and prefunded reserve — ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The twTAP bug class is an aggregate that is incremented on entry but never decremented on exit, so later accounting that relies on it is permanently skewed. The direct analog exists in `IdleCreditVault`: `requestInstantWithdraw` increments `instantWithdrawsRequestsByEpoch[user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` (IdleCreditVault.sol:371-374), but the normal funded-claim path `claimInstantWithdrawRequest` only clears `instantWithdrawsRequests[user]` (IdleCreditVault.sol:387-392). The per-epoch aggregates are only decremented in the *defaulted* claim path `_claimDefaultedInstantWithdrawRequest` (IdleCreditVault.sol:847-853). A user whose instant withdrawal was funded and claimed therefore leaves a permanent ghost receipt in `instantWithdrawClaimsByEpoch[epochNumber]`, which is exactly the basis later consumed by `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` during `finalizeDefaultRecovery`.

### Finding Description
- `requestInstantWithdraw` does `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount; instantWithdrawClaimsByEpoch[currentEpoch] += _amount; pendingInstantWithdraws += _amount` (IdleCreditVault.sol:366-374).
- `collectInstantWithdrawFunds` decrements `pendingInstantWithdraws` when the CDO funds the request (IdleCreditVault.sol:398-403), and `claimInstantWithdrawRequest` burns the receipt and zeroes `instantWithdrawsRequests[_user]` — but leaves both per-epoch mappings untouched (IdleCreditVault.sol:387-392).
- Later, if the borrower defaults in the *same* epoch while some other instant request is still unfunded (`pendingInstantWithdraws != 0`), `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` — which still includes the already-claimed ghost amount — to the recovery claim basis (IdleCreditVault.sol:644-649).
- Worse, `_defaultPrefundedInstantReserve` computes `instantBasis - pendingInstant` and counts that phantom difference as underlying *already held* by the strategy (IdleCreditVault.sol:716-722). Those tokens were paid out to the first claimant, so `reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve` is inflated by funds that do not exist (IdleCreditVault.sol:686-688).

### Impact Explanation
Because `totalBasis` and `reserveAmount` are both inflated, but only `reserveAmount` is inflated by *nonexistent* tokens, `recoveryPrice` is set too high relative to real holdings. Early default claimants (normal pending receipts via `_claimDefaultedWithdrawRequest` and instant receipts via `_claimDefaultedInstantWithdrawRequest`) are paid at this inflated price until the real underlying is exhausted; subsequent claims then revert — either on `defaultRecoveryReserve -= _amount` underflow in `_transferDefaultRecovery` (IdleCreditVault.sol:912-917) or on the ERC20 transfer — permanently freezing the remaining claimants' recovery. This is a redistribution from late claimants to early claimants plus permanent freezing, i.e., the same "aggregate only grows, exit never decrements" insolvency as the twTAP `averageMagnitude` bug.

### Likelihood Explanation
Requires: (1) an instant withdrawal requested, funded by `collectInstantWithdrawFunds`, and claimed; (2) a second unfunded instant withdrawal in the same epoch (`pendingInstantWithdraws != 0` at finalization); (3) borrower default finalized via `finalizeDefaultRecovery` before `stopEpoch` bumps `epochNumber` — all achievable by an unprivileged tranche holder timing requests around honest manager/borrower calls. The early-claimer advantage also lets an attacker who holds a pending receipt order their claim first to capture the phantom-backed overpayment, making the loss a direct theft vector rather than only a freeze.

### Recommendation
Mirror the fix applied to twTAP — update both the per-user aggregate and the global aggregate on exit. In `claimInstantWithdrawRequest`, clear the per-epoch entries the same way `_claimDefaultedInstantWithdrawRequest` does: set `instantWithdrawsRequestsByEpoch[_user][currentEpoch] = 0` and `instantWithdrawClaimsByEpoch[currentEpoch] -= amount` for the relevant request epoch(s). Alternatively, record the request epoch per user (or make instant claims epoch-scoped like `lastWithdrawRequest`) so funded claims always decrement the correct `instantWithdrawClaimsByEpoch` bucket, keeping `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` consistent with real outstanding claims.

### Proof of Concept
```solidity
// Foundry fork test against an epoch-variant CDO + IdleCreditVault (APR > 0 mode).
// Actors: userA, userB = KYC'd lenders (unprivileged); manager/borrower honest.

// Epoch N running:
1. userA deposits, then calls requestInstantWithdraw(100e6) via the CDO.
   // instantWithdrawClaimsByEpoch[N] = 100e6, pendingInstantWithdraws = 100e6
2. Manager triggers instant funding; CDO calls collectInstantWithdrawFunds(100e6).
   // pendingInstantWithdraws = 0, strategy holds 100e6
3. userA calls claimInstantWithdrawRequest -> receives 100e6.
   // instantWithdrawsRequests[A] = 0 BUT instantWithdrawsRequestsByEpoch[A][N]
   // and instantWithdrawClaimsByEpoch[N] still = 100e6  <-- stale aggregate
4. userB calls requestInstantWithdraw(50e6) in the same epoch N.
   // instantWithdrawClaimsByEpoch[N] = 150e6, pendingInstantWithdraws = 50e6
5. Borrower defaults. Owner calls finalizeDefault / CDO calls
   finalizeDefaultRecovery(R, recoverySource):
   - basis includes instantWithdrawClaimsByEpoch[N] = 150e6 (B's 50 + A's ghost 100)
   - _defaultPrefundedInstantReserve() = 150e6 - 50e6 = 100e6 phantom reserve
   - recoveryPrice = (R + 100e6_phantom + realHeld) / totalBasis  // inflated
6. Claim race: first claimants are paid at inflated recoveryPrice;
   defaultRecoveryReserve is drained below zero-basis claims ->
   _transferDefaultRecovery underflow reverts -> remaining claimants (incl. B)
   permanently unable to claim.

assertEq(strategy.instantWithdrawClaimsByEpoch(epochN), 150e6); // ghost included
assertGt(strategy.defaultRecoveryPrice(), fairPrice);           // inflated price
vm.expectRevert(); // late claimant's claimInstantWithdrawRequest / claimWithdrawRequest
```

Uncertainty note: whether a funded instant withdrawal can be claimed *within the same epoch* before `stopEpoch` depends on the CDO-side instant-liquidity path (`getInstantWithdrawFunds`); the PoC assumes the prefunded/instant flow permits same-epoch funding and claim, which is the intended design of the instant-withdraw queue. If same-epoch claim were impossible, the ghost entry could not coexist with `pendingInstantWithdraws != 0` and the bug would not trigger — this should be confirmed against `IdleCDOEpochVariant`'s instant-withdraw funding path.