### Title
Epoch index off-by-one on `lossRecoveryPriceByEpoch` lets loss-haircutted withdraw receipts claim at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The Wireshark bug is a classic off-by-one boundary error. The analog in `IdleCreditVault` is a one-epoch index mismatch: `collectWithdrawFunds` stores the haircut ratio under the *post-increment* `epochNumber` (line 421), while `requestWithdraw` and the claim path look it up under `lastWithdrawRequest[_user]`, which was recorded under the *pre-increment* epoch (lines 261–262, 282). Because `deposit()` bumps `epochNumber` inside `stopEpoch` while `isEpochRunning()` is still true (lines 607–610), pending receipts are haircut under a different epoch key than the one they were registered with — so the guard at lines 263–271 and any `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` lookup in `_claimLossAdjustedWithdrawRequest` see `0` ("no loss-adjusted epoch") instead of the haircut.

### Finding Description
When `stopEpochWithDuration(_lossAmount)` realizes a loss that is partially assigned to pending withdraw receipts, `collectWithdrawFunds` is called with `_amount < pendingWithdraws` and stores:

```solidity
lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;   // line 421
```

`epochNumber` is incremented by `deposit()` during the same `stopEpoch` (lines 607–610: `epochNumber += 1` while `isEpochRunning()`). Receipts created during the running epoch carry `lastWithdrawRequest[_user] = N` and `withdrawsRequestsByEpoch[_user][N]`, but the haircut is stored under `N+1`. The consequence is two-sided:

1. **Escape:** A user whose receipt was haircut at epoch `N` passes the guard at lines 263–271 because `lossRecoveryPriceByEpoch[N] == 0`, then claims their receipt at par via `_claimFundedWithdrawRequest`, draining funded underlying that economically belongs to other receipt holders.
2. **Wrong-epoch contamination:** A user who requests during the buffer of epoch `N+1` gets `lastWithdrawRequest = N+1` and is permanently blocked by the guard (line 270 reverts) even though their receipt was never loss-adjusted — a liveness freeze on honest withdraws.

Existing guards do not stop case (1): the check at lines 263–271 explicitly relies on `lossRecoveryPriceByEpoch[lossEpoch] != 0`, and `pendingWithdraws` was already zeroed at line 420, so nothing reverts on the par claim.

### Impact Explanation
Direct insolvency/theft: haircutted receipts redeem at par, over-drawing the strategy's funded reserve, so the last claimants of the same epoch receive nothing (or the loss that should have been socialized across pending receipts is instead borne entirely by the unfunded remainder). Quantified loss equals `pendingBasis * (1 - lossRecoveryPrice)`, i.e., the full haircut portion, stealable by the first claimant.

### Likelihood Explanation
Requires an epoch stopped via `stopEpochWithDuration` with `_lossAmount > 0` while pending receipts exist — a legitimate, non-privileged-attacker scenario (borrower shortfall realized by honest manager). Any receipt holder can then claim at par. Likelihood is moderate; it hinges on the ordering of `deposit()`/`collectWithdrawFunds` within `stopEpochWithDuration` in `IdleCDOEpochVariant`, which could not be fully verified from indexed snippets — if `collectWithdrawFunds` executes *before* the deposit that increments `epochNumber`, the keys align and the bug does not trigger (see PoC note).

### Recommendation
Key `lossRecoveryPriceByEpoch` by the epoch in which the receipts were *requested*, not the post-increment epoch. Options: snapshot `epochNumber - 1` (or the epoch that just ended) when storing the haircut, or move the `epochNumber` increment in `deposit()` so it cannot interleave with `collectWithdrawFunds`. Additionally, make `_claimLossAdjustedWithdrawRequest`/`requestWithdraw` iterate a stored per-user request-epoch set rather than trusting `lastWithdrawRequest` as the single lookup key.

### Proof of Concept
Foundry fork test sketch (verify ordering first — assert `strategy.epochNumber()` before and after `stopEpochWithDuration`):

```solidity
function testLossRecoveryEpochOffByOne() public {
    // Epoch N running: user deposits, requests withdraw of all tranches
    _depositWithUser(user1, 100e6);
    cdoEpoch.startEpoch();
    _requestWithdrawWithUser(user1, tranches1);   // lastWithdrawRequest[user1] = N

    uint256 reqEpoch = strategy.epochNumber();

    // stopEpoch with partial loss -> collectWithdrawFunds stores
    // lossRecoveryPriceByEpoch[N+1] (epochNumber already incremented by deposit())
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(interest, 0, duration, lossAmount);

    assertEq(strategy.lossRecoveryPriceByEpoch(reqEpoch), 0);          // off-by-one
    assertGt(strategy.lossRecoveryPriceByEpoch(reqEpoch + 1), 0);

    // Guard bypassed: user1 can open another request and/or claim at par
    vm.prank(user1);
    cdoEpoch.claimWithdrawRequest();  // pays full amount despite haircut
}
```

If the epoch assertion shows `lossRecoveryPriceByEpoch[reqEpoch] != 0` (i.e., `collectWithdrawFunds` runs before the increment), the analog does not hold and the finding should be downgraded to the symmetric freeze-only variant for buffer-period requesters keyed under `N+1`.