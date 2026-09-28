### Title
Claimed instant-withdraw receipts are never removed from `instantWithdrawClaimsByEpoch`/`pendingInstantWithdraws`, inflating default-recovery basis and underfunding the reserve - (contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The upstream bug class is a missing cleanup: a fix removed the only call that freed a rule, leaking every allocated object. The vault analog is `claimInstantWithdrawRequest`: when a user collects a funded instant withdrawal, the function zeroes `instantWithdrawsRequests[_user]` and burns the receipt, but never "frees" the three allocations made in `requestInstantWithdraw` — `instantWithdrawsRequestsByEpoch[_user][epoch]`, `instantWithdrawClaimsByEpoch[epoch]`, and the user's share of `pendingInstantWithdraws`. Those counters are only decremented at funding time (`collectInstantWithdrawFunds`) or on the defaulted-claim path (`_claimDefaultedInstantWithdrawRequest`). A partially-funded instant queue therefore leaves permanently stale basis that `defaultPendingClaimBasis()` and `_defaultPrefundedInstantReserve()` later treat as real debt and real collateral during `finalizeDefaultRecovery`.

### Finding Description
`requestInstantWithdraw` records three pieces of state (IdleCreditVault.sol:356-375):
- `instantWithdrawsRequests[_user] += _amount`
- `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount`
- `instantWithdrawClaimsByEpoch[currentEpoch] += _amount`
- `pendingInstantWithdraws += _amount`

On the normal (non-default) claim path, `claimInstantWithdrawRequest` only clears `instantWithdrawsRequests[_user]` and pays out (IdleCreditVault.sol:380-393). It does **not** touch `instantWithdrawsRequestsByEpoch`, `instantWithdrawClaimsByEpoch`, or `pendingInstantWithdraws`. The only place those aggregates are reduced for a paid user is `_claimDefaultedInstantWithdrawRequest` (lines 847-853), which runs exclusively when `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized`.

If a default is finalized in the same epoch while `pendingInstantWithdraws != 0`:
- `defaultPendingClaimBasis()` adds `instantWithdrawClaimsByEpoch[epochNumber]` (line 647), which still includes amounts already paid out to users who claimed early.
- `_defaultPrefundedInstantReserve()` (lines 716-722) computes `instantBasis - pendingInstant` as "already-held underlying reserved for those claims" — but part of that underlying has already left the strategy through `claimInstantWithdrawRequest`.

So `reserveAmount` in `finalizeDefaultRecovery` (line 686) credits the strategy with funds it no longer holds, and `totalBasis` is inflated by claims that no longer exist. The result is a `defaultRecoveryPrice` that dilutes every legitimate claimant, and a `defaultRecoveryReserve` that exceeds the actual token balance — the reserve is an IOU against a missing allocation, exactly like the leaked kernel objects that were still referenced but never backed.

Sequence (running epoch E, instant mode enabled):
1. Alice and Bob each `requestWithdraw` via instant mode for 100e6 → `pendingInstantWithdraws = 200e6`, `instantWithdrawClaimsByEpoch[E] = 200e6`.
2. After `instantWithdrawDelay`, manager calls `getInstantWithdrawFunds`; borrower partially funds 120e6 → `collectInstantWithdrawFunds(120e6)`, `pendingInstantWithdraws = 80e6`, strategy balance = 120e6.
3. Alice calls `claimInstantWithdrawRequest` → paid 100e6 in full (balance check in `_transferFundedClaim` passes since reserve is 0). `pendingInstantWithdraws` still 80e6, `instantWithdrawClaimsByEpoch[E]` still 200e6. Strategy balance = 20e6.
4. Borrower defaults; owner calls `finalizeDefault`. `defaultPendingClaimBasis` counts all 200e6 of instant claims; `_defaultPrefundedInstantReserve` reports 120e6 as already-held, though only 20e6 remains.
5. `defaultRecoveryReserve` is booked ~100e6 above real balance and `recoveryPrice` is computed over a ~100e6-larger basis. Bob's recovery claim and active-LP redemptions either receive less than the stated price or revert on `safeTransfer` when the reserve is exhausted — permanently freezing the tail of the recovery fund.

### Impact Explanation
Direct loss and permanent freezing of recovery funds: the phantom 100e6 basis (a) lowers `defaultRecoveryPrice`, transferring value away from honest defaulted receipt holders and active tranche holders, and (b) overstates `defaultRecoveryReserve`, so the last claimants' `_transferDefaultRecovery` reverts on insufficient balance — their share is permanently unclaimable. Magnitude equals the total instant claims paid while `pendingInstantWithdraws` remained non-zero in the default epoch (bounded by the instant queue size, i.e., up to the full instant-receipt bucket of the defaulted epoch).

### Likelihood Explanation
Requires: instant withdrawals enabled (`setInstantWithdrawParams`), at least two instant requests in one epoch, partial funding via `getInstantWithdrawFunds` (the code explicitly supports "partially prefunded instant requests"), one user claiming before the queue is fully funded, and a same-epoch default followed by `finalizeDefault`. All actors are unprivileged users plus honest manager/owner/borrower sequencing; no malicious privileged role is needed. The attacker (Alice) can also simply be an opportunistic user — the bug is deterministic once the state arises.

### Recommendation
Mirror the fix for the kernel bug — restore the missing free. In `claimInstantWithdrawRequest`, also delete `instantWithdrawsRequestsByEpoch[_user]` entries that have been funded, decrement `instantWithdrawClaimsByEpoch[epoch]` and `pendingInstantWithdraws` by the paid amount, or better: settle instant claims strictly against funded amounts per epoch (track funded vs. claimed per epoch) so a claim can never consume basis still counted as pending debt or prefunded reserve.

### Proof of Concept
```solidity
// Foundry fork PoC (scaffold): contracts/test/foundry/InstantClaimLeak.t.sol
// Setup: epoch running, instant withdraws enabled via
//   cdoEpoch.setInstantWithdrawParams(delay, instantAprDelta, false)
function testClaimedInstantInflatesDefaultRecovery() public {
    // 1. Alice & Bob deposit into AA tranche, epoch 0 runs, epoch stops, buffer.
    // 2. Alice & Bob each requestInstantWithdraw(100e6) in epoch 1.
    // 3. warp past instantWithdrawDelay; borrower partially funds:
    //    manager.getInstantWithdrawFunds() -> collectInstantWithdrawFunds(120e6)
    //    assert pendingInstantWithdraws == 80e6
    // 4. Alice claims: claimInstantWithdrawRequest() -> receives 100e6
    //    assert instantWithdrawClaimsByEpoch[epoch] == 200e6  // stale, never freed
    //    assert pendingInstantWithdraws == 80e6              // stale
    // 5. Borrower returns nothing at stopEpoch -> _checkDefault()
    // 6. finalizeDefault(recovered, manager):
    //    defaultPendingClaimBasis() includes 200e6 (Alice's paid 100e6 double counted)
    //    _defaultPrefundedInstantReserve() reports 120e6 held; real balance is 20e6
    // 7. assert strategy.defaultRecoveryReserve() > strategy token balance shortfall
    //    assert Bob's claimWithdrawRequest / claimInstantWithdrawRequest pays
    //    less than claimBasis * defaultRecoveryPrice or reverts (reserve underfunded)
}
```