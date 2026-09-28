### Title
Instant-withdraw claims never clean up per-epoch receipt accounting, corrupting default-recovery payouts and permanently freezing claims - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`requestInstantWithdraw` records per-epoch receipt basis in `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]`, but the normal funded-claim path `claimInstantWithdrawRequest` only zeroes the aggregate `instantWithdrawsRequests[_user]` and never clears the per-epoch entries. Only the post-default path `_claimDefaultedInstantWithdrawRequest` decrements them. When a borrower defaults in the same epoch in which a user already claimed a funded instant withdrawal, the stale per-epoch basis is double-counted: the user's default claim reverts (underflow/insufficient receipt balance), and the epoch-level counter used to size the unfunded-receipt reserve is inflated, leaving the recovery reserve insolvent for genuinely pending claimants.

### Finding Description
In `IdleCreditVault.requestInstantWithdraw` (lines 356–375), each request stores:

- `instantWithdrawsRequests[_user] += _amount`
- `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount`
- `instantWithdrawClaimsByEpoch[currentEpoch] += _amount`
- `pendingInstantWithdraws += _amount`

The comments (lines 368–370) state the per-epoch mappings exist so default finalization can distinguish "default-epoch pending instant receipts" from "old funded instant receipts". However, the funded claim path `claimInstantWithdrawRequest` (lines 380–393) does only:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

It never clears `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` and never decrements `instantWithdrawClaimsByEpoch[currentEpoch]` — the orphaned partial state is left on "disk", mirroring the Multer aborted-upload cleanup bug.

When `defaultRecoveryFinalized` is set, `claimInstantWithdrawRequest` routes through `_claimDefaultedInstantWithdrawRequest` (lines 842–856), which computes `claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch]` — i.e., the *cumulative* epoch basis including receipts already paid at par. It then executes:

```solidity
instantWithdrawsRequests[_user] -= claimBasis;   // underflows if re-request < old basis
pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis;
instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis;
_burn(_user, claimBasis);                        // reverts: receipt tokens already burned
```

Two failure modes result:

1. User claims a funded instant withdrawal mid-epoch, then submits a *new* instant withdraw request in the same epoch. If the new request is smaller than the old basis, `instantWithdrawsRequests[_user] -= claimBasis` underflows (or `_burn(_user, claimBasis)` reverts on insufficient receipt balance, since the old receipts were already burned). The user can never claim — permanent freezing of their recovery entitlement.
2. Even if the subtraction succeeds (new request ≥ old basis), the user receives `defaultRecoveryPrice` haircut recovery on *already-paid* amounts (double payout against the reserve), while `pendingInstantWithdraws` is decremented by the inflated basis — draining `defaultRecoveryReserve` and leaving later genuine claimants unfunded (insolvency in recovery distribution). The epoch-level `instantWithdrawClaimsByEpoch[defaultEpoch]` used to size the unfunded reserve during finalization is also inflated by paid claims, skewing `defaultRecoveryPrice`/`defaultRecoveryReserve`.

Existing guards do not stop this: the cleanup omission is unconditional, nothing validates `instantWithdrawsRequestsByEpoch` against `instantWithdrawsRequests` before subtracting, and the instant-claim path is reachable mid-epoch (see `test/foundry/IdleCDOEpochQueue.t.sol` where instant claims are available in the current epoch after `instantDelay`).

### Impact Explanation
Direct fund impact with quantified loss: recovery reserve is either permanently frozen for affected users (claim always reverts → 100% of their pending instant receipt locked) or overpaid to double-counting users at the expense of other claimants (loss = sum of already-funded instant claims still recorded in the default epoch). This is a solvency/fair-payout invariant break ("one receipt one payout"), not a gas-only DoS.

### Likelihood Explanation
Requires only unprivileged actions: a KYC-passed tranche holder requests an instant withdrawal, claims it once funded, re-requests a smaller amount in the same epoch, then the (honest) borrower defaults. The attacker purely sequences ordinary user calls around honest privileged calls. Instant-withdraw pools and borrower defaults are both supported production paths, and the window is the full epoch duration.

### Recommendation
In `claimInstantWithdrawRequest`, when a funded claim succeeds, clear the per-epoch accounting in the same way the default path does: reduce `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` by the claimed amount. Since funded claims may span multiple request epochs, consider tracking the request epoch per claim or clearing per-epoch basis at funding time in `collectInstantWithdrawFunds`.

### Proof of Concept
```solidity
// Foundry fork PoC outline (instant-withdraw enabled vault):
// 1. Epoch N running. Attacker (KYC'd LP) calls cdoEpoch.requestInstantWithdraw(100e6).
// 2. Manager stops epoch; CDO calls collectInstantWithdrawFunds(100e6) -> reserve funded.
// 3. New epoch N+1 starts (still same strategy epochNumber? No —
//    instant receipts use epochNumber at request time; ensure the request and
//    the default share one epochNumber window). After instantDelay, attacker calls
//    claimInstantWithdrawRequest() -> receives 100e6.
//    BUG: instantWithdrawsRequestsByEpoch[attacker][N] is still 100e6 and
//    instantWithdrawClaimsByEpoch[N] is still 100e6.
// 4. Attacker calls requestInstantWithdraw(10e6) again in the same epoch.
//    instantWithdrawsRequests[attacker] = 10e6; per-epoch basis = 110e6.
// 5. Borrower defaults; manager/owner finalize -> defaultRecoveryFinalized = true,
//    defaultInstantWithdrawsFinalized = true, defaultRecoveryEpoch = N.
// 6. Attacker calls claimInstantWithdrawRequest():
//    _claimDefaultedInstantWithdrawRequest reads claimBasis = 110e6,
//    instantWithdrawsRequests[attacker] (10e6) - 110e6 underflows -> revert.
//    Attacker's 10e6 recovery is permanently frozen; pendingInstantWithdraws and
//    instantWithdrawClaimsByEpoch[N] remain corrupted, so other users' recovery
//    accounting is insolvent by up to the 100e6 phantom basis.
```

Confidence note: the omitted cleanup in `claimInstantWithdrawRequest` (lines 380–393) versus the default-only decrement in `_claimDefaultedInstantWithdrawRequest` (lines 847–853) is verified directly in the source. The exact reserve-sizing consumer inside the default-finalization path was not fully read, but the underflow/burn-revert and phantom-basis payout in `_claimDefaultedInstantWithdrawRequest` alone are sufficient for the permanent-freezing/insolvency impact described.