### Title
Raising `unscaledApr` while APR0 withdraw requests are pending leaves a stale `apr0TotalPrincipal` bucket that permanently reverts `stopEpoch`, freezing all vault funds - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault.requestWithdraw` registers a per-user link `apr0Users[user].principal` plus the aggregate `apr0TotalPrincipal` whenever `unscaledApr == 0` at request time. `setAprs`/`setAprsWithBuffer`/`setApr` can later move the vault out of APR0 mode, but — exactly like `OlympusTreasury.removeCategoryGroup` deleting the group without clearing `categorization[asset][group]` — nothing cleans the stale APR0 links. `prepareStopEpochWithApr0` then unconditionally reverts on the stale bucket (`unscaledApr != 0 && apr0TotalPrincipal != 0`), so `stopEpoch` can never succeed while the stale link exists.

### Finding Description
In `requestWithdraw`, a withdraw requested while `unscaledApr == 0` is routed to `_requestWithdrawApr0`, which sets `apr0Users[_user].principalEpoch = epochNumber` and accumulates `apr0TotalPrincipal` (lines 285-286, 567-577). These entries are only cleared by:

- `prepareStopEpochWithApr0` setting `apr0TotalPrincipal = 0` (line 540) — but only reachable when `unscaledApr == 0`, because lines 505-508 revert otherwise while the bucket is non-zero;
- `_settleApr0` during a funded claim — which zeroes the *user* principal but never decrements `apr0TotalPrincipal`;
- `_clearWithdrawClaimForEpoch` — only on default-finalization claims.

So once the manager (or the CDO at `startEpoch`/`stopEpoch`) raises the APR via `setAprs`/`setAprsWithBuffer` while any APR0 withdraw is pending, `apr0TotalPrincipal` becomes orphaned state. Every subsequent `stopEpoch` call reaches `prepareStopEpochWithApr0`, which hits:

```solidity
uint256 _principal = apr0TotalPrincipal;
if (_principal == 0) return ...;
if (unscaledApr != 0) revert NotAllowed();   // lines 502-508
```

and reverts. The only exits are (a) lowering the APR back to 0, which defeats the honest manager's repricing intent, or (b) a borrower default path, which socializes a loss that should never have occurred. The "removed group" (APR0 mode) still links users into accounting long after it should have been severed — the same stale-mapping invariant violation as the Olympus report, expressed on the credit-vault APR0 surface.

### Impact Explanation
An unprivileged tranche holder can permanently wedge an APR0-mode vault: the attacker's `requestWithdraw` creates the stale bucket, and any subsequent honest APR increase makes `stopEpoch` uncallable. All active LP principal and pending receipts are frozen for as long as the stale link persists — temporary freezing of the entire vault TVL at minimum, and permanent freezing if the vault cannot economically return to `unscaledApr == 0` (the borrower was repriced precisely because the 0-rate epoch was ending). Default finalization as the only other cleanup path converts the freeze into a forced, unnecessary loss waterfall.

### Likelihood Explanation
Requires the vault to run with `unscaledApr == 0` (a supported mode, with dedicated settlement logic), an attacker with a tranche position able to call `requestWithdraw` during the epoch, and an honest manager/CDO APR update before the bucket is settled. All attacker actions are unprivileged; the privileged calls used are honest sequencing, which the threat model explicitly allows. No existing guard (`_onlyIdleCDO`, `NotAllowed` checks, epoch gating) prevents creating the bucket or blocks the later APR change — `setApr` only enforces `maxApr`.

### Recommendation
Mirror the report's fix: when the APR is raised away from 0, drain the stale APR0 bucket instead of leaving it linked. Either (a) revert in `setApr`/`setAprs` when `apr0TotalPrincipal != 0` so mode changes cannot orphan the state, or (b) settle the bucket eagerly on the APR transition — iterate/settle pending APR0 principals into `withdrawsRequestsByEpoch`/`withdrawsRequests` (or record a zero `apr0RateByEpoch` and clear `apr0TotalPrincipal`) before `unscaledApr` is updated, so `prepareStopEpochWithApr0` always sees a consistent state.

### Proof of Concept
```solidity
// Foundry fork PoC (against test/foundry/TestIdleCDOBase.sol harness)
function testStaleApr0BucketFreezesStopEpoch() external {
    // Setup: vault running with unscaledApr == 0 (APR0 mode)
    vm.prank(manager);
    strategy.setAprs(0, 0); // scaled + unscaled APR = 0

    _depositWithUser(attacker, 10_000 * ONE_SCALE, true); // attacker is a KYC'd lender
    _startEpochAndCheckPrices(0);

    // Attacker opens an APR0 withdraw request -> seeds apr0TotalPrincipal
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(AAtranche));
    assertGt(strategy.apr0TotalPrincipal(), 0, 'APR0 bucket not seeded');

    // Honest manager reprices the vault mid-epoch (APR group "removed")
    vm.prank(manager);
    strategy.setAprs(5e17, 5e17); // unscaledApr != 0 now

    // Borrower funds the epoch end; stopEpoch now always reverts on the stale link
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(initialProvidedApr, 0);

    // Funds are frozen: every stopEpoch reverts while apr0TotalPrincipal != 0 && unscaledApr != 0
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 0);
}
```

Note: I could not verify `stopEpoch`'s exact revert path through `IdleCDOEpochVariant` within the available context (the call chain `stopEpoch -> prepareStopEpochWithApr0` should be confirmed at the cite around `contracts/strategies/idle/IdleCreditVault.sol:490-508`); if `stopEpoch` invokes it only conditionally, the PoC should be adjusted to trigger it via the interest-bearing path.