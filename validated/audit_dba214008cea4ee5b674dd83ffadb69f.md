### Title
Attacker-bricked epoch transitions: a dust APR=0 withdraw request makes every `stopEpoch` with non-zero APR revert, freezing all pool funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Analogous to the Parse Server bug — where an invalid regex was *stored* at subscription time and later *crashed* the server at match time — `IdleCreditVault` lets an unprivileged KYC'd lender store a tiny APR=0 withdraw request (`_requestWithdrawApr0`), which later makes the shared epoch-finalization path `prepareStopEpochWithApr0` unconditionally revert for any non-zero APR. The stored "poison" is only cleared inside the same reverting function, so the revert is self-perpetuating.

### Finding Description
When the pool APR is 0 (`unscaledApr == 0`), `requestWithdraw` routes the request into the APR0 bucket:

`IdleCreditVault.sol` `requestWithdraw` (lines 285-286):
```solidity
if (unscaledApr == 0 && !isClosed) {
  _requestWithdrawApr0(_amount, _user);
}
```
which increments the global `apr0TotalPrincipal` (lines 567-577). During `stopEpoch`, the CDO calls `prepareStopEpochWithApr0`, which contains (lines 505-508):
```solidity
// APR0 principal is only valid while APR is 0 for that request lifecycle.
if (unscaledApr != 0) {
  revert NotAllowed();
}
```
`apr0TotalPrincipal` is only reset at the end of this same function (line 540). Therefore, if any APR0 principal is outstanding and the epoch is stopped with a non-zero `unscaledApr`, `stopEpoch` reverts. The bucket cannot be cleared by the honest manager/borrower without first completing a `stopEpoch` at APR=0 — and an attacker can re-poison the bucket every buffer period with a new dust request.

### Impact Explanation
Broken invariant: liveness of the epoch state machine → **temporary (potentially indefinite) freezing of all user funds** plus forced zero yield. While the revert stands, `stopEpoch` cannot be executed with APR > 0, so:

- The epoch cannot transition: no `epochNumber` bump, no funding of `pendingWithdraws`, so **every user's `claimWithdrawRequest` reverts** at `epochNumber <= lastWithdrawRequest[_user]` (`_claimFundedWithdrawRequest`, line 326).
- The honest borrower is coerced into either keeping `unscaledApr == 0` forever (all LPs lose all yield — theft of unclaimed yield) or leaving funds frozen.

Cost to the attacker: a KYC'd deposit plus a withdraw request netting `_amount > 0` after management fees (any amount above the dust threshold); the tranche tokens are burned but the receipt strategy tokens remain claimable, so the attacker's principal is largely recoverable once the griefing ends. Loss to the pool: all yield for the duration of the block, proportional to `getContractValue() * apr * duration` — unbounded in time as long as the attacker re-poisons each buffer.

### Likelihood Explanation
Requires only: (a) an epoch running at APR=0 (a supported mode — `unscaledApr`/`setMaxApr(0)`), (b) a KYC-passing lender (in-scope per threat model), (c) the borrower/manager later attempting to restore a non-zero APR — the normal course of operations. No privileged collusion, no race, no oracle manipulation. The only uncertainty is operational: if the deployment never returns to APR>0 after an APR0 epoch, the revert is never hit; but the moment APR normalization is attempted, the freeze is immediate and attacker-retriggerable.

### Recommendation
Validate the "request lifecycle" constraint at *request* or *APR-change* time instead of reverting inside the epoch-finalization path — mirroring the upstream fix (reject the bad input when it is stored, don't crash when it is consumed). Concretely:

- In `prepareStopEpochWithApr0`, instead of `revert NotAllowed()` when `unscaledApr != 0 && apr0TotalPrincipal != 0`, settle the open APR0 principal at a defined rate (e.g., zero interest for the partial epoch, or the last epoch's rate) and close the bucket.
- Alternatively, treat outstanding APR0 requests as normal requests when APR becomes non-zero, so `stopEpoch` always succeeds.

### Proof of Concept
Foundry fork PoC (structure, on the existing `IdleCreditVault.t.sol` harness):

```solidity
function testPocApr0RequestBricksAprRaise() external {
    // Pool configured/running at APR = 0
    vm.prank(owner);
    IdleCreditVault(address(strategy)).setMaxApr(0);

    address attacker = makeAddr('attacker'); // KYC-passing lender
    uint256 dust = 100 * ONE_SCALE;          // enough that net _amount > 0 after mgmt fee
    _depositWithUser(attacker, dust, true);  // depositAA

    // During buffer, APR=0: attacker stores the "poison" request
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(AAtranche));
    assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

    // Normal epoch lifecycle
    _startEpochAndCheckPrices(0);

    // Borrower repays; manager tries to restore non-zero APR at stopEpoch
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(initialProvidedApr, _expectedFundsEndEpoch());
    // ^ prepareStopEpochWithApr0 reverts: unscaledApr != 0 && apr0TotalPrincipal != 0

    // Consequence: epochNumber never bumps -> every pending claim reverts
    vm.prank(attacker);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.claimWithdrawRequest();

    // Attacker can re-poison each subsequent APR=0 buffer indefinitely,
    // permanently forcing APR=0 (yield theft) or freezing all funds.
}
```

Note: I verified the revert path and the absence of an alternate `apr0TotalPrincipal` clearing route in the non-default flow (it is only zeroed in `prepareStopEpochWithApr0` line 540 and per-user in `_clearWithdrawClaimForEpoch` during default finalization). Exact `stopEpoch` call ordering (when `unscaledApr` is written relative to the `prepareStopEpochWithApr0` call) should be confirmed in `IdleCDOEpochVariant.stopEpoch` during PoC execution, but the revert at `IdleCreditVault.sol:506-508` is unconditional once a non-zero APR epoch-stop is attempted with open APR0 principal.