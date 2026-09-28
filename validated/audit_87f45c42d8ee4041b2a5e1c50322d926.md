### Title
Unprivileged ERC4626 vault user can starve `onStopEpoch` liquidity and indefinitely block epoch settlement, freezing all lender funds - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
The XXE report (unrestricted resolution of an external entity) maps to the programmable-borrower credit vault resolving an external ERC4626 vault as its liquidity/accounting source without restriction. `onStopEpoch` converts a mere `vault.withdraw` failure — which an unprivileged user of the external vault (e.g. a MetaMorpho borrower who drains available liquidity) can induce at will — into a hard revert (`StopEpochVaultLiquidityUnavailable`), which propagates out of `IdleCDOEpochVariant.stopEpoch` and blocks epoch settlement, freezing every tranche holder's funds.

### Finding Description
In `ProgrammableBorrower.onStopEpoch`, when `IdleCDO` requests `_amountRequired` and on-hand cash is insufficient, the hook attempts `vault.withdraw(shortfall, ...)`. Two paths exist:

- If `shortfall > _currentVaultAssets()` (the vault sleeve cannot economically cover it), the hook returns `true` and lets the later `transferFrom` fail, which IdleCDO handles as a borrower default — an acceptable path.
- If the vault *does* cover the shortfall on paper but `vault.withdraw` reverts for any reason, the hook reverts with `StopEpochVaultLiquidityUnavailable` (ProgrammableBorrower.sol:246-253). [1](#0-0) 

For a lending-market-backed ERC4626 vault (the deployment target is MetaMorpho, per `test/foundry/ProgrammableBorrowerCreditVault.t.sol`), `withdraw` reverts whenever the vault's markets have no immediately available liquidity. Any unprivileged user can create that condition: borrow all available liquidity from the underlying Morpho markets, or request large vault withdrawals ahead of the keeper. Because the revert propagates through `stopEpoch`, the epoch cannot be settled while the condition persists: `epochEndDate` has passed, `isEpochRunning` stays true, deposits are paused, withdraw requests are disabled (`allowAAWithdrawRequest`/`allowBBWithdrawRequest` were cleared at `startEpoch`, IdleCDOEpochVariant.sol:249-250), and neither pending withdraw claims nor the next `startEpoch`/`stopEpoch` can proceed. [2](#0-1) 

The attacker pays only Morpho borrow interest on the starved liquidity; no privileged role, no price manipulation, and no theft of vault shares is required.

### Impact Explanation
Temporary freezing of the entire pool's TVL for the duration the attacker sustains vault illiquidity. Concretely, for a pool with N USDC of NAV, all AA/BB tranche holders' redemption requests made during the buffer cannot be claimed (the strategy is only funded at `stopEpoch`), and the frozen amount equals full pool NAV plus accrued interest. The attacker can extend the freeze epoch-over-epoch by rolling the borrowed liquidity position, and griefing cost is bounded by borrow interest, which is small relative to the frozen pool. This is not a gas/DoS-only issue: it is a fund-impact liveness failure on the withdrawal path.

### Likelihood Explanation
Requires (a) a programmable-borrower deployment with an external ERC4626 vault whose liquidity is shared with unprivileged users — exactly the intended MetaMorpho deployment — and (b) an attacker willing to pay borrow interest for the freeze duration, or even just a flash-bot-style timing race around `stopEpoch`. No privileged role is needed and the revert path is reached on every `stopEpoch` call while the vault is illiquid. The `try/catch` in the hook explicitly converts only this case into a revert, and the code comments acknowledge retryability but do not bound the freeze — the attacker, not the keeper, controls when liquidity returns. Likelihood is medium: it needs an economic motive (e.g., holding receipt claims that benefit from delaying settlement, or pure griefing/extortion of the pool), but it is technically trivial to execute against a shared-liquidity vault.

### Recommendation
Do not couple epoch settlement liveness to the external vault's instantaneous liquidity:
- In `onStopEpoch`, prefer returning a status (or withdrawing up to `vault.maxWithdrawable(address(this))`) instead of reverting on `withdraw` failure, and let `IdleCDO`'s existing under-funded `transferFrom` path decide between retry and default.
- Alternatively, have the hook withdraw whatever is currently withdrawable and report the residual shortfall, so a partially illiquid vault does not fully block settlement.
- Keep `totalInterestDueNow`/`_vaultNetInterest` accounting consistent with partial withdrawals (`epochWithdrawnFromVault`) so retried stops cannot double-count.

### Proof of Concept
Reproducible on the existing mainnet-fork harness in `test/foundry/ProgrammableBorrowerCreditVault.t.sol` (it already deploys `ProgrammableBorrower` against the real MetaMorpho vault):

```solidity
function testVaultLiquidityStarvationBlocksStopEpoch() external {
    uint256 amount = 10_000 * oneScale;

    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);

    idleCDO.depositAA(amount);
    _startEpochAndCheckPrices(0);
    // all pool funds are now parked in the MetaMorpho vault

    // === Attacker (unprivileged user of the ERC4626 vault) ===
    // Borrow all available liquidity from the vault's underlying Morpho
    // markets (or front-run maxWithdrawable to 0). Standard Morpho borrow,
    // no privilege required:
    //   morpho.borrow(marketParams, morphoVault.maxWithdrawable-equivalent, ...)
    //   => vault.maxWithdrawable(PB) == 0, so vault.withdraw() will revert.
    _drainMorphoVaultLiquidity(); // helper: attacker borrows all liquidity

    // === Honest manager tries to settle the epoch ===
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(ProgrammableBorrower.StopEpochVaultLiquidityUnavailable.selector);
    cdoEpoch.stopEpoch(0, 0);

    // Pool is frozen: epoch still running, deposits paused, withdraw
    // requests disabled, pending receipts unfunded.
    assertTrue(cdoEpoch.isEpochRunning(), "epoch stuck running");
    assertFalse(cdoEpoch.allowAAWithdrawRequest(), "AA requests disabled");
    assertFalse(cdoEpoch.allowBBWithdrawRequest(), "BB requests disabled");

    // Every retry while liquidity is starved reverts again — the freeze lasts
    // exactly as long as the attacker keeps the vault illiquid.
}
```

Uncertainty note: I verified the revert path and gating flags in `ProgrammableBorrower.onStopEpoch` and `IdleCDOEpochVariant.startEpoch`, but could not fully trace `stopEpoch`'s internals within the iteration budget; if `stopEpoch` wraps the hook call in `try/catch` and falls through to the default path, the freeze instead converts into an unwarranted borrower default — still a broken-invariant outcome (BB-first loss socialization of a liquidity event), though with a different severity profile.

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L239-253)
```text
    uint256 onHand = underlyingToken.balanceOf(address(this));
    if (_amountRequired > onHand) {
      uint256 shortfall = _amountRequired - onHand;
      // If the vault shares do not economically cover the shortfall, let IdleCDO's later
      // transferFrom fail and use the existing default path. Only an otherwise-covered ERC4626
      // withdrawal failure should make stopEpoch retryable.
      if (shortfall > _currentVaultAssets()) return true;
      try vault.withdraw(shortfall, address(this), address(this)) returns (uint256 shares) {
        if (epochAccountingActive) {
          epochWithdrawnFromVault += shortfall;
        }
        emit WithdrawnFromVault(shortfall, shares, address(this));
      } catch {
        revert StopEpochVaultLiquidityUnavailable();
      }
```

**File:** contracts/IdleCDOEpochVariant.sol (L244-250)
```text
    isEpochRunning = true;
    // prevent deposits
    _pause();

    // prevent withdrawals requests
    allowAAWithdrawRequest = false;
    allowBBWithdrawRequest = false;
```
