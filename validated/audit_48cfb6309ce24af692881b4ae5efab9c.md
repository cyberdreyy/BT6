### Title
APR0 withdraw request permanently bricks `stopEpoch` if APR is later raised above zero — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.prepareStopEpochWithApr0` reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0`. An unprivileged lender can create the `apr0TotalPrincipal` bucket with a normal `requestWithdraw` while the pool is configured at APR 0. A routine, honest `setAprs`/`setAprsWithBuffer` call by the manager (or the CDO itself) that raises the APR then makes every subsequent `stopEpoch` revert. Because `epochNumber` can never advance, no code path can ever decrement `apr0TotalPrincipal`, so the epoch machine is stuck forever and all vault funds are permanently frozen.

### Finding Description
In `requestWithdraw`, when `unscaledApr == 0`, the request is routed to `_requestWithdrawApr0`, which increments `apr0TotalPrincipal` (`IdleCreditVault.sol:285-286`, `:567-577`). `prepareStopEpochWithApr0` — called by the CDO during every `stopEpoch` — enforces `if (unscaledApr != 0) revert NotAllowed()` before it ever reaches the `apr0TotalPrincipal = 0` reset at line 540 (`IdleCreditVault.sol:490-541`).

Once the revert condition holds, there is no escape:

- `apr0TotalPrincipal` is only decremented in `prepareStopEpochWithApr0` (line 540) and `_clearWithdrawClaimForEpoch` (line 827, default claims only — unreachable because defaulting requires a `stopEpoch(0,0)` call that itself hits the same revert).
- The user cannot unwind it: `_claimFundedWithdrawRequest` reverts while `epochNumber <= lastWithdrawRequest[_user]` (line 326) since `epochNumber` only increases inside `deposit` during `stopEpoch` (line 610), and `_settleApr0` early-returns for `reqEpoch >= epochNumber` (line 553) without touching `apr0TotalPrincipal`.
- `setApr`/`setAprs` have no epoch-phase gating (lines 206-235); raising APR mid-lifecycle is a normal manager operation.

Broken invariant: epoch progression / liveness — the vault's core state transition can be permanently DoS'd by the composition of an unprivileged deposit-withdraw request and an honest APR reconfiguration, analogous to CVE-2021-2030's "easily exploitable" crash → complete denial of service of the component.

### Impact Explanation
All LP funds in the vault are permanently frozen: `stopEpoch` can never succeed, so the borrower never repays, withdraw requests never become claimable, new deposits/`startEpoch` never settle, and even the borrower-default path (`stopEpoch(0,0)`) reverts, so `finalizeDefault`/`finalizeDefaultRecovery` can never run. Loss equals the full vault TVL plus any accrued interest — total, permanent freezing, not merely gas/DoS without fund impact.

### Likelihood Explanation
Requires a pool configured with `unscaledApr == 0` (a supported mode with dedicated accounting throughout the file) plus a later APR raise — both plausible operational events. The attacker needs only to be a KYC'd lender making an ordinary withdraw request; no privileged role, no timing beyond requesting before the APR change. The freeze is then unconditional and irreversible.

### Recommendation
Instead of reverting when `unscaledApr != 0` with a pending APR0 bucket, settle the outstanding APR0 principal at zero interest (or at the new epoch's realized rate) and clear `apr0TotalPrincipal`, so the epoch can always close. Alternatively, gate `setApr`/`setAprs` to reject APR changes away from 0 while `apr0TotalPrincipal != 0` — though this still lets a single dust APR0 request block APR changes, so settling is preferable.

### Proof of Concept
Foundry fork test sketch (extend `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testApr0RequestThenAprRaiseBricksStopEpoch() external {
    // 1. Configure pool at APR 0
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);

    idleCDO.depositAA(10_000 * ONE_SCALE);
    address attacker = makeAddr('apr0-attacker');
    _depositWithUser(attacker, 1 * ONE_SCALE, true);

    _startEpochAndCheckPrices(0);

    // 2. Unprivileged lender opens an APR0 withdraw request
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(AAtranche.balanceOf(attacker), address(AAtranche));
    assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

    // 3. Honest manager raises APR (routine, ungated call)
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(10e18, 10e18);

    // 4. stopEpoch permanently reverts — even default-stop cannot proceed
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 0);

    // apr0TotalPrincipal can never be cleared: claim is epoch-gated and
    // epochNumber can no longer advance; all vault TVL is frozen.
}
```