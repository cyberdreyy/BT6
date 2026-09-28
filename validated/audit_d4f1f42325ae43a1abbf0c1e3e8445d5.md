### Title
External ERC4626 liquidity drain causes indefinite `stopEpoch` revert, freezing all LP withdrawals - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
The aim advisory's bug class — a synchronous access to a remote resource with no timeout/fallback that hangs a single-threaded server — maps onto `IdleCDOEpochVariant._stopEpoch`'s synchronous call into `ProgrammableBorrower.onStopEpoch`, which itself performs a synchronous `vault.withdraw` against an external ERC4626 vault. Any unprivileged user of that shared external vault (e.g. a borrower in the underlying Morpho market) can drain its liquid assets so `withdraw` reverts. The revert bubbles as `StopEpochVaultLiquidityUnavailable` and makes `stopEpoch` permanently uncallable for as long as liquidity stays drained, freezing the epoch and every withdrawal claim.

### Finding Description
In `IdleCDOEpochVariant._stopEpoch`, when `isProgrammableBorrower` is true the CDO calls `IProgrammableBorrower(_borrower()).onStopEpoch(amount + pendingWithdraws, ...)` and lets its reverts bubble up (`IdleCDOEpochVariant.sol:395-404`, comment: "Hook reverts bubble so transient ERC4626 liquidity failures can be retried").

In `ProgrammableBorrower.onStopEpoch` (`ProgrammableBorrower.sol:239-253`):
- If the shortfall is covered by vault shares (`shortfall <= _currentVaultAssets()`), it executes `vault.withdraw(shortfall, ...)`.
- On `catch` it reverts `StopEpochVaultLiquidityUnavailable()` — propagating out of `stopEpoch`.

An attacker who is an ordinary user of the external ERC4626 vault (explicitly in scope: "a user of the programmable borrower's ERC4626 vault") borrows the entire free liquidity of the vault's underlying markets. The vault's shares still value correctly (`convertToAssets` is unaffected — only liquidity is gone), so the `shortfall > _currentVaultAssets()` early-return path is not taken; `vault.withdraw` reverts on insufficient liquidity and `stopEpoch` reverts every time it is called.

Because the epoch cannot stop:
- `isEpochRunning` stays true; deposits stay paused (`_pause()` in `startEpoch`) and `allowAAWithdrawRequest`/`allowBBWithdrawRequest` remain false.
- `pendingWithdraws` are never funded via `collectWithdrawFunds`, so `IdleCreditVault.claimWithdrawRequest` has nothing to pay.
- The borrower cannot repay interest through the normal settle path either, extending the freeze.

The attacker only needs to hold the borrow open; each manager retry of `stopEpoch` reverts identically, exactly mirroring the advisory's "request the server to connect to an unresponsive resource → all subsequent requests hang" pattern.

### Impact Explanation
Temporary freezing of all funds in the vault for the duration of the liquidity drain. The quantified at-risk amount is `strategy.pendingWithdraws()` (unclaimable receipts) plus the entire live NAV (`getContractValue()`) that cannot enter a buffer period for new requests. No loss-of-funds, but LP capital is locked as long as the attacker maintains the borrow position in the shared external vault — a real economic attack gated only by borrow interest cost on the drained amount, which on a thin-liquidity Morpho market can be far smaller than the frozen vault NAV.

### Likelihood Explanation
- The attacker needs no role: supplying/borrowing in the shared ERC4626 vault's markets is permissionless.
- The attack requires the programmable-borrower deployment mode and enough capital (or a leveraged loop) to drain the external vault's free liquidity; on markets where the facility is a large fraction of vault deposits, the marginal drain amount is small.
- One mitigating factor: the code deliberately treats this revert as retryable and the manager can eventually call `stopEpoch` once liquidity returns, and `emergencyExitVault` faces the same revert. The `shortfall > _currentVaultAssets()` check prevents a fake-liquidity variant, so only genuine liquidity exhaustion works — which is precisely what an attacker borrowing in the same market produces. The behavior is partially acknowledged by the "retryable" comment, which weakens severity but does not address a *sustained* drain, since there is no timeout, no forced-default path, and no alternative unwind while shares are economically backed but illiquid.

### Recommendation
- Add a bounded-failure policy: after `N` consecutive `StopEpochVaultLiquidityUnavailable` failures or a time-based deadline past `epochEndDate`, allow `stopEpoch` to fall through to the default path (`_handleBorrowerDefault`) or to a "stop without recall" mode that settles withdraws from on-hand cash only.
- Alternatively, let `onStopEpoch` return `false` (the existing default signal) instead of reverting when `vault.maxWithdraw(address(this))` is 0 for longer than a configurable grace period.
- Track a `firstFailedStopTimestamp` so transient hiccups remain retryable while sustained drains trigger escalation.

### Proof of Concept
Foundry fork PoC sketch (modeled on `test/foundry/ProgrammableBorrowerCreditVault.t.sol:372-399`, which already demonstrates the revert path with `MockStopEpochLiquidityVault` — replace the mock limit with a real liquidity drain):

```solidity
// Fork mainnet at a block where ProgrammableBorrower's vault (e.g. GAUNTLET_USDC_PRIME)
// has low free liquidity relative to its markets' borrowable depth.
function testStopEpochFrozenByExternalLiquidityDrain() external {
    _setUpProgrammableBorrowerCreditVault(FORK_BLOCK, VAULT);
    uint256 amount = 10_000 * oneScale;

    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);
    idleCDO.depositAA(amount);
    cdoEpoch.requestWithdraw(aaTranche.balanceOf(address(this)) / 2, address(aaTranche));
    _startEpochAndCheckPrices(0);

    // Attacker: any EOA. Supply collateral and borrow ALL free liquidity from the
    // Morpho markets backing `vault`, so vault.maxWithdraw(programmableBorrower) == 0
    // while convertToAssets(balanceOf) still covers the shortfall.
    address attacker = makeAddr("attacker");
    _drainMorphoMarketLiquidity(attacker); // permissionless borrow

    // Epoch ends; manager tries to stop. Every call reverts — epoch cannot close.
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    // Retry N times while attacker keeps the borrow open — identical revert.
    for (uint i; i < 5; ++i) {
        vm.warp(block.timestamp + 1 days);
        vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
        vm.prank(manager);
        cdoEpoch.stopEpoch(0, 0);
    }

    assertTrue(cdoEpoch.isEpochRunning());
    assertFalse(cdoEpoch.defaulted());                  // no default escape either
    assertGt(strategy.pendingWithdraws(), 0);           // receipts unfunded
    vm.expectRevert();                                  // claims unavailable
    strategy.claimWithdrawRequest(attacker /* any holder */, address(aaTranche));
}
```

Key assertions: `stopEpoch` reverts on every attempt while external liquidity is drained, `defaulted` never triggers (the `return true` arm is skipped because shares cover the shortfall), and pending withdraw receipts remain unclaimable for the attack duration — a temporary freeze of all vault NAV, sustained at the cost of borrow interest only.