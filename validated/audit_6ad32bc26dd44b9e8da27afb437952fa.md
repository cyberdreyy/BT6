### Title
APR0 withdraw requests permanently brick `stopEpoch` once the APR is honestly raised above zero — ([File: contracts/strategies/idle/IdleCreditVault.sol:490-541])

### Summary
`IdleCreditVault.prepareStopEpochWithApr0` reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0`. An unprivileged KYC-passing lender can open an APR0 withdraw request while the vault APR is legitimately `0`; if the manager/CDO later sets a non-zero APR (a normal repricing through `setAprs`/`setAprsWithBuffer`, lines 206-220), every subsequent `stopEpoch` reverts. The only code that clears `apr0TotalPrincipal` (line 540) sits after the revert, so the bucket can never drain while APR is non-zero. Like CVE-2021-29478 — where a user-visible config change (`set-max-intset-entries`) invalidates state built under the old setting — the APR change corrupts accounting built under the old APR, and the corruption is triggered by an unprivileged deposit/request, not by the privileged caller.

### Finding Description
In `IdleCreditVault.requestWithdraw` (lines 243-295), a request is routed into the APR0 bucket whenever `unscaledApr == 0`:

```
if (unscaledApr == 0 && !isClosed) {
  _requestWithdrawApr0(_amount, _user);
}
```

`_requestWithdrawApr0` (lines 567-577) increments the global `apr0TotalPrincipal`. At `stopEpoch`, the CDO calls `prepareStopEpochWithApr0` (lines 490-541), which contains:

```
uint256 _principal = apr0TotalPrincipal;
if (_principal == 0) {
  return (_expInterest, _adjPendingWithdrawFees);
}
if (unscaledApr != 0) {
  revert NotAllowed();
}
```

The invariant `apr0TotalPrincipal == 0 || unscaledApr == 0` is required for `stopEpoch` to succeed, but nothing enforces it. `setAprs`/`setAprsWithBuffer` (lines 206-220) update `unscaledApr` unconditionally, with no check on `apr0TotalPrincipal`. The reset `apr0TotalPrincipal = 0` (line 540) is unreachable while the revert fires, so the freeze persists until the manager restores `unscaledApr = 0` — i.e., the vault is forced to keep a zero APR for the remainder of the epoch.

Attacker sequence (buffer/running phase, APR0 mode):
1. Vault operates an epoch with `unscaledApr == 0` (a supported configuration — the entire apr0 flow exists for it).
2. Attacker (whitelisted lender) calls `IdleCDOEpochVariant.requestWithdraw` → `IdleCreditVault.requestWithdraw` → `_requestWithdrawApr0`. Now `apr0TotalPrincipal > 0`, `apr0Users[attacker].principalEpoch = epochNumber`.
3. Manager or CDO honestly reprices: `setAprs(5e18, scaledApr)` or `setAprsWithBuffer(...)` sets `unscaledApr > 0`. Repricing mid-epoch is a supported owner/manager action.
4. `stopEpoch` is invoked → `prepareStopEpochWithApr0` reverts `NotAllowed` → epoch cannot close → `epochEndDate` never resets → all deposits, withdraw claims, and borrower repayments freeze.

Existing guards do not stop it: `_onlyIdleCDO`/`isWalletAllowed` gates don't apply to the attacker step, which is a legitimate whitelisted request; there is no check coupling `unscaledApr` changes to `apr0TotalPrincipal`.

### Impact Explanation
Temporary freezing of all vault funds: every `stopEpoch` reverts until the manager sets the APR back to 0 and successfully stops the epoch (which settles the APR0 bucket at a zero rate). During the freeze, queued withdraw receipts (`withdrawsRequests`, `instantWithdrawsRequests`), pending borrower repayment, and new deposits are all blocked. Additionally, the forced continuation at `unscaledApr == 0` means active lenders earn no yield for the extended epoch — a quantifiable loss of `expectedEpochInterest` for the extended duration. Cost to the attacker is one tranche deposit plus a withdraw request.

### Likelihood Explanation
Medium. Requires (a) an epoch configured at `unscaledApr == 0` — explicitly supported via the apr0 code path, (b) a single whitelisted withdraw request, and (c) an honest APR raise before that epoch's `stopEpoch`. The attacker controls (b) and can time it; (a) and (c) depend on operations but APR repricing mid-epoch is a designed manager function (`setApr`, `setAprs`, `setAprsWithBuffer` exist precisely for that). Note a caveat: if deployments never run zero-APR epochs, the trigger window is absent; the bug is latent in that case.

### Recommendation
Either disallow APR changes while `apr0TotalPrincipal != 0` inside `setApr`/`setAprs`/`setAprsWithBuffer` (revert `NotAllowed`), or make `prepareStopEpochWithApr0` settle the open APR0 bucket at the stored request-epoch APR (rate 0) and clear it rather than reverting — e.g., move `apr0TotalPrincipal = 0` before the `unscaledApr != 0` check and simply skip interest accrual for the stale bucket.

### Proof of Concept
Foundry fork sketch (mainnet USDC, existing `TestIdleCDOBase`-style harness in `test/foundry/`):

```solidity
function testApr0FreezesStopEpoch() public {
    // idleCDO + IdleCreditVault deployed, epoch running with unscaledApr == 0
    // (owner calls vault.setAprs(0, 0) or starts epoch at 0 APR)

    // 1. whitelisted attacker deposits AA and requests withdraw during the 0-APR epoch
    vm.startPrank(attacker); // attacker passed isWalletAllowed
    idleCDO.depositAA(1_000e6);
    idleCDO.requestWithdraw(0, idleCDO.AATranche()); // routes to _requestWithdrawApr0
    vm.stopPrank();
    assertGt(vault.apr0TotalPrincipal(), 0);

    // 2. honest manager reprices APR > 0 mid-epoch
    vm.prank(manager);
    vault.setAprs(5e18, scaledApr); // unscaledApr now 5e18

    // 3. stopEpoch reverts forever while apr0TotalPrincipal != 0
    vm.warp(vaultEpochEndDate + 1);
    vm.prank(borrower);
    token.approve(address(idleCDO), type(uint256).max);
    vm.expectRevert(NotAllowed.selector);
    idleCDO.stopEpoch();

    // vault is frozen until manager restores unscaledApr == 0
    vm.prank(manager);
    vault.setAprs(0, 0);
    idleCDO.stopEpoch(); // succeeds, APR0 claimants settled at rate 0
}
```

Key lines to assert: the revert at `IdleCreditVault.sol:506-508` and the unreachable reset at `IdleCreditVault.sol:540`.