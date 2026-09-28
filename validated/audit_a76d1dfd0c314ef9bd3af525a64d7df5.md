### Title
Permanent APR0 withdrawal-request poison causes `stopEpoch` to revert whenever APR is raised, freezing the entire vault epoch cycle - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`prepareStopEpochWithApr0` reverts with `NotAllowed` whenever `apr0TotalPrincipal > 0` and `unscaledApr != 0` (IdleCreditVault.sol:506-508). `apr0TotalPrincipal` is only cleared on line 540 of the same function — after the revert — and there is no user-facing way to cancel a pending withdraw request. Any unprivileged lender can therefore wedge `apr0TotalPrincipal > 0` by calling `requestWithdraw` while APR is 0, such that the next routine `setAprs` by the honest manager turns every subsequent `stopEpoch`/`stopEpochWithDuration` call into a revert, halting the epoch state machine and freezing all pending withdrawals and borrower repayments.

### Finding Description
The bug class of CVE-2024-31948 is "a single malformed/unexpected input crashes the core processing loop." The on-chain analog is `IdleCreditVault.prepareStopEpochWithApr0`, which is called unconditionally by `IdleCDOEpochVariant.stopEpoch`/`stopEpochWithDuration` (IdleCDOEpochVariant.sol:362) before any borrower funds are pulled:

```solidity
// IdleCreditVault.sol
uint256 _principal = apr0TotalPrincipal;
if (_principal == 0) {
  return (_expInterest, _adjPendingWithdrawFees);
}
// APR0 principal is only valid while APR is 0 for that request lifecycle.
if (unscaledApr != 0) {
  revert NotAllowed();            // <- crash point
}
...
apr0TotalPrincipal = 0;           // only reachable after the revert check
```

Attack path:
1. A KYC-passing lender deposits tranche tokens and waits for an epoch where `unscaledApr == 0` (a normal operating mode, exercised throughout `test/foundry/IdleCreditVault.t.sol`).
2. During the buffer period the lender calls `IdleCDOEpochVariant.requestWithdraw(trancheAmount, tranche)`. Because `unscaledApr == 0`, `requestWithdraw` routes to `_requestWithdrawApr0`, which sets `apr0Users[user].principal` and increments `apr0TotalPrincipal` (IdleCreditVault.sol:285-294, 567-577). The request cannot be cancelled — `IdleCreditVault` exposes no delete/cancel for normal requests, and `apr0TotalPrincipal` is only decremented inside the reverting function itself or on default-claim clearing (`_clearWithdrawClaimForEpoch`, IdleCreditVault.sol:827).
3. The honest manager later raises the APR via `setAprs(newApr > 0, ...)` — a routine operation with no guard against pending APR0 requests (`setAprs` only checks `maxApr`).
4. At `epochEndDate`, the manager calls `stopEpoch`. `prepareStopEpochWithApr0` hits `unscaledApr != 0 && apr0TotalPrincipal > 0` and reverts. Every `stopEpoch`, `stopEpochWithDuration`, and therefore `startEpoch`/claim maturation is blocked: `epochNumber` never increments, so `lastWithdrawRequest` gating in `_claimFundedWithdrawRequest` (IdleCreditVault.sol:326) keeps every pending receipt unclaimable, and borrower repayment cannot be pulled.

### Impact Explanation
Temporary freezing of all vault funds with a quantified duration. While the poison persists, the entire TVL (tranche holders' deposits + borrower repayments + all pending withdraw receipts) is locked — `stopEpoch` is the only path that increments `epochNumber`, collects borrower funds via `getFundsFromBorrower`, and matures claims. Recovery requires the manager to diagnose the condition and revert APR to 0 for a full epoch cycle so `prepareStopEpochWithApr0` can run to completion and clear `apr0TotalPrincipal`. Minimum freeze is one full `epochDuration` plus buffer; it is indefinite if operations does not identify the root cause. The attacker pays only the management fee on a withdraw request — the griefing cost is negligible relative to freezing the whole vault.

### Likelihood Explanation
- Attacker cost: a single `requestWithdraw` during any APR=0 epoch. APR0 mode is a first-class supported configuration (multiple tests exercise it), and `requestWithdraw` during APR0 is intended user behavior, not an edge case — the requester may not even be malicious; the state poison arises naturally.
- Trigger dependency: the freeze materializes only if APR is raised while an APR0 request is pending. The manager is honest, but nothing in `setAprs` prevents the change, and the pending-request condition is not surfaced to the caller — the manager can unknowingly brick the next `stopEpoch`. This mirrors the CVE: a valid-looking update carries a poisoned attribute that crashes the daemon.
- Caveat: the revert is a deliberate invariant guard (test `testApr0InvariantRevertsIfAprChangesAfterApr0Request` asserts it). The guard prevents APR0 mis-accounting, but its failure mode — a global, non-self-healing revert in the only epoch-advancement path — converts that guard into a freeze, which appears to be an unaddressed consequence rather than documented behavior.

### Recommendation
Make the guard non-fatal instead of reverting epoch processing:
- In `prepareStopEpochWithApr0`, when `unscaledApr != 0`, settle the APR0 bucket at zero interest (`apr0RateByEpoch[epochNumber] = 0`, `apr0TotalPrincipal = 0`) rather than reverting, or migrate open APR0 principal into `withdrawsRequests`/`withdrawsRequestsByEpoch` so it follows the normal funded path.
- Alternatively, block the state transition at its source: revert inside `setAprs`/`stopEpoch`-initiated APR changes when `apr0TotalPrincipal != 0`, so the manager gets immediate feedback instead of a delayed crash at the next `stopEpoch`, or provide a permissionless `cancelApr0Request` that decrements `apr0TotalPrincipal` and refunds the receipt.

### Proof of Concept
Foundry fork test (adapted from `testApr0InvariantRevertsIfAprChangesAfterApr0Request` and `testApr0WithdrawGetsInterestAtStopEpoch` in `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testApr0PoisonFreezesStopEpoch() external {
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);
    // APR starts > 0
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0); // switch vault to APR0 mode

    // honest lender deposits
    uint256 amount = 10_000 * ONE_SCALE;
    idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);

    // epoch 0 runs and stops at apr 0
    _startEpochAndCheckPrices(0);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, cdoEpoch.expectedEpochInterest());
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);
    _forceLastEpochAprToZero();

    // ATTACKER: single APR0 withdraw request -> apr0TotalPrincipal > 0
    uint256 bal = IERC20(AAtranche).balanceOf(address(this));
    cdoEpoch.requestWithdraw(bal / 2, address(AAtranche));
    assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

    // honest manager starts epoch 1 and raises APR (routine op, no guard)
    _startEpochAndCheckPrices(1);
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(5e18, 20e18);

    // borrower is funded and ready to repay, but stopEpoch now always reverts
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, type(uint128).max);
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 0);

    // all pending claims are frozen: epochNumber cannot advance
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.claimWithdrawRequest();

    // vault only recovers if APR is forced back to 0 for a full epoch cycle
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);
    deal(defaultUnderlying, borrower, IdleCreditVault(address(strategy)).pendingWithdraws());
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0); // succeeds only after APR reverted to 0
}
```