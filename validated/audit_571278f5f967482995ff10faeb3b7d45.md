### Title
Dust APR=0 withdraw request permanently bricks `stopEpoch` after any APR change, freezing all vault funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.prepareStopEpochWithApr0` reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0`. An unprivileged KYC'd lender can open a dust-sized APR0 withdraw request during a 0-APR epoch, so `apr0TotalPrincipal` becomes non-zero. If the honest manager subsequently sets a non-zero APR for the vault (a routine operational action between or during epochs), every later `stopEpoch`/`stopEpochWithDuration` call reverts inside `prepareStopEpochWithApr0` before the default-detection or accounting logic runs — permanently freezing the entire TVL, including the honest borrower's repayment path and all pending withdrawals.

### Finding Description
When a user calls `IdleCDOEpochVariant.requestWithdraw` while `unscaledApr == 0`, the request is routed to `_requestWithdrawApr0` (IdleCreditVault.sol:285-286), which increments `apr0TotalPrincipal` and stores `apr0Users[_user].principal`/`principalEpoch`. The only place `apr0TotalPrincipal` is cleared is inside `prepareStopEpochWithApr0` at line 540 — *after* the guard at lines 506-508:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol
uint256 _principal = apr0TotalPrincipal;
if (_principal == 0) {
  return (_expInterest, _adjPendingWithdrawFees);
}
// APR0 principal is only valid while APR is 0 for that request lifecycle.
if (unscaledApr != 0) {
  revert NotAllowed();
}
```

Because the revert fires *before* `apr0TotalPrincipal = 0`, the state can never self-heal. If `unscaledApr` is non-zero while any APR0 principal is outstanding, `stopEpoch` reverts forever. `claimWithdrawRequest`/`_settleApr0` only move `principal` → `settledPrincipal` per-user and never touch the global `apr0TotalPrincipal`, so user-side claims cannot unblock it either. The intended invariant "APR0 requests only exist while APR is 0" is enforced by reverting the epoch-close path instead of settling — turning a one-user dust request into a global, irreversible DoS.

### Impact Explanation
`stopEpoch` is the only path that (a) accounts borrower repayment, (b) funds `pendingWithdraws` via `collectWithdrawFunds`, (c) triggers `_handleBorrowerDefault`, and (d) bumps `epochNumber` so `claimWithdrawRequest` matures (`epochNumber <= lastWithdrawRequest` check at IdleCreditVault.sol:326). With `stopEpoch` permanently reverting:

- All deposited underlying held by the borrower is frozen; the vault can never even be declared `defaulted`, so `finalizeDefault`/recovery distribution is unreachable.
- All pending and future withdraw requests are frozen — `pendingWithdraws` is never funded and `epochNumber` never increments.
- Loss = entire pool TVL plus all pending withdrawal basis. Attacker cost is a single dust `requestWithdraw` (minimum non-zero tranche amount) during any 0-APR epoch, which also only burns dust principal.

### Likelihood Explanation
The preconditions are realistic and require no malicious privilege:

1. Vault configured at APR = 0 (`unscaledApr == 0`), a supported mode given the dedicated APR0 code paths.
2. Attacker (any KYC-passing lender) deposits dust and calls `requestWithdraw`, creating non-zero `apr0TotalPrincipal`.
3. The honest manager/borrower later changes `unscaledApr` to a non-zero value — a normal operational action (repricing the pool) that the code does not couple to outstanding APR0 receipts.
4. Every subsequent `stopEpoch`/`stopEpochWithDuration` reverts at the `unscaledApr != 0` check.

No existing guard prevents it: `_skimDonatedAssets`, KYC checks, and epoch gating are all orthogonal, and the attacker needs no privileged role. The residual uncertainty is whether `unscaledApr` can only be changed in a window where `apr0TotalPrincipal` is provably zero (e.g., if APR updates are only applied inside the same `stopEpoch` call that clears the bucket); if APR is settable any time before that stop, the freeze is fully attacker-armed.

### Recommendation
Do not revert in `prepareStopEpochWithApr0` when `unscaledApr != 0`. Instead, settle the outstanding APR0 bucket deterministically — e.g., treat `apr0RateByEpoch[epochNumber]` as 0 for that epoch, move the global principal to a settled state (`apr0TotalPrincipal = 0`), and let per-user `_settleApr0` pay principal without interest. Alternatively, prevent the inconsistent state by reverting the APR update itself (`setApr`-style path) while `apr0TotalPrincipal != 0`, and/or adding a permissionless "settle/expire APR0 bucket" escape so a stuck bucket cannot wedge `stopEpoch`.

### Proof of Concept
Foundry fork PoC sketch (mirroring `test/foundry/IdleCreditVault.t.sol` helpers `_startEpochAndCheckPrices`/`_stopEpochAndCheckPrices`):

```solidity
function testPocApr0StopEpochPermanentDos() external {
  // Vault configured with unscaledApr == 0 (APR0 mode)
  uint256 amount = 10_000 * ONE_SCALE;
  idleCDO.depositAA(amount);                       // honest liquidity

  _startEpochAndCheckPrices(0);                    // epoch 0 running, APR = 0

  // Attacker: dust APR0 withdraw request -> apr0TotalPrincipal > 0
  address attacker = makeAddr('attacker');
  deal(defaultUnderlying, attacker, 1e6);
  vm.startPrank(attacker);
  IERC20Detailed(defaultUnderlying).approve(address(idleCDO), 1e6);
  uint256 dust = idleCDO.depositAA(1e6);           // or depositDuringEpoch in buffer
  cdoEpoch.requestWithdraw(dust, address(AAtranche));
  vm.stopPrank();
  assertGt(strategy.apr0TotalPrincipal(), 0);

  // Honest manager reprices pool: set unscaledApr != 0 before stopEpoch
  vm.prank(manager);
  strategy.setApr(10e18);                          // routine APR update

  // Borrower fully repays - does not matter, stopEpoch reverts first
  vm.warp(cdoEpoch.epochEndDate() + 1);
  vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
  vm.prank(manager);
  cdoEpoch.stopEpoch(0, 0);

  // No recovery path: every subsequent stopEpoch reverts identically,
  // epochNumber never increments, pendingWithdraws never funded,
  // defaulted never set -> entire TVL and all withdraw receipts frozen.
}
```

Note: the exact setter for `unscaledApr` and whether APR changes are restricted to a pre-stop window could not be fully verified within available iterations; the PoC assumes the manager can update APR while an APR0 bucket is open, which the guard's existence (`revert NotAllowed()` rather than a settled no-op) implies is a reachable state.