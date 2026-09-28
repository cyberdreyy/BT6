### Title
Claimed instant-withdraw receipts stay in `instantWithdrawClaimsByEpoch`, inflating default-recovery basis and phantom prefunded reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
When a borrower defaults while any current-epoch instant-withdraw request is still unfunded, `finalizeDefaultRecovery` reads `instantWithdrawClaimsByEpoch[epochNumber]` — a cumulative counter that is *never decremented* when an instant receipt is claimed. The accounting therefore "reads" already-paid-out receipts as if they were still live claims: `defaultPendingClaimBasis()` counts them as defaulted claim basis, and `_defaultPrefundedInstantReserve()` counts the corresponding underlying as still held by the strategy. Both errors make `defaultRecoveryPrice` wrong and set `defaultRecoveryReserve` above the actual token balance, so recovery claims either over-pay early claimants or permanently revert for late claimants.

### Finding Description
`requestInstantWithdraw` accumulates `instantWithdrawClaimsByEpoch[currentEpoch] += _amount` for every request (line 372). `claimInstantWithdrawRequest` (lines 380–393) burns the receipt and pays out underlying but never reduces `instantWithdrawClaimsByEpoch`. The only place that counter is decremented is `_claimDefaultedInstantWithdrawRequest` (line 853), which runs only after default finalization.

At default finalization:

1. `defaultPendingClaimBasis()` (lines 644–649) adds the *full* `instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0`, including receipts already claimed and paid.
2. `_defaultPrefundedInstantReserve()` (lines 716–723) computes `instantBasis - pendingInstant` and treats it as underlying already held by the strategy — but the portion belonging to already-claimed receipts was transferred out to users and is not held.
3. `finalizeDefaultRecovery` (lines 685–692) then sets `reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve` and stores it as `defaultRecoveryReserve`, while `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` uses a `totalBasis` inflated by the same phantom claims.

Net effect: `defaultRecoveryReserve` exceeds `underlyingToken.balanceOf(address(this))`. Every `_transferDefaultRecovery` decrements the reserve and transfers real tokens, so early claimants are paid at a mispriced rate and the reserve is exhausted early; subsequent defaulted-receipt claims then revert on `defaultRecoveryReserve -= _amount` underflow or on `safeTransfer` failure. The unclaimed remainder is permanently locked: `_transferFundedClaim` (lines 899–905) explicitly forbids spending `balance - reserve < amount`, and there is no sweep path for the stuck reserve.

### Impact Explanation
- Over-/under-payment of recovery to pending receipt holders relative to the true pro-rata share.
- Permanent freezing of the tail of the recovery reserve: once the real balance is drained to satisfy the inflated reserve accounting, remaining claimants (normal pending receipts, unfunded instant receipts, post-default requests) can never claim, and the dust/shortfall cannot be recovered.
- Quantified loss: up to the full value of same-epoch instant withdrawals that were funded and claimed before the default — those tokens leave the contract while their claim basis and "prefunded" backing are double-counted.

### Likelihood Explanation
Requires only an unprivileged KYC-passing lender to request an instant withdraw that gets funded and claimed inside the same epoch, plus the borrower defaulting while another instant request is still unfunded (`pendingInstantWithdraws != 0` at `stopEpoch`). Both are ordinary protocol flows — partially funded instant queues are explicitly contemplated by the code comments at lines 636–640 — and the attacker needs no privileged role; the user simply claiming their own instant withdrawal in the same epoch creates the phantom basis.

### Recommendation
Decrement `instantWithdrawClaimsByEpoch[epochNumber]` inside `claimInstantWithdrawRequest` (or track a separate "claimed" counter subtracted in `defaultPendingClaimBasis`/`_defaultPrefundedInstantReserve`) so that only still-outstanding instant receipts contribute to default claim basis and prefunded reserve. Alternatively, snapshot the *unclaimed* instant basis at finalization by subtracting `instantWithdrawsRequests`-equivalent live receipts rather than the cumulative epoch counter.

### Proof of Concept
Foundry fork PoC sketch against `IdleCreditVault` + `IdleCDOEpochVariant`:

```solidity
// Setup: depositAA for userA and userB; APR > 0 mode; epoch running.
// 1. userA: cdoEpoch.requestInstantWithdraw(...) -> strategy.requestInstantWithdraw
//    instantWithdrawClaimsByEpoch[epoch] += amtA; pendingInstantWithdraws += amtA.
// 2. Borrower/CDO funds the instant queue: strategy.collectInstantWithdrawFunds(amtA + amtB_partial).
// 3. userA: cdoEpoch.claimInstantWithdrawRequest() -> paid amtA, but
//    instantWithdrawClaimsByEpoch[epoch] is NOT decremented.
// 4. userB: requestInstantWithdraw(amtB); only part of it is funded, so
//    pendingInstantWithdraws != 0 remains.
// 5. Warp past epochEndDate; borrower repays 0 -> cdoEpoch.stopEpoch(0,0) -> defaulted.
// 6. owner: cdoEpoch.finalizeDefault(recovered, manager).
//    - defaultPendingClaimBasis() = pendingWithdraws + instantWithdrawClaimsByEpoch[epoch]
//      which still contains amtA (already paid out).
//    - _defaultPrefundedInstantReserve() = instantBasis - pendingInstant counts amtA as
//      held reserve although it was transferred to userA.
// 7. assert: strategy.defaultRecoveryReserve() > underlying.balanceOf(strategy);
//    userB.claimInstantWithdrawRequest() or a pending-receipt claimWithdrawRequest reverts
//    (defaultRecoveryReserve underflow / safeTransfer failure) => recovery funds locked.
```

Expected result: `defaultRecoveryReserve` exceeds the strategy's real token balance by the claimed instant amount times the recovery ratio, and at least one legitimate defaulted claim permanently reverts.