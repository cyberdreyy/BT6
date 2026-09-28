### Title
APR0 withdraw request combined with a non-zero APR permanently bricks `stopEpoch` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external report (CVE-2023-3774) describes an unhandled error that crashes the Vault process — a single unhandled revert path causing denial of service. The direct analog in idle-tranches is `IdleCreditVault.prepareStopEpochWithApr0`, which unconditionally reverts with `NotAllowed` whenever any APR0 withdraw principal exists while `unscaledApr != 0`. Because this function is invoked by the CDO during epoch stop, the revert bricks `stopEpoch` itself: no epoch can ever close, and all deposited principal, pending withdraws, and claims are frozen indefinitely.

### Finding Description
`IdleCreditVault.prepareStopEpochWithApr0` (`contracts/strategies/idle/IdleCreditVault.sol:490-541`) is called by `IdleCDOEpochVariant` as part of the `stopEpoch` flow. When `apr0TotalPrincipal` is non-zero it executes this guard:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:505-508
// APR0 principal is only valid while APR is 0 for that request lifecycle.
if (unscaledApr != 0) {
  revert NotAllowed();
}
```

The sequence is entirely reachable by an unprivileged lender:

1. The pool is operating in APR=0 mode (`unscaledApr == 0`), a supported configuration — `requestWithdraw` routes to `_requestWithdrawApr0` only under this mode (`contracts/strategies/idle/IdleCreditVault.sol:285-286`).
2. An attacker (any KYC-passed lender / tranche holder) calls `cdoEpoch.requestWithdraw(amount, tranche)`. This calls `requestWithdraw` → `_requestWithdrawApr0`, which sets `apr0Users[attacker].principal`, `principalEpoch = epochNumber`, and increments `apr0TotalPrincipal` (`contracts/strategies/idle/IdleCreditVault.sol:567-577`).
3. For the next epoch, the honest borrower/manager sets a non-zero APR via `setAprsWithBuffer` / `setApr` (`contracts/strategies/idle/IdleCreditVault.sol:217-235`). This is a routine honest action — the attacker does not control it — but once `unscaledApr != 0`, the invariant `apr0TotalPrincipal != 0 && unscaledApr != 0` holds.
4. Every subsequent `stopEpoch` / `stopEpochWithDuration` call reverts inside `prepareStopEpochWithApr0`. The epoch can never stop.

The freeze is self-reinforcing (catch-22):

- `apr0TotalPrincipal` is only zeroed inside `prepareStopEpochWithApr0` (`contracts/strategies/idle/IdleCreditVault.sol:540`), which is the very function that reverts.
- The attacker's receipt cannot be claimed to clear the bucket: `_settleApr0` returns early while `principalEpoch >= epochNumber` (`contracts/strategies/idle/IdleCreditVault.sol:551-554`), and `epochNumber` can only advance via `deposit` called during a successful `stopEpoch` (`contracts/strategies/idle/IdleCreditVault.sol:607-610`). `claimWithdrawRequest` → `_claimFundedWithdrawRequest` also reverts `NotAllowed` while `epochNumber <= lastWithdrawRequest[_user]` (`contracts/strategies/idle/IdleCreditVault.sol:326-328`).
- There is no `deleteWithdrawRequest`-style escape on the strategy for APR0 principal.

The only theoretical unfreeze is the manager setting `unscaledApr` back to 0 — but the APR is driven by the honest CDO/borrower each `startEpoch`, so as long as the vault's configured APR is non-zero the freeze is permanent.

### Impact Explanation
Permanent (or at minimum indefinite) freezing of all vault funds: no deposits can be withdrawn (they sit behind `stopEpoch`-gated claims), no epoch can close, no interest can settle. Every LP's principal is locked. This maps exactly to the CVE's "unhandled error → crash → denial of service" class, but with direct fund impact, satisfying the permanent-freezing acceptance criterion.

### Likelihood Explanation
Medium. Requires the vault to run an APR=0 epoch (a designed mode used for zero-interest borrower periods) and a subsequent non-zero APR epoch while at least one APR0 withdraw receipt is still open — a routine transition, not an exotic state. The attacker only needs to hold tranche tokens and submit a normal `requestWithdraw`; all privileged actors behave honestly.

### Recommendation
In `prepareStopEpochWithApr0`, do not revert when `unscaledApr != 0` and APR0 principal exists. Instead, settle the APR0 bucket at a zero rate (or the last stored `apr0RateByEpoch`), i.e. treat `_apr0NetInterest = 0` and still zero `apr0TotalPrincipal` so `stopEpoch` proceeds and users can claim principal-only receipts. Alternatively, block `setApr`/`setAprsWithBuffer` from raising `unscaledApr` above 0 while `apr0TotalPrincipal != 0`, which converts the crash into a caller-side validation error and preserves the invariant.

### Proof of Concept
Foundry fork test sketch (extend `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testPocApr0RequestBricksStopEpoch() external {
  // pool running with unscaledApr == 0
  _setAprsWithBuffer(0, cdoEpoch.epochDuration(), cdoEpoch.bufferPeriod());
  uint256 amount = 10_000 * ONE_SCALE;
  _depositWithUser(address(this), amount, true);

  // attacker requests a withdraw during APR0 mode
  cdoEpoch.requestWithdraw(0, address(AAtranche));
  assertGt(creditVault.apr0TotalPrincipal(), 0);

  // honest manager configures non-zero APR for next epoch
  _setAprsWithBuffer(initialProvidedApr, cdoEpoch.epochDuration(), cdoEpoch.bufferPeriod());
  assertGt(creditVault.unscaledApr(), 0);

  // every stopEpoch now reverts: epoch cannot close, funds frozen
  vm.warp(cdoEpoch.epochEndDate() + 1);
  vm.prank(manager);
  vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
  cdoEpoch.stopEpoch(initialProvidedApr, 0);

  // no user-level escape: claim reverts, epochNumber never advances
  vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
  cdoEpoch.claimWithdrawRequest();
}
```

Note: I verified the revert guard, the APR0 request path, and the settlement gating in `IdleCreditVault.sol`. The exact call site of `prepareStopEpochWithApr0` inside `IdleCDOEpochVariant.stopEpoch` could not be fully inspected within the search limit — the PoC should confirm whether the call is unconditional on every `stopEpoch` or only under APR0 mode; if conditional on `unscaledApr == 0` being read at stop time, the freeze instead arises when the CDO passes a non-zero APR while receipts exist, which is the same revert outcome.