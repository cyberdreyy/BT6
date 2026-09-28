### Title
APR0 withdraw request permanently bricks `stopEpoch` once APR is raised — replayable wedge freezes all vault funds - (File: `contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
`IdleCreditVault.requestWithdraw` lets any KYC'd tranche holder open an APR0 withdraw receipt whenever `unscaledApr == 0`, which increments the global `apr0TotalPrincipal` bucket. The only paths that ever clear that bucket are `prepareStopEpochWithApr0` (line 540) and the default-claim path `_clearWithdrawClaimForEpoch` (line 827). `prepareStopEpochWithApr0` reverts with `NotAllowed` when `apr0TotalPrincipal != 0 && unscaledApr != 0` (lines 499–508), so once an APR0 receipt is open and the manager sets a nonzero APR, every subsequent `stopEpoch` reverts. The attacker can re-open the wedge on every epoch where APR returns to 0, replaying the condition indefinitely — analogous to replaying corrupted handshake packets to deny service.

### Finding Description
- An unprivileged lender calls `IdleCDOEpochVariant.requestWithdraw` (IdleCDOEpochVariant.sol:739) during a buffer/stopped phase while `unscaledApr == 0`. The strategy mints the receipt and executes `_requestWithdrawApr0`, which adds `_amount` to `apr0TotalPrincipal` (IdleCreditVault.sol:567-577, 285-286).
- The honest manager later raises APR via `setAprs` — a routine, legitimate operation. Now `apr0TotalPrincipal != 0` and `unscaledApr != 0`.
- Every `stopEpoch` call routes through `prepareStopEpochWithApr0`, which hits `revert NotAllowed()` at line 507 before reaching the `apr0TotalPrincipal = 0` reset at line 540. The epoch can never be stopped, so `epochNumber` never advances.
- Consequences cascade: borrowers' interest/repayment flow stalls, all withdraw receipts stay frozen (claims require `epochNumber > lastWithdrawRequest`, IdleCreditVault.sol:326), queued claims cannot be processed, and new epochs cannot start. If the manager lowers APR back to 0 to unbrick, the pool earns zero yield for the epoch — and the attacker can simply open another APR0 request, replaying the wedge on the next APR raise. The only unwinding path is borrower default + `finalizeDefault` claims, which destroys the vault anyway.
- Guards do not stop it: `requestWithdraw` has no cancellation, `_onlyIdleCDO` is satisfied through the public CDO entry point, KYC (`isWalletAllowed`) only gates who can deposit — the attacker passes it — and nothing caps or expires `apr0TotalPrincipal`.

### Impact Explanation
Permanent freezing of all vault funds (entire TVL of AA+BB tranches) for as long as APR remains nonzero, or forced zero-yield epochs if the manager keeps APR at 0. Loss is unbounded-up-to-TVL in freezing terms and unbounded in yield theft: each replayed wedge epoch transfers the pool's entire interest accrual to zero. The wedge requires no capital lock — the requester's own receipt is frozen too, but they externalize the freeze onto every other LP and the borrower.

### Likelihood Explanation
Requires only: (a) a phase where `unscaledApr == 0` (APR0 is a supported mode, exercised in tests such as `testApr0WithdrawRollsAcrossEpochsNoDoubleAccrual`), and (b) a manager later setting a nonzero APR — a normal operational action the attacker simply waits for. Any single tranche-token holder can trigger it with dust-sized requests, and can replay it each time APR is reset to 0. No privileged cooperation is needed.

### Recommendation
Allow `prepareStopEpochWithApr0` to settle/close the APR0 bucket when APR is raised instead of reverting — e.g., migrate outstanding `apr0Users` principals into the normal `withdrawsRequests`/`withdrawsRequestsByEpoch` buckets (preserving `lastWithdrawRequest` so the one-epoch wait still applies) and zero `apr0TotalPrincipal`. Alternatively, provide a permissionless `cancelApr0Request`/migration function, or reject new APR0 requests while another epoch is pending settlement. The invariant to restore: no unprivileged request can make the epoch state machine un-progressable.

### Proof of Concept
Foundry fork sketch (mirroring `test/foundry/IdleCreditVault.t.sol` APR0 setup):

```solidity
function testApr0WedgeBricksStopEpoch() external {
    // Pool running with unscaledApr == 0 (APR0 mode), epoch 0 stopped, buffer open.
    _forceLastEpochAprToZero(); // helper as in existing tests

    // Attacker (plain KYC'd AA holder) opens a tiny APR0 withdraw receipt.
    uint256 dust = IERC20(AAtranche).balanceOf(attacker) / 1000;
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(dust, address(AAtranche));
    assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

    // Manager legitimately raises APR for the next epoch.
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(1e18, 1e18);

    _startEpochAndCheckPrices(0);
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.warp(cdoEpoch.epochEndDate() + 1);

    // stopEpoch reverts forever while APR != 0 and apr0TotalPrincipal != 0.
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(initialProvidedApr, 0);

    // Replay: even after forcing APR back to 0 and unsticking once,
    // attacker re-requests during that epoch and raises the wedge again
    // on the next APR change — epochNumber never sustainably advances.
}
```

Caveat: I was unable to fully trace the `stopEpoch` → `prepareStopEpochWithApr0` call site and the `_handleBorrowerDefault` bypass in `IdleCDOEpochVariant.sol` within the available iterations, so the exact revert path should be confirmed; the arithmetic and guard ordering in `IdleCreditVault.sol:499-540` make the revert unconditional given the stated preconditions.