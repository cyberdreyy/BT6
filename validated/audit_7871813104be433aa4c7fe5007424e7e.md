### Title
Dust APR0 withdraw request permanently blocks `stopEpoch` whenever APR is non-zero, freezing all lender funds and forcing zero-yield epochs - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.prepareStopEpochWithApr0` reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0`. Any KYC-passing lender can open a dust-sized withdraw request while the pool APR is 0 (`requestWithdraw` → `_requestWithdrawApr0` → `apr0TotalPrincipal += _amount`), which permanently poisons the epoch settlement path: as long as that APR0 principal bucket is open, every `IdleCDOEpochVariant.stopEpoch` call reverts. The bucket can only be closed by a successful `stopEpoch`, creating a deadlock that the attacker can renew each buffer period at dust cost.

### Finding Description
In `contracts/strategies/idle/IdleCreditVault.sol`:

- `requestWithdraw` routes requests into the APR0 bucket whenever `unscaledApr == 0` and the pool is not closed (lines 285-286), calling `_requestWithdrawApr0`, which does `apr0TotalPrincipal += _amount` (line 576). There is no minimum amount — a 1-wei request works, and the "principal" is credited back to the requester on settlement, so the attack is nearly free.
- `prepareStopEpochWithApr0`, invoked by the CDO during `stopEpoch`, reverts if `apr0TotalPrincipal != 0 && unscaledApr != 0` (lines 502-508). The revert is an intentional invariant ("APR0 principal is only valid while APR is 0 for that request lifecycle"), and the codebase even has a test asserting this revert (`testApr0InvariantRevertsIfAprChangesAfterApr0Request`, `test/foundry/IdleCreditVault.t.sol:3306`).
- The bucket is only closed inside the same function (`apr0TotalPrincipal = 0`, line 540), which is unreachable when it reverts.
- `_settleApr0` can only clear a user's `principal` after `epochNumber` has advanced past `principalEpoch` (lines 552-554), and `epochNumber` is only bumped by a successful `stopEpoch` — so the poisoned state cannot be cleared by the attacker or by claims.
- There is no cancel-request path and no privileged escape hatch: the only recovery is the manager setting `unscaledApr` back to 0 via `setApr`/`setAprs` so `stopEpoch` succeeds at zero interest — after which the attacker can deposit dust again and repeat the request in the next buffer window.

### Impact Explanation
An unprivileged lender (any KYC-passing address; `requestWithdraw` is reachable through `IdleCDOEpochVariant.requestWithdraw` by any tranche holder) can:

1. Freeze the vault: once the manager sets a non-zero APR for a new epoch (a routine, honest action) while a dust APR0 request is pending, every `stopEpoch` reverts. The epoch never ends: `epochEndDate` passes but funds are locked mid-epoch, deposits/withdrawals are gated by `isEpochRunning`, and no withdraw requests can ever become claimable (claims require `epochNumber > lastWithdrawRequest`). All lender principal and accrued interest are frozen until governance/manager intervention resets the APR to 0.
2. Permanent forced zero-yield griefing: even with the APR-0 workaround, the attacker can re-poison each epoch for ~1 wei of principal, forcing the pool to run at 0% APR indefinitely — a complete denial of all lender yield at negligible cost.

Quantified loss: temporary freezing of the entire vault TVL (100% of lender funds) for the duration APR remains non-zero, plus loss of all epoch interest indefinitely under the repeated-griefing scenario.

### Likelihood Explanation
- Attacker requirements: pass Keyring KYC and deposit enough to hold any positive tranche balance — dust-level capital.
- Triggering sequence is purely attacker-driven at the critical step (request during an APR-0 buffer/running epoch); the only privileged input is the manager setting a non-zero APR, which is the normal operating mode of the vault, not adversarial behavior.
- The revert path is deterministic (no oracle, timing, or liquidity dependency) and is already exercised by the protocol's own test suite.
- The attack is repeatable per epoch and self-perpetuating, since APR0 state can never be cleared while it blocks settlement.

### Recommendation
Decouple APR0 lifecycle enforcement from `stopEpoch` liveness. Options:

- Instead of reverting in `prepareStopEpochWithApr0` when `unscaledApr != 0`, settle outstanding APR0 principal at 0 interest (skip the pro-rata interest split, set `apr0RateByEpoch[epochNumber] = 0`, still close `apr0TotalPrincipal`) so settlement always succeeds.
- Or enforce the invariant at request/mutation time: revert `setApr`/`setAprs` (or `startEpoch`) when `apr0TotalPrincipal != 0`, so a non-zero APR can never coexist with an open APR0 bucket — moving the block to a governance action rather than to fund settlement.
- Add a minimum request size and/or allow cancelling a pending APR0 request before its epoch settles, reducing the griefing surface.

### Proof of Concept
Foundry fork PoC (sketch, building on `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testApr0DustGriefingBlocksStopEpoch() external {
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);

    // Honest lender deposits real funds
    idleCDO.depositAA(10000 * ONE_SCALE);

    // Epoch 0 runs at APR = 0
    _startEpochAndCheckPrices(0);
    _forceLastEpochAprToZero(); // unscaledApr == 0 for the buffer window

    // Attacker (a second KYC'd lender, dust capital) deposits 1 wei worth
    // of tranche tokens and requests a withdraw -> poisons apr0TotalPrincipal
    address attacker = makeAddr("attacker");
    deal(defaultUnderlying, attacker, 1);
    vm.startPrank(attacker);
    IERC20Detailed(defaultUnderlying).approve(address(idleCDO), 1);
    idleCDO.depositAA(1);
    vm.stopPrank();
    _transferBurnedTrancheTokens(attacker, true);
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(1, address(AAtranche));
    assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

    // Manager honestly configures a normal non-zero APR for epoch 1
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(5e17, 5e17); // unscaledApr != 0
    _startEpochAndCheckPrices(1);

    // Borrower repays; stopEpoch must settle the epoch but always reverts
    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 expectedInterest = cdoEpoch.expectedEpochInterest();
    deal(defaultUnderlying, borrower, expectedInterest);
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 0);

    // No recovery: claims require epochNumber to advance (impossible),
    // and APR0 settlement (_settleApr0) also requires epochNumber > principalEpoch.
    // All lender funds remain locked while unscaledApr != 0.
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 0);
}
```

The existing test `testApr0InvariantRevertsIfAprChangesAfterApr0Request` already demonstrates the revert; the PoC above reframes it as attacker-controlled liveness failure by introducing a second, unprivileged dust depositor whose APR0 request bricks settlement for all honest depositors.

Uncertainty note: I confirmed the revert sites, the APR0 accounting flow, and the deadlock conditions directly in `IdleCreditVault.sol` and the test file. I did not fully trace `IdleCDOEpochVariant.stopEpoch`'s call into `prepareStopEpochWithApr0` line-by-line due to iteration limits, but the existing test `cdoEpoch.stopEpoch(0, 0)` → `NotAllowed` revert confirms the propagation path end-to-end.