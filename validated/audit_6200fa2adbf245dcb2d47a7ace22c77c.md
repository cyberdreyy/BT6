### Title
Raising APR above zero while APR=0 withdraw requests are pending permanently reverts `stopEpoch`, freezing all lender funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
When `unscaledApr == 0`, user withdraw requests are routed into a separate APR0 accounting bucket (`apr0TotalPrincipal`). At `stopEpoch`, the CDO calls `prepareStopEpochWithApr0`, which reverts with `NotAllowed()` whenever `apr0TotalPrincipal != 0 && unscaledApr != 0`. An unprivileged lender can therefore pin the vault: as long as they keep an open APR0 withdraw request, any subsequent APR increase by the honest manager makes every `stopEpoch` call revert, so the epoch can never end and no withdraw request can ever be claimed or new requests settle — a direct analog of the external `minPosition` freeze, where a guard blocks deallocation and traps user funds.

### Finding Description
In `contracts/strategies/idle/IdleCreditVault.sol`:

- `requestWithdraw` diverts requests made while `unscaledApr == 0` into `_requestWithdrawApr0`, which accumulates `apr0TotalPrincipal` (lines 285-286, 567-577).
- `apr0TotalPrincipal` is only reset inside `prepareStopEpochWithApr0` (line 540), but that same function reverts before reaching the reset when the APR was raised:

```solidity
uint256 _principal = apr0TotalPrincipal;
if (_principal == 0) {
    return (_expInterest, _adjPendingWithdrawFees);
}
// APR0 principal is only valid while APR is 0 for that request lifecycle.
if (unscaledApr != 0) {
    revert NotAllowed();
}
```
(lines 501-508)

- Because the revert happens before `apr0TotalPrincipal = 0`, the stuck bucket can never be cleared while `unscaledApr != 0`. Meanwhile, `_claimFundedWithdrawRequest` reverts (`epochNumber <= lastWithdrawRequest`) until `stopEpoch` bumps `epochNumber` (lines 326-328), and `_settleApr0` refuses settlement until `epochNumber` advances (lines 551-555). So while APR is nonzero, APR0 receipt holders can neither claim nor have their bucket closed — and, more importantly, `stopEpoch` reverting freezes the epoch for *all* users (normal withdraw claimants, depositors waiting for interest accounting, tranche holders).

- `setApr`/`setAprs` is callable by the CDO or the manager (lines 225-235). The manager is honest per the threat model, but raising APR is a routine operational action (e.g., reacting to market rates); nothing warns the manager that an open APR0 bucket makes the change epoch-bricking. The attacker needs no privilege: during a legitimately-configured APR=0 period (a supported mode of the vault), they deposit via the CDO, `requestWithdraw` a dust amount, and simply never claim. The freeze lasts until the manager sets APR back to exactly 0 — i.e., the pool's pricing is held hostage by an unprivileged dust request.

### Impact Explanation
Temporary freezing of the entire vault's funds: while `unscaledApr != 0` and a nonzero `apr0TotalPrincipal` exists, every `stopEpoch` reverts, `epochNumber` never advances, all pending withdraw claims revert in `_claimFundedWithdrawRequest`, and borrower interest settlement halts. The locked amount equals the full CDO TVL plus pending receipts for the duration of the freeze, and recovery requires reverting pool pricing to APR=0 — an attacker can re-open an APR0 request each such epoch to re-arm the condition, so the vault can effectively never raise its APR while the attacker is willing to keep dust receipts open.

### Likelihood Explanation
Triggering requires (1) an APR=0 configuration period, which is an explicitly supported mode (`apr0Users`, `apr0RateByEpoch`, `_requestWithdrawApr0` are dedicated code paths), (2) one unprivileged `requestWithdraw` of any size during that period, and (3) a subsequent manager APR update — a routine, non-malicious action. No default, no privileged misbehavior, and no external oracle dependence is needed. Existing guards do not stop it: `maxApr` only caps the APR value, `_onlyIdleCDO` gates are satisfied on the normal path, and the `lossRecoveryPriceByEpoch` claim-before-request check is orthogonal.

### Recommendation
Do not revert on `unscaledApr != 0` in `prepareStopEpochWithApr0`. Instead, settle the pending APR0 bucket at the APR that was in effect when the requests were created (store the per-epoch unscaled APR at request time, or snapshot `lastApr`/`unscaledApr` per epoch), then clear `apr0TotalPrincipal` unconditionally. Alternatively, checkpoint `unscaledApr` at `stopEpoch` start and evaluate the APR0 split against the request-epoch value, so manager APR changes can never make the epoch-terminating path revert.

### Proof of Concept
Foundry-style fork test (setup mirrors `test/foundry/IdleCDOEpochQueue.t.sol`):

```solidity
function test_Apr0PendingBlocksStopEpochAfterAprRaise() external {
    // Epoch 0 running with unscaledApr == 0 (vault in APR0 mode).
    vm.prank(manager);
    cdoEpoch.setAprs(0, 0); // or strategy in APR0 config at deploy

    // Unprivileged lender deposits and requests withdraw during APR0 epoch.
    deal(address(underlying), ALICE, 100e6, true);
    vm.startPrank(ALICE);
    underlying.approve(address(cdoEpoch), 100e6);
    cdoEpoch.depositAA(100e6);
    cdoEpoch.requestWithdraw(1e6, address(trancheAA)); // dust receipt
    vm.stopPrank();
    assertGt(strategy.apr0TotalPrincipal(), 0);

    // Epoch ends; manager runs start/stop cycle, then honestly raises APR.
    _stopCurrentEpochWithApr(0);
    vm.prank(manager);
    cdoEpoch.startEpoch();
    // new epoch running with APR0 still configured; attacker requests again
    vm.prank(ALICE);
    cdoEpoch.requestWithdraw(1e6, address(trancheAA)); // apr0TotalPrincipal > 0

    // Manager raises APR for the next epoch (legitimate operation).
    vm.prank(manager);
    strategy.setAprs(5e18, 5e18);

    // Epoch end arrives: every stopEpoch path now reverts NotAllowed via
    // prepareStopEpochWithApr0 (apr0TotalPrincipal != 0 && unscaledApr != 0).
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.expectRevert(NotAllowed.selector);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, /* borrower repayment funded */);

    // Consequences: epochNumber frozen, so ALICE's and all other pending
    // withdraw claims revert in _claimFundedWithdrawRequest.
    vm.expectRevert(NotAllowed.selector);
    vm.prank(ALICE);
    cdoEpoch.claimWithdrawRequest();
}
```

Expected result: `stopEpoch` reverts while `unscaledApr != 0`; the vault only recovers when the manager restores `unscaledApr = 0`, confirming a temporary freeze of all funds gated on reverting a legitimate pricing action — matching the "position limit blocks deallocation → account freeze" class of the source report.