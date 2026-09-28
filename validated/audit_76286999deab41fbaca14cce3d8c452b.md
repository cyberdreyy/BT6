### Title
Instant-withdraw per-epoch claim basis is never cleared on funded claims, enabling double-counted default-recovery payouts - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Analog to the DHCP refcount leak (`option_code_hash_lookup()` increments a counter that is never decremented): `IdleCreditVault.requestInstantWithdraw` increments per-epoch receipt counters (`instantWithdrawsRequestsByEpoch[user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]`), but the normal funded-claim path `claimInstantWithdrawRequest` only clears the aggregate `instantWithdrawsRequests[_user]` — the per-epoch counters are never decremented. `collectInstantWithdrawFunds` likewise only decrements `pendingInstantWithdraws`. If a borrower default is later finalized while the same epoch still has unfunded instant withdrawals, `defaultPendingClaimBasis()` re-reads the stale `instantWithdrawClaimsByEpoch[epochNumber]` (inflating the recovery basis and diluting `defaultRecoveryPrice` for every other claimant), and the stale `instantWithdrawsRequestsByEpoch[user][defaultEpoch]` lets the already-paid user claim the same basis a second time through `_claimDefaultedInstantWithdrawRequest`, stealing from `defaultRecoveryReserve`.

### Finding Description
- `requestInstantWithdraw` mints a receipt and accumulates three counters: `instantWithdrawsRequests[_user] += _amount`, `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount`, and `instantWithdrawClaimsByEpoch[currentEpoch] += _amount` (`IdleCreditVault.sol:366-374`).
- `claimInstantWithdrawRequest` burns `instantWithdrawsRequests[_user]` and zeroes only that aggregate (`IdleCreditVault.sol:387-392`). Neither the per-user-per-epoch mapping nor the epoch total is touched.
- `collectInstantWithdrawFunds` decrements only `pendingInstantWithdraws` (`IdleCreditVault.sol:398-403`), leaving the stale epoch basis fully counted in `_defaultPrefundedInstantReserve` and `defaultPendingClaimBasis` (`IdleCreditVault.sol:644-649`, `716-723`).
- On default finalization, `finalizeDefaultRecovery` adds `instantWithdrawClaimsByEpoch[epochNumber]` to `totalBasis` whenever `pendingInstantWithdraws != 0` (`IdleCreditVault.sol:679`, `696`). Already-paid claims are therefore included in the denominator, pushing `defaultRecoveryPrice` below its correct value for honest claimants.
- `_claimDefaultedInstantWithdrawRequest` pays `(claimBasis * defaultRecoveryPrice)` where `claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch]` (`IdleCreditVault.sol:842-855`). For a user who already claimed at par, the stale basis remains nonzero; the check `instantWithdrawsRequests[_user] -= claimBasis` only underflow-protects if the user has no newer request — an attacker can front-run finalization with a fresh small `requestInstantWithdraw` (allowed while `defaultInstantWithdrawsFinalized` is not yet set, or simply hold an unclaimed instant receipt), so `instantWithdrawsRequests[_user]` is nonzero and the stale basis is paid out again.

This mirrors the advisory exactly: an incremented accounting counter (lease-query option refcount / epoch instant-claim basis) with no matching decrement on the consumption path, so repeated/stale entries corrupt global accounting.

### Impact Explanation
- Honest normal and instant withdraw requesters of the defaulted epoch receive a diluted `defaultRecoveryPrice` because the denominator includes basis already paid out at par — direct loss of recovery yield proportional to the double-counted amount.
- The attacker receives a second payout of `staleBasis * defaultRecoveryPrice` drawn from `defaultRecoveryReserve`, i.e., theft of funds reserved for other claimants. With a large funded instant withdrawal immediately claimed and then a default in the same epoch, the stolen amount approximates the full stale basis times the recovery ratio.
- Invariant broken: "one receipt one payout" and fair loss socialization.

### Likelihood Explanation
- Requires instant-withdraw mode in use and a borrower default (`_handleBorrowerDefault`/`finalizeDefaultRecovery`) occurring in the same epoch in which a funded instant claim was already paid, while `pendingInstantWithdraws` remains nonzero (i.e., other instant requests unfunded). Attacker is an ordinary tranche holder with `requestInstantWithdraw`/`claimInstantWithdrawRequest` access via the CDO — no privileged role needed.
- The sequence is deterministic: (1) request instant withdraw amount A; (2) borrower/CDO funds it via `collectInstantWithdrawFunds` (honest keeper action); (3) claim A at par; (4) keep or open a dust instant request so `instantWithdrawsRequests[attacker] != 0`; (5) default finalization occurs in the same epoch while other requests are pending → stale basis re-enters `totalBasis` and is claimable again.
- No existing guard stops it: `_onlyIdleCDO` is satisfied, `defaultInstantWithdrawsFinalized` enables exactly the claim path abused, and nothing validates that per-epoch basis corresponds to live receipts.

### Recommendation
In `claimInstantWithdrawRequest`, clear per-epoch accounting symmetrically with the request path: subtract `instantWithdrawsRequestsByEpoch[_user][epoch]` entries and decrement `instantWithdrawClaimsByEpoch[epoch]` for the user's outstanding request epochs (or store per-request epochs and clear them), and decrement `pendingInstantWithdraws` for any still-unfunded portion of the claimed amount so `defaultPendingClaimBasis` never re-reads dead basis.

### Proof of Concept
Not fully reproducible here (no live fork execution available in this session), but the Foundry PoC outline on a fork:

```solidity
// Arrange: epoch running, instant withdraws enabled, underlying = USDC
// 1. attacker deposits AA, requests instant withdraw A via cdoEpoch.requestInstantWithdraw path
// 2. manager stopEpoch/borrower funds: strategy.collectInstantWithdrawFunds(A) -> pendingInstantWithdraws decreases
//    (leave another user's instant request B unfunded so pendingInstantWithdraws == B > 0)
// 3. attacker claims: cdoEpoch.claimInstantWithdrawRequest() -> receives A
//    assert: strategy.instantWithdrawsRequestsByEpoch(attacker, epoch) == A   // STALE
//    assert: strategy.instantWithdrawClaimsByEpoch(epoch) == A + B            // STALE
// 4. attacker requests dust instant withdraw D (instantWithdrawsRequests[attacker] == D)
// 5. borrower defaults; manager calls _handleBorrowerDefault then finalizeDefaultRecovery(...)
//    totalBasis includes A + B again -> defaultRecoveryPrice is diluted
// 6. cdoEpoch.claimInstantWithdrawRequest() as attacker:
//    _claimDefaultedInstantWithdrawRequest pays (A + D-relevant basis) * recoveryPrice again
//    assert stolen: attackerUnderlying > deposited - losses
```

Confidence note: reachability of step 4 (a new `requestInstantWithdraw` between funding and default finalization) depends on CDO-side gating in `IdleCDOEpochVariant` (allow-instant-withdraw flags / epoch phase), which I could not fully verify in the remaining iterations; if new instant requests are blocked after funding but before finalization, the attacker can instead leave a small unclaimed portion of the original request (partial claims aren't supported, so they'd need two requests: claim request #1 fully, keep request #2's dust open) — either way the stale `instantWithdrawsRequestsByEpoch` basis survives.