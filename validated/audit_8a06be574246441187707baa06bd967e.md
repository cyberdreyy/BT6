### Title
APR0 withdraw requests permanently brick `stopEpoch` when the manager raises the APR before epoch end - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`prepareStopEpochWithApr0` enforces a `CHECK`-style invariant: if `apr0TotalPrincipal != 0` then `unscaledApr` must be `0`, otherwise it reverts. An unprivileged lender can request a withdrawal while the pool APR is 0 (creating a nonzero `apr0TotalPrincipal`), and the honest manager can later legitimately set a nonzero APR for the next epoch — a normal operation. From that point, every `stopEpoch` call reverts, and no user can ever settle or claim, permanently freezing all vault funds.

### Finding Description
The external bug is a `CHECK` failure triggered by an input shape the code assumed impossible. The analog here is the hard revert at `IdleCreditVault.sol:506-508`:

```solidity
// APR0 principal is only valid while APR is 0 for that request lifecycle.
if (unscaledApr != 0) {
  revert NotAllowed();
}
```

This sits inside `prepareStopEpochWithApr0`, which the epoch CDO calls during `stopEpoch` whenever the strategy is an `IdleCreditVault`. The implicit assumption — "APR0 withdraw principal can never coexist with a nonzero `unscaledApr`" — is not actually enforced anywhere else:

- `requestWithdraw` routes into `_requestWithdrawApr0` and increments `apr0TotalPrincipal` whenever `unscaledApr == 0` at request time (`IdleCreditVault.sol:285-286`, `567-577`). Any KYC'd lender does this via the CDO's withdraw-request path.
- `setApr`/`setAprs`/`setAprsWithBuffer` (`IdleCreditVault.sol:206-235`) let the manager or CDO set a nonzero APR at any time, with only the `maxApr` cap. Nothing checks `apr0TotalPrincipal == 0`.
- Once `apr0TotalPrincipal != 0` and `unscaledApr != 0`, `prepareStopEpochWithApr0` reverts on every call, so `stopEpoch` reverts on every call.
- The deadlock is unbreakable: `apr0TotalPrincipal` is only decremented via `claimWithdrawRequest` → `_settleApr0`, which requires `epochNumber` to advance — which requires `stopEpoch` to succeed (`IdleCreditVault.sol:552-564`). Owner `transferToken` could sweep tokens but cannot repair the epoch state machine or let lenders redeem.

### Impact Explanation
Permanent freezing of all funds in the vault: once triggered, no epoch can be stopped, no lender (including the attacker) can claim normal or APR0 withdraw receipts, instant-withdraw receipts behind the same epoch machine are stranded, and the borrower cannot repay through the normal flow. Total loss is the entire vault TVL plus pending withdraw basis — quantified as `getContractValue() + pendingWithdraws + pendingInstantWithdraws` of the affected vault.

### Likelihood Explanation
Low-to-medium. It requires a specific but ordinary sequence: a lender files a withdraw request during an APR=0 configuration, and the manager subsequently configures a nonzero APR before the epoch is stopped. Managers changing APR between epochs is a routine operation; the invariant violation needs no malicious intent from any privileged role, only the attacker's well-timed `requestWithdraw` while `unscaledApr == 0`. The attacker bears only gas costs. If deployments guarantee APR is never 0 during the buffer/request window (not enforced in this contract), the surface shrinks — that guarantee is not encoded anywhere in `IdleCreditVault`.

### Recommendation
Replace the hard revert with graceful handling: when `unscaledApr != 0` but `apr0TotalPrincipal != 0`, settle the APR0 bucket at zero interest (or at the last recorded `apr0RateByEpoch`) instead of reverting — i.e., treat stale APR0 principal as principal-only and continue `stopEpoch`. Alternatively, enforce the invariant at write time: revert in `setApr`/`setAprs` when `apr0TotalPrincipal != 0` and the new `unscaledApr` is nonzero, so an impossible state can never be entered rather than panicking at epoch end.

### Proof of Concept
Foundry fork PoC sketch (against the existing `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
// test/foundry/Apr0StopEpochDoS.t.sol
function test_Apr0RequestThenAprChangeBricksStopEpoch() public {
  // 1. Pool configured with unscaledApr == 0 (APR0 mode), epoch running.
  // 2. Attacker (KYC'd lender) deposits via CDO, then calls the CDO
  //    withdraw-request path -> IdleCreditVault.requestWithdraw(amount, attacker, principal).
  //    Assert: vault.apr0TotalPrincipal() == amount.

  // 3. Honest manager configures a nonzero APR for the next epoch:
  vm.prank(manager);
  vault.setAprs(5e18, scaledApr); // unscaledApr = 5e18, below maxApr

  // 4. Warp to epoch end; borrower/manager calls IdleCDO.stopEpoch().
  //    stopEpoch -> prepareStopEpochWithApr0 reverts NotAllowed()
  //    because apr0TotalPrincipal != 0 && unscaledApr != 0.
  vm.expectRevert(NotAllowed.selector);
  cdo.stopEpoch(0);

  // 5. Permanent: repeat after any delay — still reverts; epochNumber
  //    can never increase, so _settleApr0 can never run and
  //    apr0TotalPrincipal can never be cleared. All claims are frozen.
  vm.warp(block.timestamp + 365 days);
  vm.expectRevert(NotAllowed.selector);
  cdo.stopEpoch(0);
}
```

Uncertainty to verify in a Devin session: the exact call site of `prepareStopEpochWithApr0` inside `IdleCDOEpochVariant.stopEpoch`/`stopEpochWithDuration` (whether it is invoked unconditionally or only when `unscaledApr == 0`), and whether `startEpoch`/manager tooling ever forces a nonzero `unscaledApr` while APR0 principal is pending in practice. If `stopEpoch` only calls `prepareStopEpochWithApr0` under conditions that already guarantee `unscaledApr == 0`, this finding collapses to a non-issue.