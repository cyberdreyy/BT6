### Title
Unfunded instant-withdraw receipts from pre-default epochs escape the recovery haircut and are paid at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` tracks instant-withdraw receipt basis per request epoch (`instantWithdrawsRequestsByEpoch[user][epoch]`) but only ever applies the default recovery haircut to receipts recorded in `defaultRecoveryEpoch` (`instantWithdrawClaimsByEpoch[epochNumber]` at finalization). An attacker whose instant-withdraw request was created in an earlier epoch and left unfunded (`pendingInstantWithdraws > 0`) is never included in the recovery claim basis, never haircutted, and after `finalizeDefaultRecovery` can claim the full aggregate `instantWithdrawsRequests[user]` at par through `_transferFundedClaim` against the strategy's non-reserve balance.

### Finding Description
The analog of the out-of-bounds read is a lookup keyed on a single epoch index that reads only a slice of a multi-epoch queue:

- `requestInstantWithdraw` records basis under `instantWithdrawsRequestsByEpoch[user][epochNumber]` and increments the global `pendingInstantWithdraws` (IdleCreditVault.sol:367-374).
- `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` — only the *current* epoch's instant claims — whenever `pendingInstantWithdraws != 0` (IdleCreditVault.sol:644-648). Receipts requested in earlier epochs that are still unfunded contribute to `pendingInstantWithdraws` but are missing from the claim basis.
- `_defaultPrefundedInstantReserve` likewise compares `instantWithdrawClaimsByEpoch[epochNumber]` to `pendingInstantWithdraws` (IdleCreditVault.sol:716-722), so a stale-epoch receipt distorts the prefunded-reserve estimate as well.
- After finalization, `claimInstantWithdrawRequest` only clears the `defaultRecoveryEpoch` slice via `_claimDefaultedInstantWithdrawRequest` (IdleCreditVault.sol:842-856), then burns the *aggregate* `instantWithdrawsRequests[user]` and pays it at par via `_transferFundedClaim` (IdleCreditVault.sol:387-392, 897-907).

`_transferFundedClaim` only checks `balance - reserve >= amount`. A defaulted vault still holds non-reserve underlying: borrower-funded pending withdrawals collected at prior `stopEpoch`s are in the contract balance but are not part of `defaultRecoveryReserve` (reserve = `_recoveredAmount + prefundedReserve + priorReserve`, IdleCreditVault.sol:685-691), while those same pending receipts are haircutted to `defaultRecoveryPrice` on claim. The resulting haircut surplus is spendable by the stale-epoch instant receipt at 100 cents on the dollar.

### Impact Explanation
Broken invariant: fair loss socialization / one receipt one payout. Two users with economically identical unfunded instant receipts receive different treatment solely because one was requested one epoch earlier: the default-epoch receipt is paid `claimBasis * defaultRecoveryPrice / 1e18` while the stale-epoch receipt is paid in full. The par payment drains underlying that should remain for recovery claimants and funded pending receipts, i.e. direct theft of residual vault value quantified as `(1 - defaultRecoveryPrice) * staleReceiptAmount`, plus possible permanent freezing if the drain later causes `_transferDefaultRecovery` / `_transferFundedClaim` to revert for honest claimants when the free balance is exhausted.

### Likelihood Explanation
Requirements are all unprivileged: the attacker only needs to (1) hold tranche/CF tokens and call `requestInstantWithdraw` during epoch N-1, (2) have that request remain partially or wholly unfunded at `startEpoch` (instant queue funded only up to available liquidity), (3) wait for a borrower default in epoch N and `finalizeDefaultRecovery`, (4) call `claimInstantWithdrawRequest`. No privileged action by the attacker is needed — sequencing around honest borrower/manager calls only. No existing guard stops it: `defaultRecoveryFinalized` gating routes the claim but only clears the default-epoch slice, and the reserve guard in `_transferFundedClaim` passes whenever any non-reserve balance exists.

### Recommendation
In `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve`, account for *all* unfunded instant receipts, not only `instantWithdrawClaimsByEpoch[epochNumber]` — e.g. track a global `totalInstantClaimBasis` alongside `pendingInstantWithdraws`, or persist per-epoch instant claims across epochs instead of treating the queue as current-epoch-only. Correspondingly, `_claimDefaultedInstantWithdrawRequest` should iterate/aggregate all of a user's outstanding instant receipt epochs (or store a per-user total claim basis) and apply `defaultRecoveryPrice` to the whole amount, clearing `instantWithdrawsRequests[user]` fully.

### Proof of Concept
Foundry fork outline (mirroring `test/foundry/IdleCreditVault.t.sol` setup with `cdoEpoch`/`proxiedStrategy`):

1. Deposit via `idleCDO.depositAA`/`_depositWithUser` for attacker and a victim so the vault has TVL.
2. `manager` calls `startEpoch` (epoch N-1). Attacker calls `cdoEpoch.requestInstantWithdraw(amount)`; arrange liquidity so `collectInstantWithdrawFunds` funds only part → `pendingInstantWithdraws > 0` and `instantWithdrawsRequestsByEpoch[attacker][N-1] = amount`.
3. Warp past `epochEndDate`, `stopEpoch` runs epoch N-1 without funding the instant remainder; epoch N starts and runs.
4. Borrower defaults: `cdoEpoch._handleBorrowerDefault` / `finalizeDefault` then `finalizeDefaultRecovery(recovered, source)` with `recovered < basis`. `defaultRecoveryEpoch = N`; attacker's basis was never added to `defaultPendingClaimBasis` because `instantWithdrawClaimsByEpoch[N]` excludes it.
5. Attacker calls `cdoEpoch.claimInstantWithdrawRequest()`. `_claimDefaultedInstantWithdrawRequest` reads `instantWithdrawsRequestsByEpoch[attacker][N] == 0` and returns; the function then burns the aggregate `instantWithdrawsRequests[attacker]` and `_transferFundedClaim` transfers the full `amount` at par from non-reserve balance.
6. Assert `underlying.balanceOf(attacker)` increased by the full `amount` while an identical epoch-N receipt holder only receives `amount * defaultRecoveryPrice / 1e18`, and that remaining reserve/free balance is insufficient to pay all recovery claims.

Note: I was not able to fully trace `IdleCDOEpochVariant`'s default-finalization callers to confirm every amount flowing into `instantWithdrawClaimsByEpoch`/`pendingInstantWithdraws`; if `collectInstantWithdrawFunds` guarantees the per-epoch mapping always equals the unfunded remainder (i.e. instant requests can never remain pending across an epoch boundary), the stale-epoch window would not exist and this would reduce to a no-finding. That guarantee is not evident in the strategy code inspected.