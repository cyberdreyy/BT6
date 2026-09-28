### Title
Missing per-epoch instant-withdraw receipt updates in `claimInstantWithdrawRequest` leave stale claim basis that corrupts default-recovery pricing - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary

The external bug class is "functions update the primary position but omit the corresponding secondary/per-user accounting update". The strongest analog in this codebase is in `IdleCreditVault.claimInstantWithdrawRequest`: when a user claims a funded instant withdrawal, the function clears `instantWithdrawsRequests[_user]` and burns the receipt, but never clears `instantWithdrawsRequestsByEpoch[_user][epoch]` and never decrements the epoch aggregate `instantWithdrawClaimsByEpoch[epoch]`. Those per-epoch fields are only cleared in the post-default path `_claimDefaultedInstantWithdrawRequest`. Because `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` read `instantWithdrawClaimsByEpoch[epochNumber]` directly, a same-epoch borrower default after partial instant funding double-counts already-claimed receipts: once as (phantom) prefunded reserve and once as outstanding claim basis. The result is an inflated `defaultRecoveryPrice`/`reserveAmount` computed against tokens that already left the contract, so later recovery claimants' `_transferDefaultRecovery` calls revert when the real balance is exhausted — permanent freezing of recovery funds / insolvency for the last claimants.

### Finding Description

`requestInstantWithdraw` records three pieces of state for each receipt: `instantWithdrawsRequests[user]`, `instantWithdrawsRequestsByEpoch[user][epochNumber]`, and the epoch total `instantWithdrawClaimsByEpoch[epochNumber]` (lines 366-374). During `startEpoch`, the CDO calls `collectInstantWithdrawFunds(min(pendingInstant, totUnderlyings))`, which reduces `pendingInstantWithdraws` but does not touch the per-epoch claim aggregates. When funding is partial, `pendingInstantWithdraws` stays non-zero while some underlying is already sitting in the strategy.

A user whose receipt was funded calls `claimInstantWithdrawRequest` (lines 380-393). It burns the receipt tokens and pays out, but:

- does not decrement `instantWithdrawClaimsByEpoch[currentEpoch]`,
- does not clear `instantWithdrawsRequestsByEpoch[user][currentEpoch]`.

Compare with `_claimDefaultedInstantWithdrawRequest` (lines 842-856), which is the only path that correctly decrements both aggregates. This mirrors the external finding exactly: the "success" path forgets the bookkeeping that the "default" path maintains.

If the borrower subsequently defaults in the same epoch (e.g. via a failed `getFundsFromBorrower` in `stopEpoch`, leading to `_handleBorrowerDefault`), `finalizeDefault` → `finalizeDefaultRecovery` computes:

- `defaultPendingClaimBasis()` adds `instantWithdrawClaimsByEpoch[epochNumber]` when `pendingInstantWithdraws != 0` — this still includes the already-claimed user's amount.
- `_defaultPrefundedInstantReserve()` computes `instantBasis - pendingInstant`, which is also inflated by the claimed amount, so `reserveAmount` credits the strategy with underlying it no longer holds.

Both the numerator (`reserveAmount` → `defaultRecoveryPrice`) and denominator (`totalBasis`) are wrong: the claimed user is counted twice. Recovery claims are then paid from `defaultRecoveryReserve`, which was set from the phantom balance. Early claimants withdraw real tokens; once the actual balance is consumed, `_transferDefaultRecovery` reverts for remaining claimants (`defaultRecoveryReserve -= _amount` underflows / transfer fails), permanently freezing the tail of the recovery.

### Impact Explanation

Direct loss of recovery funds for defaulted-epoch claimants, quantified as the sum of instant receipts that were claimed between funding and finalization: that amount is counted as reserve that does not exist. In a concrete setup — pendingInstant = 100, CDO cash covers 60, user A claims 60, borrower defaults on the remaining 40 — `finalizeDefaultRecovery` treats 100 as instant basis and 40 as "prefunded reserve" even though the 60 paid to A already left. If `_recoveredAmount = 50`, `recoveryPrice` and `defaultRecoveryReserve` are computed as if the strategy held `50 + 40`, i.e. claims totalling `(100 * price)` are authorized against only `50 + 40 - 60` real tokens; the final claimants' transactions revert, permanently locking their entitled recovery share in the contract.

### Likelihood Explanation

Requires a specific but plausible sequence: (1) instant withdrawals enabled (`disableInstantWithdraw = false`, APR drop triggers requests — a normal protocol state), (2) partial instant funding at `startEpoch` (CDO liquidity < pendingInstant, common when most TVL is lent out), (3) at least one funded claimant redeems, (4) borrower defaults mid-epoch before the instant queue clears, and (5) owner/manager finalizes recovery. None of the attacker steps require privileges: the "attacker" is simply a normal whitelisted lender claiming an honestly funded receipt; the accounting error does the damage. Guards do not help: `_ensureDefaultRecoveryInitialized` only gates on `pendingInstantWithdraws`, `defaultRecoveryFinalized` only runs once, and no check reconciles `instantWithdrawClaimsByEpoch` with still-unclaimed receipts.

### Recommendation

In `claimInstantWithdrawRequest`, mirror the cleanup performed by `_claimDefaultedInstantWithdrawRequest`: after burning, decrement `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and `instantWithdrawClaimsByEpoch[epochNumber]` by the claimed amount (tracking the user's request epoch per receipt, e.g. a `lastInstantWithdrawRequest` marker analogous to `lastWithdrawRequest`), so that `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` only ever observe still-outstanding claims. Alternatively derive the funded portion from `pendingInstantWithdraws` vs. live (uncleared) per-epoch receipts rather than a never-decremented aggregate.

### Proof of Concept

Foundry PoC against `test/foundry/IdleCreditVault.t.sol` infrastructure:

```solidity
function testClaimedInstantReceiptInflatesDefaultRecovery() external {
    // setup: deposits, APR drop so instant path is enabled, epoch E0 running
    // 1. users A and B call requestWithdraw -> requestInstantWithdraw for 60 and 40
    //    (tranche tokens burned, receipts minted, pendingInstantWithdraws = 100,
    //     instantWithdrawClaimsByEpoch[E0] = 100)
    // 2. epoch ends; owner calls startEpoch for E0->E1 with only 60 underlying liquid
    //    collectInstantWithdrawFunds(60): pendingInstantWithdraws = 40,
    //    instantWithdrawClaimsByEpoch[E0] still = 100
    //    NOTE: epochNumber increments on stopEpoch deposit(); use same-epoch default
    //    via getInstantWithdrawFunds failure or a defaulted stopEpoch in the request epoch
    // 3. A calls claimInstantWithdrawRequest() -> receives 60,
    //    instantWithdrawsRequests[A] = 0 but instantWithdrawClaimsByEpoch stays 100
    // 4. borrower defaults (getFundsFromBorrower reverts in stopEpoch)
    // 5. owner calls finalizeDefault(recovered, recoverySource)
    //    defaultPendingClaimBasis() = pendingWithdraws + 100 (includes A's claimed 60)
    //    _defaultPrefundedInstantReserve()  = 100 - 40 = 60 counted as reserve
    //    reserveAmount = recovered + 60(phantom) + defaultRecoveryReserve
    // 6. B and defaulted-receipt claimants call claimWithdrawRequest/claimInstantWithdrawRequest
    // assert: last claimant's _transferDefaultRecovery reverts (insufficient real balance)
    //         while defaultRecoveryReserve still shows unallocated "recovery" dust locked forever
}
```

Expected result: `defaultRecoveryPrice` is inflated and `defaultRecoveryReserve` overstates real holdings by exactly A's claimed amount; the sum of successful recovery payouts is less than the entitled total, leaving tokens permanently stranded in the strategy.