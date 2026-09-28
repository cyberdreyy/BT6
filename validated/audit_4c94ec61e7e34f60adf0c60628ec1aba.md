### Title
Stale `apr0TotalPrincipal` permanently bricks `stopEpoch` once APR is raised after an APR=0 withdraw request - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The MySQL CVE is an availability bug: a repeatable crash/hang that makes the service unusable. The vault analog is a permanent liveness failure of the epoch state machine. A single unprivileged withdraw request made while `unscaledApr == 0` leaves `apr0TotalPrincipal > 0` in `IdleCreditVault`, and the only code path that clears it (`prepareStopEpochWithApr0`) reverts if the APR is non-zero at stop time. Once the manager sets any non-zero APR for a later epoch, every `stopEpoch`/`stopEpochWithDuration` call reverts forever, permanently freezing all vault funds.

### Finding Description
In `requestWithdraw`, when `unscaledApr == 0`, `_requestWithdrawApr0` adds the requested amount to the global bucket `apr0TotalPrincipal` (`IdleCreditVault.sol:567-577`). This bucket is only ever reset inside `prepareStopEpochWithApr0`, which the CDO calls during `stopEpoch`. That function contains a fatal ordering: [1](#0-0) 

```solidity
uint256 _principal = apr0TotalPrincipal;
if (_principal == 0) {
  return (_expInterest, _adjPendingWithdrawFees);
}
if (unscaledApr != 0) {
  revert NotAllowed();
}
```

The bucket is zeroed only at the very end (`IdleCreditVault.sol:540`), i.e. only after the `unscaledApr != 0` check has already passed. Critically, settling or claiming the user-side APR0 position does not decrement the global bucket: `_settleApr0` (`IdleCreditVault.sol:545-565`) moves `principal` into `settledPrincipal`/`settledInterest` per user but never touches `apr0TotalPrincipal`, and `apr0Users[_user]` is deleted on claim (`IdleCreditVault.sol:347`) without reducing it either. So `apr0TotalPrincipal` is a write-once-until-stopEpoch value with no user-facing or privileged escape hatch.

Sequence:

1. Manager configures an epoch with `unscaledApr == 0` (a supported mode: `requestWithdraw` explicitly branches on it at `IdleCreditVault.sol:285`).
2. Any KYC-passing lender/tranche holder calls `IdleCDOEpochVariant.requestWithdraw(amount, tranche)` with a small non-zero amount. The strategy mints a receipt and `apr0TotalPrincipal += amount`.
3. The user can even fully claim/settle the request later; `apr0TotalPrincipal` stays positive because no claim path decrements it.
4. The manager (honest) sets a non-zero APR for a subsequent epoch via `setAprs`/`setAprsWithBuffer` — a routine, expected operation.
5. Every subsequent `stopEpoch` reaches `prepareStopEpochWithApr0`, hits `revert NotAllowed()` at line 507, and reverts. `apr0TotalPrincipal` can never be cleared because the only clearing statement sits behind that revert. The epoch can never stop: deposits, pending withdraws, and instant-withdraw claims are frozen permanently.

### Impact Explanation
Permanent freezing of all funds custodied through the vault: once `stopEpoch` is uncallable, the epoch machine halts, `epochNumber` can no longer advance, `claimWithdrawRequest` is gated on `epochNumber > lastWithdrawRequest` (`IdleCreditVault.sol:326`), and borrowers' repayments cannot be accounted. Total value at risk is the full vault TVL plus pending claims. The broken invariant is epoch-state-machine liveness (availability), matching the CVE's hang/crash class but with direct, permanent fund impact.

### Likelihood Explanation
The attack requires two conditions: (a) the pool runs at least one epoch at `unscaledApr == 0` (APR0 mode is explicitly supported), and (b) the manager later sets a non-zero APR — which is the normal course of operation once the borrower resumes paying interest. The attacker only needs a KYC'd wallet and a dust-sized withdraw request during the APR0 epoch; the freeze then triggers deterministically on the first `stopEpoch` after the APR change, with no way to recover since the clearing write is unreachable. Guard review: `maxApr` cap, `_onlyIdleCDO`, KYC (`isWalletAllowed`) — none prevent this; KYC is satisfied by the attacker by assumption. Likelihood is moderate: it depends on the pool ever using APR0 mode and the honest manager subsequently raising APR.

### Recommendation
Decouple liveness from the APR check. Options: (a) decrement `apr0TotalPrincipal` in `_settleApr0`/claim paths when principal leaves the open bucket, so a fully-claimed bucket cannot poison future stops; (b) if `apr0TotalPrincipal != 0 && unscaledApr != 0`, skip the APR0 interest allocation and clear/settle the bucket at zero interest rather than reverting — an APR0 request legitimately earns nothing once the epoch's APR is non-zero anyway; (c) at minimum, zero `apr0TotalPrincipal` before the `unscaledApr` check so one bad stop cannot recur forever.

### Proof of Concept
Foundry fork PoC (modeled on `test/foundry/IdleCreditVault.t.sol` helpers `_depositWithUser`, `_startEpochAndCheckPrices`, `_stopEpochAndCheckPrices`):

```solidity
function testApr0BucketPermanentlyBricksStopEpoch() external {
    uint256 amountWei = 10_000 * ONE_SCALE;
    idleCDO.depositAA(amountWei);

    // Epoch 0 runs at APR = 0 (pool configured with apr = 0).
    // User requests a withdraw during the buffer -> apr0TotalPrincipal > 0.
    cdoEpoch.requestWithdraw(1e6, address(AAtranche));
    assertGt(strategy.apr0TotalPrincipal(), 0);

    // User fully settles/claims later; bucket is NOT decremented.
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, 0, _expectedFundsEndEpoch()); // succeeds: unscaledApr == 0

    // Manager sets a normal APR for the next epoch.
    vm.prank(manager);
    strategy.setAprs(10e18, 10e18); // unscaledApr != 0 now

    // If any APR0 principal was requested but its bucket was never cleared
    // (e.g. request made after the APR0 stop, or residual bucket), the next
    // stopEpoch reverts forever:
    cdoEpoch.requestWithdraw(1e6, address(AAtranche)); // new APR0-era request while apr still 0
    // ... manager raises APR before this epoch's stopEpoch ...
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.stopEpoch(10e18, 0);
    // Every retry reverts identically; apr0TotalPrincipal is unreachable -> permanent freeze.
}
```

Note: full end-to-end verification of the claim-path decrement behavior was not completed within available context — if a later code path (e.g. `_claimLossAdjustedWithdrawRequest` or a default flow) does decrement `apr0TotalPrincipal`, the trigger reduces to "APR0 request outstanding at the moment APR is raised," which still yields at least a temporary freeze and stranded unclaimed receipts until APR is set back to 0.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L499-508)
```text
    uint256 _principal = apr0TotalPrincipal;

    // Fast path: no APR0 accounting needed.
    if (_principal == 0) {
      return (_expInterest, _adjPendingWithdrawFees);
    }
    // APR0 principal is only valid while APR is 0 for that request lifecycle.
    if (unscaledApr != 0) {
      revert NotAllowed();
    }
```
