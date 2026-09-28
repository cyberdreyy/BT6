### Title
Loss-adjusted withdraw receipts claimable at par due to epoch-index mismatch between `lastWithdrawRequest` and `lossRecoveryPriceByEpoch` - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault` keys per-epoch withdraw receipts by the epoch in which `requestWithdraw` runs (`withdrawsRequestsByEpoch[user][epochNumber]`, `lastWithdrawRequest[user] = epochNumber`), but `collectWithdrawFunds` stores the haircut price under `epochNumber` *at collect time*. Because `deposit()` increments `epochNumber` when invoked while `isEpochRunning()` is true (the deposit performed during `stopEpoch`), a loss-realizing `stopEpochWithDuration` stores `lossRecoveryPriceByEpoch[N+1]` while all receipts from that epoch are indexed at `N`. The loss-adjusted claim path then reads price `0`, falls through to the funded-claim path, and pays receipts at par.

### Finding Description
Relevant code:

- `requestWithdraw` records the request epoch: `lastWithdrawRequest[_user] = currentEpoch` and `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount` (`IdleCreditVault.sol:260-294`).
- `deposit()` bumps the epoch counter whenever the epoch is still running (`IdleCreditVault.sol:607-610`).
- `collectWithdrawFunds` stores the haircut under the *current* `epochNumber`: `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice` (`IdleCreditVault.sol:417-421`).
- `_claimLossAdjustedWithdrawRequest` looks the price up only under `lastWithdrawRequest[_user]` — the *request* epoch (`IdleCreditVault.sol:789-801`).
- `_claimFundedWithdrawRequest` only requires `epochNumber > lastWithdrawRequest[_user]`, which is satisfied the moment `stopEpoch` incremented the counter (`IdleCreditVault.sol:326-349`).

Sequence:

1. During epoch `N`, a lender calls `requestWithdraw(amount, user, principal)` via the CDO. `lastWithdrawRequest[user] = N`, `pendingWithdraws += amount`.
2. Borrower repays only part of `pendingWithdraws` (a partial default / `stopEpochWithDuration(_lossAmount)`). During `stopEpoch` the CDO deposits redeemed funds back into the strategy while `isEpochRunning()` is still true → `epochNumber` becomes `N+1`.
3. `collectWithdrawFunds(funded)` with `funded < pendingWithdraws` executes: `lossRecoveryPriceByEpoch[N+1] = funded * 1e18 / pendingWithdraws`, `pendingWithdraws = 0`, and only `funded` underlyings are pulled into the strategy.
4. The user calls `claimWithdrawRequest`. `_claimLossAdjustedWithdrawRequest` reads `lossRecoveryPriceByEpoch[N] == 0` → returns 0 without clearing the receipt. `_claimFundedWithdrawRequest` sees `epochNumber (N+1) > lastWithdrawRequest (N)` → pays `withdrawsRequests[user]` **at full par** via `_transferFundedClaim`.

### Impact Explanation
Every loss-adjusted receipt pays 100% even though only `funded < basis` was transferred in. The first claimants drain underlying belonging to other users' funded receipts, active LP redemptions held in the strategy, or (once any `defaultRecoveryReserve` check is bypassed by subsequent accounting) later claimants revert — i.e., theft plus permanent freezing of the remaining claims. Broken invariant: loss socialization / fair burn (one receipt should pay `claimBasis * lossRecoveryPrice`, it pays `claimBasis`). The `requestWithdraw` guard that forces claiming a loss-adjusted receipt before re-requesting does not help, because the receipt is never recognized as loss-adjusted in the first place.

### Likelihood Explanation
Triggers whenever `stopEpochWithDuration` is called with `_lossAmount > 0` while `pendingWithdraws > 0` — a normal, honest-manager code path, no attacker privilege needed. The attacker only needs to be an ordinary withdraw requester who races to `claimWithdrawRequest` after the loss epoch stops. Requires confirming that the CDO's `stopEpoch` calls `strategy.deposit` (incrementing `epochNumber`) before `collectWithdrawFunds` in the same transaction; the code comments ("deposit done on stopEpoch … so we reset the counter") indicate this ordering is intended. If `collectWithdrawFunds` ever ran before the increment, the same receipt bookkeeping would still be off by one whenever a *new* epoch's request epoch collides with a stored price.

### Recommendation
Key `lossRecoveryPriceByEpoch` by the epoch the pending receipts were *requested* in, not the epoch at collection time — e.g., store under `epochNumber - 1` after the stop-epoch increment, or snapshot `pendingWithdrawsEpoch` when the first request of an epoch is recorded and use that key in both `collectWithdrawFunds` and `_claimLossAdjustedWithdrawRequest`. Additionally, make `_claimFundedWithdrawRequest` revert (rather than silently paying par) when `withdrawsRequestsByEpoch[user][epoch]` is non-zero for any epoch that has a pending-but-unpriced loss, and add a regression test exercising `requestWithdraw` → `stopEpochWithDuration(loss)` → `claimWithdrawRequest`.

### Proof of Concept
```solidity
// Foundry fork test against an IdleCDOEpochVariant + IdleCreditVault deployment.
// Assumes stopEpoch calls strategy.deposit() while isEpochRunning() (incrementing
// epochNumber) before collectWithdrawFunds().

function testLossAdjustedReceiptPaysPar() public {
    // --- epoch N, running phase ---
    _startEpoch();                                    // epochNumber == N
    uint256 basis = 100e18;
    _cdoRequestWithdraw(user, basis);                 // IdleCreditVault.requestWithdraw
    // lastWithdrawRequest[user] == N
    // withdrawsRequestsByEpoch[user][N] == basis
    // pendingWithdraws == basis

    // --- borrower repays only 60% at stopEpoch ---
    uint256 funded = 60e18;
    _repayAndStopEpochWithLoss(funded);               // stopEpoch -> deposit() -> epochNumber = N+1
                                                      // -> collectWithdrawFunds(funded)
    // lossRecoveryPriceByEpoch[N+1] == 0.6e18
    // lossRecoveryPriceByEpoch[N]   == 0          <-- mismatch
    // strategy received only `funded` underlying for these receipts

    // --- user claims ---
    vm.prank(address(cdo));
    uint256 paid = strategy.claimWithdrawRequest(user);

    // BUG: paid == basis (par) instead of basis * 0.6
    assertEq(paid, basis);                            // expected-fix: 60e18
    assertGt(underlying.balanceOf(user), funded);     // overpaid vs. funded amount
    // subsequent claimants' _transferFundedClaim now reverts or drains other users' funds
}
```

Note: I could not execute the PoC or fully trace `IdleCDOEpochVariant.stopEpoch`'s internal call order (deposit vs. `collectWithdrawFunds`) within this session; the finding hinges on `deposit()` running first, which the in-code comments strongly imply. If that ordering does not hold, the same off-by-one exposure applies to any request made during the epoch in which a loss price was stored.