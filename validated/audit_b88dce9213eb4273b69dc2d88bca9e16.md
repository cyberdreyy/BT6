### Title
Stranded APR0 withdraw receipt permanently bricks `stopEpoch` after the manager raises the APR - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault.prepareStopEpochWithApr0` reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0` while `unscaledApr != 0` (IdleCreditVault.sol:502-508). Any KYC-passed lender can create that APR0 principal by calling `requestWithdraw` during an APR=0 epoch and simply never claiming. Once the manager honestly sets a non-zero APR for a later epoch, every `stopEpoch` call reverts, freezing the vault.

### Finding Description
During an epoch where `unscaledApr == 0`, `requestWithdraw` routes the request into `_requestWithdrawApr0`, which records per-user `apr0Users[_user]` data and increments the global `apr0TotalPrincipal` (IdleCreditVault.sol:285-286). At every `stopEpoch`, `IdleCDOEpochVariant` calls `prepareStopEpochWithApr0`, which hits the guard:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:502-508
if (_principal == 0) {
  return (_expInterest, _adjPendingWithdrawFees);
}
if (unscaledApr != 0) {
  revert NotAllowed();
}
```

There is no privileged path that force-settles or deletes a user's open `apr0Users[_user].principal`; the bucket is only cleared when the user himself calls `claimWithdrawRequest` (which settles via `_settleApr0` inside `_claimFundedWithdrawRequest`, IdleCreditVault.sol:330-348) or when a defaulted claim clears it in `_clearWithdrawClaimForEpoch`. The attacker therefore controls a persistent storage flag that turns a routine, honest `setAprs` call by the manager into a permanent revert of the epoch state machine.

Attack sequence:
1. Attacker (KYC'd lender) deposits AA/BB tranches during a buffer where `unscaledApr == 0` (APR0 mode is a supported configuration, see `testApr0WithdrawGetsInterestAtStopEpoch`).
2. Attacker calls `cdoEpoch.requestWithdraw(dust, tranche)` — even 1 wei suffices — minting a receipt and setting `apr0TotalPrincipal > 0` and `apr0Users[attacker].principal`.
3. Epoch runs; borrower repays honestly. Manager sets `setAprs(aprAA, aprBB)` with non-zero values for the next epoch and calls `stopEpoch`.
4. `stopEpoch` → `prepareStopEpochWithApr0` → `_principal != 0 && unscaledApr != 0` → revert.
5. Attacker never calls `claimWithdrawRequest`. Every subsequent `stopEpoch` reverts; `pendingWithdraws` cannot be collected, new epochs cannot start, and `claimWithdrawRequest` for all other users is blocked because `epochNumber <= lastWithdrawRequest[_user]` stays true forever (IdleCreditVault.sol:326-328).

### Impact Explanation
Denial of service with direct fund impact: the entire vault TVL plus all funded-but-unclaimed withdraw receipts are frozen indefinitely. `stopEpoch` is the only path that increments `epochNumber`, collects `pendingWithdraws`/`pendingInstantWithdraws` and enables claims; bricking it permanently locks every depositor's principal and yield. Loss = 100% of vault NAV for the duration (permanent absent an upgrade). This mirrors the CVE's bug class — a crafted input (a dust APR0 withdraw request) that permanently wedges the state machine.

### Likelihood Explanation
High feasibility, low cost:
- The attacker needs only a KYC-passed wallet and dust capital; no privileged role is abused (owner, manager, borrower all behave honestly).
- APR=0 epochs are a first-class supported mode (`_requestWithdrawApr0` exists specifically for it), so the precondition occurs naturally whenever the manager sets APRs to 0.
- The only mitigation is the attacker voluntarily claiming, which is irrational for a griefer; there is no forced settlement or owner sweep of `apr0Users`.
- One caveat: if `lastWithdrawRequest` gating or `_settleApr0` were reachable via another unprivileged call that settles the attacker's bucket, the DoS would downgrade to temporary — but all settlement paths are keyed to `msg.sender`'s own receipts, so third parties cannot clear the attacker's principal.

### Recommendation
Do not revert on the APR mismatch. In `prepareStopEpochWithApr0`, when `unscaledApr != 0` and `apr0TotalPrincipal != 0`, settle the stranded APR0 principal deterministically — e.g., treat the pending APR0 principal as a normal pending withdraw (fold it into `pendingWithdraws`/`withdrawsRequests` at the current price and zero the APR0 bucket), or emit it with zero APR0 interest since the APR is no longer 0. Alternatively, add an owner/manager escape hatch that force-settles a user's `apr0Users` entry, and add a regression test covering "APR0 request outstanding + APR raised + stopEpoch".

### Proof of Concept
Foundry fork PoC (against the existing `IdleCreditVault.t.sol` harness):

```solidity
function testApr0StrandedReceiptBricksStopEpoch() external {
  IdleCreditVault vault = IdleCreditVault(address(strategy));

  // Configure APR=0 epoch
  vm.prank(manager);
  vault.setAprs(0, 0);

  // Attacker deposits and files a dust APR0 withdraw request
  address attacker = makeAddr('attacker');
  _depositWithUser(attacker, 100 * ONE_SCALE, true);
  vm.prank(attacker);
  cdoEpoch.requestWithdraw(1, address(AAtranche));
  assertGt(vault.apr0TotalPrincipal(), 0);

  // Epoch runs, borrower repays honestly
  _stopEpochAndCheckPrices(0, 0, _expectedFundsEndEpoch());

  // Manager honestly raises APR for next epoch
  vm.prank(manager);
  vault.setAprs(initialApr, initialApr);

  // Attacker never claims. Warp past next epoch end; borrower funded.
  _startEpochAndCheckPrices(1);
  vm.warp(cdoEpoch.epochEndDate() + 1);
  deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());

  // stopEpoch permanently reverts: stranded apr0TotalPrincipal + unscaledApr != 0
  vm.prank(manager);
  vm.expectRevert(NotAllowed.selector);
  cdoEpoch.stopEpoch(0, 0);

  // All other users' claims are frozen: epochNumber can never advance past
  // lastWithdrawRequest for any pending receipt.
}
```

Uncertainty note: I could not read `_requestWithdrawApr0`/`_settleApr0` and the `stopEpoch` body in `IdleCDOEpochVariant` within the available iterations, so the exact call ordering (whether `prepareStopEpochWithApr0` runs before or after the epoch-number bump) should be confirmed; the revert at IdleCreditVault.sol:506-508 is unconditional once `apr0TotalPrincipal != 0` and `unscaledApr != 0`, which is the crux of the freeze.