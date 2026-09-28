### Title
APR0 withdraw bucket permanently bricks `stopEpoch` once APR is raised — an unprivileged lender can freeze the epoch or force all future yield to zero - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
A KYC-passing lender can open an APR0 withdraw request while `unscaledApr == 0`. That request leaves `apr0TotalPrincipal > 0`. `prepareStopEpochWithApr0` hard-reverts whenever `apr0TotalPrincipal != 0 && unscaledApr != 0` (`IdleCreditVault.sol:505-508`), and `apr0TotalPrincipal` is only ever cleared inside that same reverting function (line 540) or by default-epoch claims. Once the honest manager raises the APR (normal operation between epochs), every subsequent `stopEpoch`/`stopEpochWithDuration` reverts: the epoch can never close, pending receipts can never be funded, and tranche holders cannot exit. The only recovery is to set the APR back to 0 — letting the attacker permanently suppress pool interest — or wait for default finalization. This mirrors the Broke Window Attack: a small unprivileged "credit" (the APR0 bucket) exhausts the window through which the whole epoch must flow.

### Finding Description
- `requestWithdraw` → `IdleCreditVault.requestWithdraw` routes to `_requestWithdrawApr0` when `unscaledApr == 0`, incrementing the global `apr0TotalPrincipal` (lines 285–286, 567–577).
- `prepareStopEpochWithApr0` returns early only if `apr0TotalPrincipal == 0`; otherwise it reverts if `unscaledApr != 0` (lines 499–508). It is called unconditionally by `IdleCDOEpochVariant.stopEpoch` before any borrower funding logic (`IdleCDOEpochVariant.sol:362`).
- Nothing else decrements `apr0TotalPrincipal` in a non-defaulted pool: `_settleApr0` only settles per-user data, `_claimFundedWithdrawRequest` never touches the global bucket, and `_clearWithdrawClaimForEpoch` reduces it only on the default-claim path (lines 545–565, 821–828).
- Attack sequence: (1) pool runs an epoch at APR 0; (2) attacker (KYC'd lender) deposits AA and calls `requestWithdraw` during the buffer — `apr0TotalPrincipal` becomes nonzero; (3) manager later sets APR > 0 via `setAprs`; (4) every `stopEpoch` reverts `NotAllowed()` until APR is forced back to 0. If the manager resets APR to 0 to unstick the epoch, the attacker claims, redeposits, and re-requests next buffer — repeating indefinitely.
- Existing guards don't stop it: `isWalletAllowed` (KYC) is satisfied by the attacker, the APR0 routing is the intended path, and the revert is unconditional.

### Impact Explanation
Either (a) the vault is frozen — `stopEpoch` reverts forever, pending withdraw receipts are never funded, `claimWithdrawRequest` reverts because `epochNumber` never advances (`IdleCreditVault.sol:326`), and all TVL stays locked — or (b) the manager is coerced into keeping `unscaledApr == 0` every epoch, permanently zeroing LP yield while the borrower enjoys interest-free credit. Quantified loss: the entire epoch interest on pool TVL per affected epoch (option b), or temporary freezing of the full TVL and all pending claims (option a).

### Likelihood Explanation
Requires only a KYC-passing lender and one deposit+withdraw request during a buffer period while APR is 0 — a normal, intended user action. Triggering the freeze additionally requires the manager to set a nonzero APR afterward, which is the pool's ordinary operating mode. No privileged misbehavior, no oracle manipulation, no large capital.

### Recommendation
Decouple the APR0 bucket from the live APR guard: in `prepareStopEpochWithApr0`, settle/close outstanding APR0 principal at the recorded `apr0RateByEpoch` (or at zero interest) instead of reverting when `unscaledApr != 0`, and clear `apr0TotalPrincipal` in all cases. Alternatively, allow `_settleApr0`/`_claimFundedWithdrawRequest` to decrement `apr0TotalPrincipal` so claims drain the bucket, and prevent new `_requestWithdrawApr0` entries once APR becomes nonzero (already implicit).

### Proof of Concept
Foundry fork PoC outline (mirroring `test/foundry/IdleCreditVault.t.sol` setup, e.g. `testApr0WithdrawGetsInterestAtStopEpoch` at lines 2979–3028):

```solidity
function testApr0BucketBricksStopEpoch() external {
    // Setup: epoch running with unscaledApr == 0 (manager set via strategy.setAprs(0,0))

    // 1. Attacker (whitelisted lender) deposits and requests withdraw during buffer
    uint256 amount = 1000 * ONE_SCALE; // small capital suffices
    idleCDO.depositAA(amount);
    // buffer period, apr still 0
    cdoEpoch.requestWithdraw(0, address(AAtranche));
    assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

    // 2. Honest manager raises APR for the next epoch
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(initialProvidedApr, 0);

    // 3. Epoch ends -> every stopEpoch reverts; epoch cannot close
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, 1e30); // borrower fully funded, still reverts
    vm.prank(manager);
    vm.expectRevert(); // NotAllowed() at prepareStopEpochWithApr0 line ~507
    cdoEpoch.stopEpoch(0, interest);

    // 4. No alternative unwind: claim also reverts since epochNumber never advances
    vm.expectRevert();
    cdoEpoch.claimWithdrawRequest();

    // 5. Recovery requires forcing APR back to 0 (yield suppression) or default
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0); // now succeeds — attacker can repeat next buffer
}
```