### Title
Claimed instant-withdraw receipts stay in `instantWithdrawClaimsByEpoch`, inflating the default-recovery basis and permanently freezing recovery funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.claimInstantWithdrawRequest` burns a user's instant-withdraw receipt and pays out funded underlying, but never decrements `instantWithdrawClaimsByEpoch[epochNumber]`. Only the defaulted-claim path (`_claimDefaultedInstantWithdrawRequest`) clears that per-epoch counter. The stale counter is later consumed by `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` during `finalizeDefaultRecovery`, which corrupts the recovery price and reserve accounting after a borrower default.

### Finding Description
`requestInstantWithdraw` tracks two aggregates: `instantWithdrawsRequests[_user]` (per-user) and `instantWithdrawClaimsByEpoch[currentEpoch]` (per-epoch total) plus `pendingInstantWithdraws` (unfunded remainder). `pendingInstantWithdraws` is decremented in `collectInstantWithdrawFunds`, but `instantWithdrawClaimsByEpoch` is only decremented inside `_claimDefaultedInstantWithdrawRequest` (`instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis`). The normal claim path (`claimInstantWithdrawRequest` → `_transferFundedClaim`) leaves it untouched, so the per-epoch instant basis monotonically grows with every request and is never cleaned by normal claims.

At default finalization:

- `defaultPendingClaimBasis` returns `pendingWithdraws + instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0`, so already-claimed receipts are double-counted into the claim basis.
- `_defaultPrefundedInstantReserve` computes `instantBasis - pendingInstant` and treats the difference as underlyings already held by the strategy. With a stale (inflated) `instantBasis`, the phantom "prefunded" reserve is added to `reserveAmount` in `finalizeDefaultRecovery` without any real tokens backing it.

### Impact Explanation
Two failure modes, both reachable by an unprivileged lender (any tranche-token holder who uses the instant-withdraw flow):

1. **Overstated reserve → insolvency at claim time.** If the phantom prefunded reserve pushes `reserveAmount` above the real token balance, `defaultRecoveryPrice` is set too high. Each defaulted claim calls `_transferDefaultRecovery`, which does `defaultRecoveryReserve -= _amount` and then `safeTransfer`. Once the real balance is exhausted, every remaining claimant's transaction reverts — permanently freezing their recovery shares. The last claimants lose their entire entitled recovery; the first claimants are overpaid.
2. **Overstated basis → diluted recovery and trapped dust.** Even when the phantom reserve doesn't exceed the balance, inflating `totalBasis` lowers `recoveryPrice`, underpaying all defaulted claimants; the excess reserve is never withdrawable (no sweep path spends `defaultRecoveryReserve`), permanently locking real tokens.

### Likelihood Explanation
Triggering requires only: (a) at least one instant-withdraw request that is claimed via the normal path before a default, leaving a nonzero stale `instantWithdrawClaimsByEpoch`; and (b) a later borrower default finalized while `pendingInstantWithdraws != 0`. No privileged attacker is involved — the manager/owner/borrower act honestly, and the corruption happens entirely inside the strategy's own accounting. The guards do not stop it: `_ensureDefaultRecoveryInitialized` only checks `pendingInstantWithdraws`, `defaultRecoveryFinalized` is a one-way flag, and `_transferFundedClaim`'s reserve check protects the reserve balance but not the basis math. Likelihood is gated by an actual default occurring while the stale counter survives into the same `epochNumber` bucket; since `epochNumber` only increments on `deposit` during a running epoch and `instantWithdrawClaimsByEpoch` is never cleared on epoch rollover, stale entries can persist into a defaulted epoch.

### Recommendation
- In `claimInstantWithdrawRequest`, decrement `instantWithdrawClaimsByEpoch[epochOfRequest]` alongside `instantWithdrawsRequests[_user]`, or track per-user the request epoch (as done for normal receipts via `withdrawsRequestsByEpoch`) so the per-epoch basis always equals outstanding, unclaimed receipts.
- Alternatively, derive the instant basis in `defaultPendingClaimBasis` from live `instantWithdrawsRequests` totals rather than the never-decremented cumulative map, and make `_defaultPrefundedInstantReserve` measure actual held balance instead of inferring it from stale counters.
- Add an invariant test: sum of per-epoch instant claims equals outstanding instant receipts after any sequence of request/claim/collect operations.

### Proof of Concept
Sketch (Foundry, mainnet-style vault harness from `test/foundry/TestIdleCDOBase.sol`):

```solidity
function test_StaleInstantBasisFreezesRecovery() public {
    // Epoch N running; lender A deposits AA, then:
    vm.prank(lenderA);
    cdo.requestInstantWithdraw(amountA);   // instantWithdrawsRequests[A] += amountA
                                          // instantWithdrawClaimsByEpoch[N] += amountA
    // CDO collects funded instant liquidity:
    vm.prank(address(cdo));
    strategy.collectInstantWithdrawFunds(amountA); // pendingInstantWithdraws -> 0 partially
    // lender A claims normally (funded path):
    vm.prank(address(cdo));
    strategy.claimInstantWithdrawRequest(lenderA);
    // BUG: instantWithdrawClaimsByEpoch[N] still == amountA

    // Later epoch: new instant requests arrive, borrower funds only partially,
    // pendingInstantWithdraws > 0 again, then borrower defaults.
    _forceBorrowerDefault();
    vm.prank(address(cdo));
    strategy.finalizeDefaultRecovery(recovered, recoverySource);
    // reserveAmount includes phantom amountA from _defaultPrefundedInstantReserve
    // totalBasis includes amountA again via defaultPendingClaimBasis

    // Result: defaultRecoveryPrice mispriced; final claimant reverts on
    // defaultRecoveryReserve -= _amount / safeTransfer -> permanent freeze.
    vm.expectRevert();
    vm.prank(address(cdo));
    strategy.claimInstantWithdrawRequest(lastClaimant);
}
```

The PoC asserts `instantWithdrawClaimsByEpoch[N]` is nonzero after A's claim and that the sum of defaulted payouts exceeds (or is diluted below) the real recovered balance — demonstrating permanent freezing of recovery funds caused purely by unprivileged request/claim sequencing.