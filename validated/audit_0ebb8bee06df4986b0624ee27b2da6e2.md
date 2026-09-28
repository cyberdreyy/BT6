### Title
Post-default instant withdraw receipts reuse the defaulted epoch bucket and double-spend `defaultRecoveryReserve` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`requestWithdraw` has an explicit post-default path (`postDefaultRequests`) that isolates new receipts from the finalized default recovery accounting, but `requestInstantWithdraw` has no such guard. A user who opens an instant-withdraw request after `finalizeDefaultRecovery` has their receipt written into `instantWithdrawsRequestsByEpoch[user][epochNumber]` and `instantWithdrawClaimsByEpoch[epochNumber]`. If `epochNumber` still equals `defaultRecoveryEpoch` (the strategy only increments `epochNumber` at `stopEpoch`, which no longer runs after default), the new unfunded receipt is indistinguishable from defaulted-epoch receipts in `_claimDefaultedInstantWithdrawRequest`. It is then paid `claimBasis * defaultRecoveryPrice` out of `defaultRecoveryReserve`, which was sized only for receipts that existed at finalization — the same "use of uninitialized/stale slot" pattern as the CVE's uninitialized `r_symbol` being passed to `free()`.

### Finding Description
In `requestWithdraw` (lines 247-257) the code checks `defaultRecoveryFinalized` and routes new requests into `postDefaultRequests`, minting a receipt already haircut at the post-default virtual price and never touching the reserve accounting. `requestInstantWithdraw` (lines 356-375) calls `_onlyIdleCDO` and `_ensureDefaultRecoveryInitialized`, then unconditionally increments `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` and `instantWithdrawClaimsByEpoch[currentEpoch]` where `currentEpoch = epochNumber`. Since default finalization does not advance `epochNumber`, `currentEpoch == defaultRecoveryEpoch`. On claim, `claimInstantWithdrawRequest` (lines 380-393) runs `_claimDefaultedInstantWithdrawRequest` first, which reads `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]` — now including the new request — decrements `instantWithdrawClaimsByEpoch[defaultEpoch]` below its finalized value, and pays out via `_transferDefaultRecovery` against `defaultRecoveryReserve`, a reserve funded only for pre-finalization claims. The receipt tokens minted to the user at request time make the claim executable.

### Impact Explanation
Each post-default instant request of `X` drains `X * defaultRecoveryPrice` from `defaultRecoveryReserve` that was never backed by recovery funding. This is a double-spend of the recovery reserve: legitimate defaulted-epoch claimants are left underfunded (claims revert on reserve depletion or underlying shortfall), and the attacker extracts unbacked underlying at the recovery ratio. Loss is bounded by the total post-default instant requests a user can open, i.e. up to their tranche balance, quantified as `min(postDefaultRequestVolume, reserveBalance) * defaultRecoveryPrice` stolen from honest claimants.

### Likelihood Explanation
Requires: instant withdraws enabled (`setInstantWithdrawParams`), a borrower default followed by `finalizeDefaultRecovery`, and the CDO allowing `requestInstantWithdraw` while `defaulted` (the vault side imposes no block; verification of the CDO-level gating on this path after default was not fully completed). Attacker is an ordinary KYC-passing tranche holder — permitted by scope. The analogous `requestWithdraw` path was explicitly fixed with `postDefaultRequests`, indicating the developers knew this isolation was needed but missed the instant variant.

### Recommendation
In `requestInstantWithdraw`, when `defaultRecoveryFinalized` is true either revert `NotAllowed()`, or mirror `requestWithdraw` by routing the amount into `postDefaultRequests`/a dedicated post-default instant bucket instead of `instantWithdrawsRequestsByEpoch[currentEpoch]`. Alternatively, increment `epochNumber` during default finalization so new receipts can never collide with `defaultRecoveryEpoch`.

### Proof of Concept
```solidity
// Foundry fork PoC sketch (test/foundry harness)
// 1. Enable instant withdraws; user deposits AA; epoch runs.
// 2. Honest users open instant + normal withdraw requests in epoch N.
// 3. Borrower defaults -> _handleBorrowerDefault -> finalizeDefaultRecovery:
//    epochNumber == N == defaultRecoveryEpoch, reserve sized for existing claims.
// 4. Attacker (fresh AA holder) calls cdoEpoch.requestInstantWithdraw(amount).
//    strategy.requestInstantWithdraw mints receipt, writes
//    instantWithdrawsRequestsByEpoch[attacker][N] += amount (no default guard).
// 5. Attacker calls cdoEpoch.claimInstantWithdrawRequest():
//    _claimDefaultedInstantWithdrawRequest pays amount * defaultRecoveryPrice
//    from defaultRecoveryReserve.
// 6. Honest claimant's defaulted receipt then reverts/underpays — reserve drained
//    by `attackerAmount * defaultRecoveryPrice` beyond finalized accounting.
assertLt(underlyingNeededForHonestClaims, defaultRecoveryReserveAfterAttack);
```

Caveat: the exploit requires the CDO epoch variant to permit `requestInstantWithdraw` after default; if `defaulted` blocks that entry point, the issue downgrades to a latent inconsistency rather than exploitable theft. That gating line could not be fully confirmed within the available context.