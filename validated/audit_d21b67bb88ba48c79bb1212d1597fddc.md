### Title
Any pending APR0 withdraw request permanently reverts `stopEpoch` once APR is raised above zero — pool-wide freeze of all vault funds - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`IdleCreditVault.prepareStopEpochWithApr0` reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0`. An unprivileged lender who calls `requestWithdraw` while the vault runs at `unscaledApr == 0` leaves a nonzero `apr0TotalPrincipal` bucket. If the honest manager later restores a positive APR — a routine operational action — every subsequent `stopEpoch`/`stopEpochWithDuration` call reverts, so the epoch can never be stopped and no withdraw request can ever be claimed while the pool offers a positive yield.

### Finding Description
In `contracts/strategies/idle/IdleCreditVault.sol`:

- `requestWithdraw` (lines 271–295) credits any user a strategy-token receipt and, when `unscaledApr == 0`, routes the request through `_requestWithdrawApr0`, which increments `apr0TotalPrincipal` and records `apr0Users[_user].principal`/`principalEpoch`. No owner approval is needed; any KYC-passing lender with tranche tokens can do this, even for a dust amount.
- `prepareStopEpochWithApr0` (lines 490–508) is invoked by the CDO inside every `stopEpoch`/`stopEpochWithDuration` path. After the fast-path check on `apr0TotalPrincipal`, it enforces `if (unscaledApr != 0) revert NotAllowed()` — treating "APR0 bucket exists while APR is non-zero" as fatal rather than settling the bucket at the accrued rate.
- `apr0TotalPrincipal` is only cleared inside the same function (line 540) or lazily via `_clearWithdrawClaimForEpoch` on user claims — but users cannot claim until an epoch boundary passes (`epochNumber <= lastWithdrawRequest[_user]` reverts in `_claimFundedWithdrawRequest`, lines 326–328), and `epochNumber` only advances via `stopEpoch`. So once APR > 0 with an open APR0 bucket, the state machine deadlocks: claims need a stopped epoch, and stopping needs `apr0TotalPrincipal == 0` or `unscaledApr == 0`.

The broken invariant is liveness of the epoch state machine: an unprivileged request made in one phase (buffer/running epoch at APR 0) poisons all future `stopEpoch` executions under any positive APR configuration. Existing guards do not help: the `NotAllowed` revert is the guard itself, `maxApr` doesn't apply to 0, and honest roles (manager, borrower, feeReceiver) cannot remove a stranger's `apr0TotalPrincipal` — only the user themselves can claim, and only after the very `stopEpoch` that is now blocked.

### Impact Explanation
High: permanent (or indefinitely long) freezing of all vault funds. While APR is non-zero, `stopEpoch` always reverts, so:
- No withdraw requests (normal, APR0, or instant) can be claimed — they all require `epochNumber` to advance.
- The pool cannot close (`stopEpoch(0, 1)` also calls `prepareStopEpochWithApr0`), cannot enter default processing, and borrower funds cannot be recalled.
- The only escape is returning `unscaledApr` to 0 forever, i.e. the vault can never again offer a positive yield — economically equivalent to a shutdown, and if the manager believes APR must be > 0 for business reasons the freeze persists.

Cost to the attacker is negligible: one tranche position (dust suffices) plus a single `requestWithdraw` while APR happens to be 0, which is a legitimate supported mode (`setAprs(0,0)` is exercised in tests).

### Likelihood Explanation
Medium. It requires a sequence of honest privileged configuration: manager sets APR 0 (a supported yield-free mode), attacker submits any withdraw request, then manager raises APR. None of the attacker steps need privilege, and the trigger state (APR 0 epoch) is an explicitly designed operating mode, not an edge case. No front-running or oracle manipulation is needed; the griefing window is the entire APR0 epoch/buffer period.

### Recommendation
Do not revert in `prepareStopEpochWithApr0` when `unscaledApr != 0` while `apr0TotalPrincipal > 0`. Instead, settle the open APR0 bucket at the rate already recorded in `apr0RateByEpoch` for its request epoch (or at zero additional interest if unset), then zero `apr0TotalPrincipal`, so the epoch state machine can proceed regardless of current APR. Alternatively, allow `stopEpoch` to proceed and mark the APR0 bucket force-settled, and add a regression test covering: APR0 epoch → user `requestWithdraw` → `setAprs(x>0)` → `stopEpoch` succeeds and the user's claim still pays principal plus epoch-finalized interest.

### Proof of Concept
Foundry fork PoC sketch (extend `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testApr0RequestBlocksStopEpochAfterAprRaised() external {
    // 1. Honest manager sets APR to 0 (supported APR0 mode)
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);

    // 2. KYC'd attacker deposits and requests a dust withdraw during APR0 epoch
    address attacker = makeAddr('apr0-griefer');
    _depositWithUser(attacker, 10 * ONE_SCALE, true);
    uint256 trancheBal = IERC20(AAtranche).balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(1, address(AAtranche)); // dust is enough

    // 3. Epoch advances honestly while APR is still 0
    _startEpochAndCheckPrices(0);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, _expectedFundsEndEpoch());
    assertGt(strategy.apr0Users(attacker).principal, 0);

    // 4. Manager legitimately restores a positive APR
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(initialProvidedApr, scaledApr);

    // 5. New epoch runs; at its end stopEpoch reverts forever
    _startEpochAndCheckPrices(1);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, _expectedFundsEndEpoch());

    // 6. Attacker cannot unblock it: claim reverts because epochNumber never advanced
    vm.prank(attacker);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.claimWithdrawRequest();
    // => All lender principal + all other pending receipts frozen until APR is forced back to 0.
}
```

Note: the freeze is technically escapable only by permanently keeping `unscaledApr == 0` (the revert is conditioned on `unscaledApr != 0`), so the quantified loss is the forfeiture of all positive-yield operation plus indefinite lock of every unclaimed receipt while any positive APR is configured.