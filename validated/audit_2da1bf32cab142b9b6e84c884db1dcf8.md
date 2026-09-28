### Title
Permanent stopEpoch deadlock via ERC4626 liquidity starvation in `ProgrammableBorrower.onStopEpoch` - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
The Besu bug class is an unguarded `waitForPeer()` that never wakes when a condition (peer capacity) is permanently starved, deadlocking the sync retry loop. The direct analog is `ProgrammableBorrower.onStopEpoch`: when the parked ERC4626 vault position economically covers the epoch-stop liability but the vault's `withdraw` reverts for lack of liquidity, the hook reverts `StopEpochVaultLiquidityUnavailable()` instead of returning `false` (default) or timing out. `IdleCDOEpochVariant._stopEpoch` lets that revert bubble, so the epoch stays running forever and no default is ever declared — the same "wait forever on a starved resource with no timeout" pattern.

### Finding Description
In `onStopEpoch` (contracts/strategies/idle/ProgrammableBorrower.sol:240-253):

```solidity
uint256 shortfall = _amountRequired - onHand;
if (shortfall > _currentVaultAssets()) return true;   // -> default path in IdleCDO
try vault.withdraw(shortfall, address(this), address(this)) { ... }
catch { revert StopEpochVaultLiquidityUnavailable(); }
```

The coverage check uses `convertToAssets`, which values shares at *accounting* value — it does not reflect withdrawable liquidity. For a MetaMorpho/ERC4626 vault, an unprivileged user can borrow (or otherwise keep occupied) all underlying market liquidity so that `vault.withdraw`/`vault.redeem` revert while `convertToAssets` still reports full coverage. Every `manager.stopEpoch()` then reverts; the retryable path in `IdleCDOEpochVariant._stopEpoch` (IdleCDOEpochVariant.sol:395-404) is only reachable when `onStopEpoch` *returns*, and there is no time-based escape: after `epochEndDate`, the epoch can be retried indefinitely but never settles and never defaults. `emergencyExitVault` (line 361) hits the same `redeem` liquidity wall and anyway needs the honest manager.

The "user of the programmable borrower's ERC4626 vault" is an explicitly allowed attacker class, and no privileged action is required: the attacker only needs to keep the Morpho market liquidity saturated through the stop window each time it is retried (renewable by keeping a borrow position open).

### Impact Explanation
- Pending withdraw requests (`requestWithdraw` receipts) for the epoch can never be funded: `stopEpoch` is the only path that calls `collectWithdrawFunds` and clears `pendingWithdraws`, so receipt holders' underlying is frozen indefinitely while the attacker maintains the squeeze.
- No default can be declared on this path (`defaulted()` stays false), so `DefaultDistributor` recovery never starts — worse than an insolvency, which would at least distribute the vault-backed value.
- Tranche holders cannot redeem through the epoch flow; borrower interest keeps accruing against the facility, degrading eventual recovery.

This is temporary-to-permanent freezing of the epoch's withdrawable funds (bounded by the attacker's borrow cost; a squeeze maintained long enough is effectively permanent since the code has no deadline or forced-default fallback).

### Likelihood Explanation
Requires a programmable-borrower vault whose ERC4626 (MetaMorpho) liquidity can be cornered by an unprivileged borrower, and a `stopEpoch` call during the squeeze. Both conditions are economically feasible: the attacker finances a borrow on Morpho whose cost is far below the value of the frozen LP claims (griefing/extortion scenario). Existing tests (`testProgrammableBorrowerStopEpochRevertsWhenVaultLiquidityUnavailable`) confirm the revert is intended and retryable, but nothing bounds the retry window or escalates to default — identical in spirit to the Besu `waitForPeer()` with no timeout.

### Recommendation
Add a liveness fallback analogous to Besu's `.orTimeout(5s)`: e.g., record `stopEpochRetrySince`/consecutive `StopEpochVaultLiquidityUnavailable` failures and, past a grace deadline, treat the vault recall failure as a borrower default (`_handleBorrowerDefault`) or allow a manager-invoked forced default so pending receipts enter `DefaultDistributor` instead of freezing forever.

### Proof of Concept
Foundry fork (MetaMorpho Gauntlet USDC Prime, mirroring `test/foundry/ProgrammableBorrowerCreditVault.t.sol`):

```solidity
function testStopEpochDeadlockWhenVaultLiquidityStarved() external {
    _setUpProgrammableBorrowerCreditVault(GAUNTLET_FORK_BLOCK, GAUNTLET_USDC_PRIME);
    uint256 amount = 10_000 * oneScale;
    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);
    idleCDO.depositAA(amount);
    cdoEpoch.requestWithdraw(aaTranche.balanceOf(address(this)) / 2, address(aaTranche));
    _startEpochAndCheckPrices(0);

    // Attacker (unprivileged Morpho borrower) borrows all market liquidity so
    // gauntletVault.withdraw() reverts while convertToAssets still covers the shortfall.
    address attacker = makeAddr("attacker");
    _borrowAllMorphoLiquidity(attacker); // e.g. supply collateral + borrow supply assets

    vm.warp(cdoEpoch.epochEndDate() + 1);
    for (uint i; i < 5; ++i) {
        vm.warp(block.timestamp + 30 days); // any later retry also fails
        vm.prank(manager);
        vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
        cdoEpoch.stopEpoch(0, 0);
    }
    assertTrue(cdoEpoch.isEpochRunning());   // epoch never settles
    assertFalse(cdoEpoch.defaulted());       // no default, no recovery path
    assertGt(strategy.pendingWithdraws(), 0); // receipts permanently unfunded
}
```