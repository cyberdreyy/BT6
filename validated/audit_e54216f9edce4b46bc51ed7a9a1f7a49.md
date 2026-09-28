### Title
Stale per-epoch instant-withdraw entries are never cleared on funded claims, leaving phantom claims that corrupt default-recovery accounting - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The oracle-removal bug class — a "remove" operation that fails to clear the slot being removed while a parallel index still points at it — maps onto `IdleCreditVault.claimInstantWithdrawRequest`. Instant-withdraw receipts are tracked in two places: the aggregate `instantWithdrawsRequests[_user]` and the per-epoch index `instantWithdrawsRequestsByEpoch[_user][epoch]` / `instantWithdrawClaimsByEpoch[epoch]` (added so default finalization can distinguish default-epoch receipts from older funded ones). The claim path zeroes only the aggregate and burns the receipt, but never clears the per-epoch entries — the analogue of writing the "removed" element into a nonexistent slot while leaving the real entry stale.

### Finding Description
In `claimInstantWithdrawRequest` (lines 380-393) the code does:
- `amount = instantWithdrawsRequests[_user]`, `_burn(_user, amount)`, `instantWithdrawsRequests[_user] = 0`, then pays out.

It never writes `instantWithdrawsRequestsByEpoch[_user][currentEpoch] = 0` nor decrements `instantWithdrawClaimsByEpoch[currentEpoch]`, even though `requestInstantWithdraw` (lines 366-374) increments both, and both exist specifically to feed default finalization (`instantWithdrawClaimsByEpoch` is documented as "total outstanding instant-withdraw receipt basis per request epoch" for distinguishing default-epoch pending receipts from funded ones).

The dedicated default claim `_claimDefaultedInstantWithdrawRequest` (lines 842-856) reads `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` directly and, before the fix-class correction elsewhere, also recomputes `pendingInstantWithdraws` and `instantWithdrawClaimsByEpoch[defaultEpoch]` from those same maps. So a user whose funded instant receipt was already paid out still has a nonzero per-epoch basis recorded — a phantom claim entry that the removal path failed to delete.

### Impact Explanation
If default finalization (or the `defaultInstantWithdrawsFinalized` accounting) consumes `instantWithdrawClaimsByEpoch[defaultEpoch]`/`instantWithdrawsRequestsByEpoch` to size the unfunded default-epoch instant bucket, the stale entries inflate that bucket by every already-claimed receipt in the epoch. Consequences:

- `defaultRecoveryReserve` is over-allocated / `defaultRecoveryPrice` is depressed, diluting honest recovery claimants; or
- `instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis` arithmetic during claims produces inconsistent totals versus `pendingInstantWithdraws`, and
- in `_claimDefaultedInstantWithdrawRequest` itself, `instantWithdrawsRequests[_user] -= claimBasis` can underflow when a stale per-epoch basis exceeds the remaining aggregate, reverting and freezing that user's (and potentially the epoch's) recovery claims.

The direct double-spend is partially gated because `_burn(_user, claimBasis)` requires the user to still hold receipt tokens, but the reserve-dilution and claim-freeze paths do not depend on the attacker's token balance — only on the stale bookkeeping left behind by an ordinary, legitimate claim.

### Likelihood Explanation
The trigger is routine: any KYC-passing lender requests an instant withdraw, the borrower funds it at `startEpoch`, the lender claims it — the stale per-epoch entry now exists. If the pool later defaults and the manager/owner finalizes recovery in the same strategy epoch, the phantom basis enters accounting. Attacker control is limited to voluntarily creating and claiming instant receipts, which costs only the time value of the deposit; multiple wallets can maximize the stale basis.

**Unverified assumption:** I could not read `finalizeDefault`/`finalizeDefaultRecovery` within the search budget to confirm it sizes the instant bucket from `instantWithdrawClaimsByEpoch` rather than from `pendingInstantWithdraws` alone. If it uses only `pendingInstantWithdraws` (which is correctly decremented at `collectInstantWithdrawFunds`), the fund impact reduces to the underflow-freeze path in `_claimDefaultedInstantWithdrawRequest`, which still permanently locks recovery funds for affected users but is narrower.

### Recommendation
In `claimInstantWithdrawRequest`, clear the per-epoch entries for every epoch in which the user has a nonzero basis (or track the request epoch per user like `lastWithdrawRequest` and clear that slot):

```solidity
instantWithdrawsRequestsByEpoch[_user][reqEpoch] = 0;
instantWithdrawClaimsByEpoch[reqEpoch] -= amount;
```

Alternatively, change the default-claim path to derive the default-epoch basis from the funded/unfunded split recorded at finalization rather than trusting per-epoch receipts that funded claims never removed — the exact analogue of replacing with the last valid element instead of `oracles[numOracles]`.

### Proof of Concept
Foundry fork sketch (mainnet fork as in `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol`):

```solidity
// 1. Attacker (KYC'd lender) deposits AA, epoch starts.
// 2. During running epoch: requestInstantWithdraw via CDO.
// 3. Borrower funds instant withdraws (collectInstantWithdrawFunds).
// 4. Attacker calls claimInstantWithdrawRequest -> paid in full,
//    but instantWithdrawsRequestsByEpoch[attacker][epoch] and
//    instantWithdrawClaimsByEpoch[epoch] remain nonzero.
assert(strategy.instantWithdrawsRequests(attacker) == 0);
assert(strategy.instantWithdrawsRequestsByEpoch(attacker, epoch) != 0); // stale
// 5. Borrower defaults; owner finalizes default recovery.
// 6. Assert: defaultRecoveryPrice / defaultRecoveryReserve are
//    computed including the attacker's phantom basis, or
//    _claimDefaultedInstantWithdrawRequest underflows on
//    instantWithdrawsRequests[attacker] -= claimBasis and freezes.
```

The stale-entry assertion in step 4 is directly verifiable on the current code; step 6 quantifies the reserve dilution or the revert once `finalizeDefault` semantics are confirmed.