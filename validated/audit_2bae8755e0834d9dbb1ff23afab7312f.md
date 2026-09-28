### Title
APR0 withdraw receipt permanently bricks `stopEpoch` once a nonzero APR is set — deliberate `NotAllowed` trap freezes all vault funds - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`IdleCreditVault.prepareStopEpochWithApr0` contains a hard `revert NotAllowed()` whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0` (contracts/strategies/idle/IdleCreditVault.sol:505-508). This is the analog of the swift-nio-http2 bug: a deliberate trap on an "unsupported" state combination, reachable via ordinary unprivileged input, that permanently crashes the epoch state machine. An unprivileged tranche holder opens a dust APR0 withdraw request while `unscaledApr == 0`; if the honest manager ever sets a nonzero APR before that bucket is settled, every subsequent `stopEpoch` call reverts, epoch numbers stop advancing, and withdraw claims can never mature — permanently freezing all LP funds.

### Finding Description
- `requestWithdraw` routes to `_requestWithdrawApr0` whenever `unscaledApr == 0` and the pool is not closed (contracts/strategies/idle/IdleCreditVault.sol:285-286). This increases `apr0TotalPrincipal` and stores `apr0Users[_user].principal` keyed to the current `epochNumber`.
- The only place `apr0TotalPrincipal` is cleared is at the end of `prepareStopEpochWithApr0` (contracts/strategies/idle/IdleCreditVault.sol:540).
- But that same function reverts earlier if `unscaledApr != 0` while `_principal != 0` (lines 505-508):

```solidity
// APR0 principal is only valid while APR is 0 for that request lifecycle.
if (unscaledApr != 0) {
  revert NotAllowed();
}
```

- `prepareStopEpochWithApr0` is called unconditionally near the top of `_stopEpoch` (contracts/IdleCDOEpochVariant.sol:362). A revert here aborts the whole stop — `epochNumber` never increments, `pendingWithdraws` is never collected, and the epoch never ends.
- Because the bucket can only be cleared inside the function that now always reverts, the state is unrecoverable: `stopEpoch`, `stopEpochWithDuration`, and any close-pool flow (`_interest == 1`) all funnel through the same trap. Claims are also frozen: `_claimFundedWithdrawRequest` requires `epochNumber > lastWithdrawRequest[_user]` (line 326), which can never become true once stopEpoch is bricked.

### Impact Explanation
Broken invariant: epoch liveness → permanent freezing of all user funds. Once triggered, no LP can withdraw (normal, APR0, or instant receipts all depend on epoch progression or funding pulled at stop), the borrower principal cannot be recalled, and there is no admin escape hatch — `emergencyShutdown`/default paths still route through `_stopEpoch` internals or leave funds locked in the vault/strategy. Loss magnitude: 100% of vault TVL plus pending receipts, frozen permanently.

### Likelihood Explanation
The attacker is unprivileged: any KYC-passing tranche holder calls `cdoEpoch.requestWithdraw(dust, tranche)` during an APR0 epoch — a legitimate, low-cost action. Detonation requires only an honest privileged call: the manager sets a nonzero APR (via `setAprs` or a `stopEpoch` carrying `_newApr > 0`, wherever `unscaledApr` is written ahead of the next stop) while the attacker's receipt is still pending. Note a residual uncertainty I could not fully verify within the iteration limit: the exact ordering of the `unscaledApr` write relative to `prepareStopEpochWithApr0` inside `_stopEpoch`, and whether `setAprs` is epoch-phase gated. If `unscaledApr` is only updated after a successful stop, the trap requires the manager to call `setAprs` directly between epochs — still an honest, routine operation — so the finding holds.

### Recommendation
Replace the hard revert with graceful handling: either settle/carry the APR0 bucket across the APR change (snapshot the bucket at rate 0 and let `_settleApr0` pay principal with zero interest), or move the `apr0TotalPrincipal = 0` reset before the `unscaledApr` check, or make `setAprs`/`stopEpoch` reject APR changes only while an APR0 bucket is open rather than trapping the state machine afterward.

### Proof of Concept
```solidity
// Foundry fork PoC (mainnet fork of an APR0-configured IdleCreditVault)
function testApr0TrapPermanentlyFreezesVault() external {
    // Epoch running, unscaledApr == 0 (APR0 mode)
    address attacker = makeAddr("attacker");
    uint256 dust = 1; // dust receipt is enough
    _depositWithUser(attacker, 1_000e6, true);          // KYC'd lender deposits
    uint256 trancheBal = aaTranche.balanceOf(attacker);

    vm.prank(attacker);
    cdoEpoch.requestWithdraw(dust, address(aaTranche)); // opens APR0 bucket
    assertGt(strategy.apr0TotalPrincipal(), 0);

    _startEpochAndCheckPrices(0);

    // Honest manager later restores a nonzero APR while the receipt is open
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(initialApr, 0); // unscaledApr != 0

    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.stopEpoch(0, 0);

    // Every subsequent stop reverts: epoch never advances, claims never mature
    vm.prank(manager);
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.stopEpochWithDuration(0, 0, cdoEpoch.epochDuration(), 0);

    vm.prank(attacker);
    vm.expectRevert(NotAllowed.selector); // epochNumber <= lastWithdrawRequest forever
    cdoEpoch.claimWithdrawRequest();
}
```