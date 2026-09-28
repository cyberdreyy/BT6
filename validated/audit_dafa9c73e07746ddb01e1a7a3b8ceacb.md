### Title
Permanent `stopEpoch` DoS via APR0 withdraw receipt plus mid-epoch APR change forces borrower default / freezes all pool funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.prepareStopEpochWithApr0()` unconditionally reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0`. An unprivileged lender can create this poisoned state with a single `requestWithdraw` during an APR=0 epoch: if the manager legitimately updates the pool APR to a non-zero value before `stopEpoch` runs (which `setAprsWithBuffer`/`setApr` allow with no epoch-phase gating), every subsequent `stopEpoch`/`stopEpochWithDuration` call reverts. The epoch can never be stopped cleanly, no withdraw claims are funded, and the only escape is the borrower-default path, which haircuts all LPs.

### Finding Description
- In `requestWithdraw`, when `unscaledApr == 0` and the pool is not closed, the request is routed to `_requestWithdrawApr0`, which increments `apr0TotalPrincipal` and records `apr0Users[_user].principalEpoch = epochNumber` (`IdleCreditVault.sol:285-286`, `567-577`). Any KYC-passed lender can do this by depositing and calling `IdleCDOEpochVariant.requestWithdraw`.
- `setAprsWithBuffer`/`setApr` (lines 217-235) are callable by the manager at any time, including mid-epoch. Honest managers routinely set the next epoch's APR; the comment at line 223 explicitly documents direct manager APR updates.
- At `stopEpoch`, `IdleCDOEpochVariant` calls `_strategy.prepareStopEpochWithApr0(_interest)` (`IdleCDOEpochVariant.sol:362`). Inside, when `apr0TotalPrincipal != 0` the function reverts if `unscaledApr != 0` (`IdleCreditVault.sol:505-508`).
- There is no recovery path: `apr0TotalPrincipal` can only be cleared by `prepareStopEpochWithApr0` itself (line 540), which is unreachable because the same function reverts first. APR0 user principal can only be cleared via claims, but `_settleApr0` only settles after `epochNumber` advances, and `epochNumber` only advances inside a successful `stopEpoch`/`deposit` flow. The state is a dead-end: every `stopEpoch` reverts before `getFundsFromBorrower`, so interest is never collected, `pendingWithdraws` is never funded, and `claimWithdrawRequest`/`claimInstantWithdrawRequest` stay blocked.

### Impact Explanation
Permanent freezing of all vault funds pending a borrower default, or an effectively forced default. While the epoch is stuck, the borrower cannot repay and LPs cannot withdraw. If the manager eventually triggers the default path, all LPs and pending withdraw receipts take a recovery haircut on `defaultRecoveryPrice` — a direct loss proportional to the missing repayment, attributable to a single unprivileged withdraw request combined with a routine honest manager APR update. This matches the external bug class (low-privilege, network-reachable, availability-only failure) mapped onto the credit-vault claim/epoch state machine.

### Likelihood Explanation
The attacker transaction is cheap and permissionless: deposit + `requestWithdraw` while `unscaledApr == 0` (APR0 mode is a supported pool configuration). The honest-manager precondition is realistic — updating APR for the upcoming epoch is a normal manager operation and `setApr` has no timing guard. The revert is deterministic and unconditional once `apr0TotalPrincipal != 0 && unscaledApr != 0`.

### Recommendation
Make `prepareStopEpochWithApr0` tolerant of an APR change instead of reverting: e.g., settle the open APR0 bucket at a zero rate (write `apr0RateByEpoch[epochNumber] = 0` or skip it, and clear `apr0TotalPrincipal`) when `unscaledApr != 0`, since APR0 requests were priced under a 0-APR epoch anyway. Alternatively, block `setApr`/`setAprsWithBuffer` to non-zero values while `apr0TotalPrincipal != 0`, or snapshot the APR per epoch so mid-epoch changes cannot invalidate in-flight receipts.

### Proof of Concept
Foundry fork PoC sketch (based on the harness in `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testStopEpochDosAfterApr0RequestAndAprChange() external {
    uint256 amount = 10_000 * ONE_SCALE;
    // Pool configured with APR = 0 (apr0 mode)
    vm.prank(manager);
    strategy.setAprsWithBuffer(0, cdoEpoch.epochDuration(), cdoEpoch.bufferPeriod());

    uint256 mintedAA = idleCDO.depositAA(amount);

    // Epoch 0 runs with APR 0
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, 0, _expectedFundsEndEpochZeroApr());

    // Attacker (any KYC'd lender) opens an APR0 withdraw request -> apr0TotalPrincipal > 0
    cdoEpoch.requestWithdraw(mintedAA / 2, address(AAtranche));
    assertGt(strategy.apr0TotalPrincipal(), 0);

    // Honest manager sets the next epoch's APR to a non-zero value (allowed any time)
    vm.prank(manager);
    strategy.setAprsWithBuffer(10e18, cdoEpoch.epochDuration(), cdoEpoch.bufferPeriod());

    _startEpochAndCheckPrices(1);
    vm.warp(cdoEpoch.epochEndDate() + 1);

    // Every stopEpoch now reverts in prepareStopEpochWithApr0: epoch can never end
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(initialProvidedApr, 0);

    // Retrying with any parameters reverts identically; claims stay unfunded.
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpochWithDuration(0, 0, cdoEpoch.epochDuration(), 0);
}
```

The revert is at `IdleCreditVault.sol:506` (`if (unscaledApr != 0) revert NotAllowed();`) reached because the attacker's receipt set `apr0TotalPrincipal > 0` at line 576.