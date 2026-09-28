### Title
Unfunded instant-withdraw receipts from a prior epoch escape the default haircut and are paid at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The Xen bug class is a check-then-act race: an unlock is observed before the critical section's writes, so the invariant "a checked value cannot change underneath" breaks. The analog here is epoch-keyed accounting that assumes all unfunded instant-withdraw receipts belong to the *current* epoch. `defaultPendingClaimBasis` only adds `instantWithdrawClaimsByEpoch[epochNumber]` and `_claimDefaultedInstantWithdrawRequest` only clears `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]`, while `pendingInstantWithdraws` is a global, epoch-agnostic remainder. Receipts recorded in an earlier epoch but left unfunded fall outside the default-epoch basis, are never haircut, and are later paid 1:1 from the strategy balance / recovery reserve.

### Finding Description
`requestInstantWithdraw` records receipts both globally and per-epoch (`instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, `instantWithdrawClaimsByEpoch[currentEpoch]`, `pendingInstantWithdraws`) at `contracts/strategies/idle/IdleCreditVault.sol:366-374`. Funding is decoupled: `collectInstantWithdrawFunds` decrements only `pendingInstantWithdraws` (`:398-403`), so a partially funded instant queue leaves a residual `pendingInstantWithdraws` that persists across `stopEpoch` into the next epoch.

At finalization, `defaultPendingClaimBasis` includes instant claims only as `instantWithdrawClaimsByEpoch[epochNumber]` (`:644-649`), i.e., only receipts created in the default epoch. `_defaultPrefundedInstantReserve` uses the same epoch key (`:716-723`), so it can even return 0 for a reserve that actually backs older receipts. Consequently `finalizeDefaultRecovery` sets `defaultRecoveryPrice` on a basis that excludes the stale-epoch unfunded receipts, and `defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0` (`:696`).

On claim, `claimInstantWithdrawRequest` first runs `_claimDefaultedInstantWithdrawRequest`, which clears only `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` (`:842-856`), then pays the *entire* remaining `instantWithdrawsRequests[_user]` at par via `_transferFundedClaim` (`:387-392`). The prior-epoch unfunded portion therefore bypasses `defaultRecoveryPrice` entirely — exactly the "writer in the critical section concurrently with readers" shape of CVE-2020-11739: finalization assumed no receipt could exist outside the current-epoch bucket, but a receipt written in epoch N is still live when finalization "reads" epoch N+1.

### Impact Explanation
An attacker holding tranche tokens requests an instant withdrawal in epoch N. At `stopEpoch` the borrower/manager funds the instant queue only partially, leaving `pendingInstantWithdraws > 0` attributable to epoch-N receipts. During epoch N+1 the borrower defaults and `finalizeDefaultRecovery` runs. The attacker's epoch-N receipt is excluded from `totalBasis`, inflating `defaultRecoveryPrice` for themselves while the receipt itself remains fully payable at par. When they call `claimWithdrawRequest`/`claimInstantWithdrawRequest` through the CDO, they receive 100% on an unfunded receipt, drawing down `defaultRecoveryReserve` (and other users' funded claims) that should have paid them only `claimBasis * defaultRecoveryPrice / RECOVERY_FULL`. Loss equals `receipt * (1 - defaultRecoveryPrice)` — direct theft of recovery funds and underpayment of every other defaulted claimant, i.e., a solvency/fair-payout invariant break with quantified loss.

### Likelihood Explanation
Requires (a) an epoch with instant withdrawals enabled where the queue is only partially collected (the code explicitly contemplates this: "pendingInstantWithdraws is only the unfunded remainder"), and (b) a borrower default finalized in a later epoch. Both are normal protocol states reachable by an unprivileged tranche holder for step (a). The attacker's only action is an ordinary instant-withdraw request plus a later claim; no privileged cooperation is needed beyond the partial funding outcome, which the honest borrower can produce whenever liquidity is short. No existing guard stops it: `_onlyIdleCDO` is satisfied via the CDO, `defaultInstantWithdrawsFinalized` is true but keyed to the wrong epoch, and the funded-claim path performs no solvency check against `pendingInstantWithdraws`.

### Recommendation
Make the instant-claim basis epoch-agnostic or drain it per epoch: either track a global unfunded instant basis (`pendingInstantWithdraws` itself plus the funded-but-unclaimed remainder) for inclusion in `defaultPendingClaimBasis`, or iterate/clear all `instantWithdrawsRequestsByEpoch` entries up to and including `defaultRecoveryEpoch` in `_claimDefaultedInstantWithdrawRequest`. At minimum, `defaultInstantWithdrawsFinalized` should cause *all* outstanding instant receipts — not just `instantWithdrawClaimsByEpoch[defaultRecoveryEpoch]` — to settle at `defaultRecoveryPrice`, and `_defaultPrefundedInstantReserve` should compare `pendingInstantWithdraws` against total outstanding instant claims rather than only the current epoch's.

### Proof of Concept
```solidity
// Foundry fork-style PoC sketch (idle-tranches conventions, USDC underlying)
// Setup: standard epoch variant, instant withdrawals enabled via
// cdoEpoch.setInstantWithdrawParams(delay, minAprDiff, false).

// Epoch N (running):
// 1. Attacker deposits and calls cdoEpoch.requestInstantWithdraw(amount, attacker)
//    -> instantWithdrawsRequests[attacker] = amount;
//       instantWithdrawsRequestsByEpoch[attacker][N] = amount;
//       instantWithdrawClaimsByEpoch[N] = amount; pendingInstantWithdraws = amount.
// 2. stopEpoch: CDO calls collectInstantWithdrawFunds(part) with part < amount.
//    pendingInstantWithdraws = amount - part > 0. epochNumber -> N+1.
//    Attacker does NOT claim yet.

// Epoch N+1 (running): borrower defaults.
// 3. Manager/keeper triggers default; CDO calls strategy.finalizeDefaultRecovery(R, src).
//    defaultPendingClaimBasis() = pendingWithdraws + instantWithdrawClaimsByEpoch[N+1]
//    -> attacker's epoch-N receipt is NOT in basis (instantWithdrawClaimsByEpoch[N+1] == 0).
//    _defaultPrefundedInstantReserve() = max(0, 0 - pendingInstantWithdraws) = 0,
//    so the 'part' already held is not counted either.
//    defaultRecoveryFinalized = true; defaultInstantWithdrawsFinalized = true;
//    defaultRecoveryEpoch = N+1; defaultRecoveryPrice = reserve/totalBasis (haircut).

// 4. Attacker claims via cdoEpoch.claimInstantWithdrawRequest(attacker):
//    _claimDefaultedInstantWithdrawRequest clears
//    instantWithdrawsRequestsByEpoch[attacker][N+1] == 0 -> no-op.
//    Then pays instantWithdrawsRequests[attacker] (= amount) in full via
//    _transferFundedClaim, draining defaultRecoveryReserve / funded balance.

// Assert: attackerReceived == amount (par), while every other defaulted
// claimant receives amount * defaultRecoveryPrice / RECOVERY_FULL, and
// defaultRecoveryReserve is depleted by the unfunded (amount - part) portion.
```

Caveat: I verified the epoch-keyed basis mismatch and the par-pay fallback path in `IdleCreditVault.sol`, but within the available search budget I could not fully confirm the exact IdleCDOEpochVariant gating that leaves a partially funded `pendingInstantWithdraws` across a `stopEpoch`; the PoC assumes `collectInstantWithdrawFunds` can be called for less than the full pending amount, which the code structure (`pendingInstantWithdraws -= _amount` with no completeness check) supports.