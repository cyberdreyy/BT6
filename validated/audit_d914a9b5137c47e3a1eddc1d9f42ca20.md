### Title
Stale `epochNumber` lets a new epoch reuse a previous epoch's ID, applying an old loss haircut to fresh withdraw receipts - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`epochNumber` is the vault's global epoch identifier and is used as the key for `withdrawsRequestsByEpoch`, `lossRecoveryPriceByEpoch`, `apr0RateByEpoch`, `instantWithdrawClaimsByEpoch`, and `lastWithdrawRequest`. It is only incremented inside `deposit()` (`epochNumber += 1` at `contracts/strategies/idle/IdleCreditVault.sol:610`), and only when that call lands while `isEpochRunning()` is still true. Any epoch transition that completes without a `deposit()` during the running epoch leaves `epochNumber` unchanged, so the next epoch silently reuses the same ID — the direct analog of the CVE's "two global IDs are the same" race. When that happens, receipts created in the new epoch are indexed under an epoch key that already carries a `lossRecoveryPriceByEpoch` haircut (or an `apr0RateByEpoch` rate) from the previous epoch, and users' fresh claims are paid at the stale, reduced price.

### Finding Description
The vulnerable keying lives in three places:

- `requestWithdraw` records `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount` and `lastWithdrawRequest[_user] = epochNumber` (`IdleCreditVault.sol:282-293`).
- `collectWithdrawFunds` stores a partial-funding haircut at `lossRecoveryPriceByEpoch[epochNumber]` (`IdleCreditVault.sol:421`) when the borrower funds less than `pendingWithdraws` at `stopEpochWithDuration`.
- `_claimLossAdjustedWithdrawRequest` resolves the haircut via `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` (`IdleCreditVault.sol:790-799`), and `_settleApr0` settles interest via `apr0RateByEpoch[principalEpoch]` (`IdleCreditVault.sol:558-561`).

Sequence:

1. Epoch N stops with a partial funding of pending receipts: `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch[N] = p1 < 1e18`.
2. A subsequent epoch boundary passes without any call into `deposit()` while `isEpochRunning()` (e.g., a zero-flow `stopEpoch`, or a flow that only calls `mintStrategyTokens`/`collectWithdrawFunds` — none of which touch `epochNumber`). `epochNumber` stays N.
3. Attacker (or any victim) calls `requestWithdraw` during the buffer. Their receipt is written into `withdrawsRequestsByEpoch[user][N]` and `lastWithdrawRequest[user] = N`.
4. The guard at `requestWithdraw:263-271` only checks `lossRecoveryPriceByEpoch[lastWithdrawRequest[user]]` — i.e., the user's *previous* request epoch, not the epoch being written — so it does not block the collision.
5. After the next real `stopEpoch` bumps `epochNumber` to N+1, `claimWithdrawRequest` → `_claimLossAdjustedWithdrawRequest` finds `lossRecoveryPriceByEpoch[N] = p1` and pays only `claimBasis * p1 / 1e18`, burning the full receipt (`IdleCreditVault.sol:794-800`).

The unpaid `(1 - p1)` fraction is never credited to anyone: `pendingWithdraws` was already cleared in step 1, so the shortfall is permanently stranded in the strategy contract. Symmetrically, an APR0 request colliding with an old epoch inherits `apr0RateByEpoch[N]` interest it never earned (paid out of the funded bucket, diluting other claimants) or, when the stale epoch has no rate, is settled at zero interest.

### Impact Explanation
Users' new-epoch withdraw receipts are paid at a stale haircut from an unrelated prior loss epoch: a direct, quantified loss of `(1 - p1) * receiptAmount` per affected user, with the difference frozen in the vault forever (no code path releases it). In the APR0 variant, stale-rate collision lets an attacker mint `principal * apr0RateByEpoch[N] / 1e18` of unearned interest, paid from the same funded pool honest claimants draw on — theft of unclaimed yield.

### Likelihood Explanation
The trigger requires an epoch transition with no `deposit()` call while running. Whether `IdleCDOEpochVariant.stopEpoch`/`_afterStopEpochWithDuration` always calls `strategy.deposit(...)` unconditionally (deposit(0) still bumps `epochNumber`, so an unconditional call would fully mitigate this) is the unverified precondition — I could not confirm the CDO-side call path within this analysis. If any mode (prefunded, minted-interest, emergency-stop, or zero-repayment stop) skips `deposit`, the collision is deterministic, not probabilistic, and repeatable every epoch.

### Recommendation
Bump `epochNumber` inside `stopEpoch`/`_afterStopEpoch`-driven strategy hooks rather than inside `deposit()`, so the epoch ID advances on every epoch transition regardless of cash flow. Alternatively, write `lossRecoveryPriceByEpoch`/`apr0RateByEpoch` under a monotonically increasing counter and revert `requestWithdraw` if `lossRecoveryPriceByEpoch[epochNumber] != 0`.

### Proof of Concept
```solidity
// Fork test against a live IdleCreditVault/IdleCDOEpochVariant deployment.
// Requires a stopEpoch path that does not call strategy.deposit() while running
// (e.g., prefunded or minted-interest vault); adjust stop helper accordingly.

function testStaleEpochIdAppliesOldHaircut() public {
    // epoch N running; LP requests withdraw, recorded under epochNumber N
    vm.prank(lp);
    cdoEpoch.requestWithdraw(shares, address(aaTranche));
    uint256 epochN = strategy.epochNumber();

    // borrower under-funds pendingWithdraws at stopEpochWithDuration
    // -> collectWithdrawFunds sets lossRecoveryPriceByEpoch[N] = p1 < 1e18
    _stopEpochWithPartialFunding(0.5e18); // p1 = 0.5

    // a new epoch boundary passes with NO strategy.deposit() while running
    // -> epochNumber is still N
    _advanceEpochWithoutStrategyDeposit();
    assertEq(strategy.epochNumber(), epochN); // duplicate epoch ID

    // attacker/victim opens a fresh request, written under the same key N
    vm.prank(lp2);
    cdoEpoch.requestWithdraw(shares2, address(aaTranche));
    assertEq(strategy.lastWithdrawRequest(lp2), epochN);

    // next real stopEpoch bumps epochNumber to N+1; claim matures
    _stopEpochFullyFunded();

    uint256 balPre = underlying.balanceOf(lp2);
    vm.prank(lp2);
    cdoEpoch.claimWithdrawRequest();

    uint256 expected = strategy.withdrawsRequestsByEpoch(lp2, epochN); // 0 post-claim
    // lp2 receives only p1 * basis; the rest is stranded in the strategy
    assertLt(underlying.balanceOf(lp2) - balPre, fullBasis);
    assertGt(strategy.contractUnderlyingBalance(), reservedOnly);
}
```