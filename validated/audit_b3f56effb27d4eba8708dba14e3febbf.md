### Title
External ERC4626 liquidity squeeze makes `stopEpoch` uncallable and freezes all lender funds - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
CVE-2023-31847 is a "client connects to an attacker-influenced external endpoint and is harmed through it" bug class. The on-chain analog in idle-tranches is `ProgrammableBorrower`, which parks all undeployed pool capital in an external ERC4626 vault that the attacker can also use. `IdleCDOEpochVariant._stopEpoch` calls `onStopEpoch`, which unconditionally attempts `vault.withdraw(shortfall)` whenever on-hand cash does not cover the recall, and reverts `StopEpochVaultLiquidityUnavailable` on failure [1](#0-0) . Because the revert propagates through `stopEpoch` [2](#0-1) , an unprivileged user of that same vault can keep the vault's redeemable liquidity exhausted so `stopEpoch` can never succeed, indefinitely freezing the whole credit pool.

### Finding Description
In the programmable/revolving mode deployed by `deployRevolvingCreditVault` (`isInterestMinted = true`, instant withdraw and deposit-during-epoch disabled) [3](#0-2) , essentially all NAV is inside the shared external vault after `onStartEpoch` deposits the full idle balance [4](#0-3) . When the honest manager calls `stopEpoch`, `_amountRequired = pendingWithdraws` (interest is minted, so at minimum the withdraw-request reserve must be pulled back in cash) [5](#0-4) . `onStopEpoch` only skips the withdrawal when `shortfall > _currentVaultAssets()` — i.e., a real shortfall in share value — but routes a merely *illiquid* vault into `vault.withdraw`, which reverts and bubbles up [6](#0-5) . An ordinary depositor/borrower of that external ERC4626 (an explicitly in-scope unprivileged actor) can withdraw or borrow the vault's available liquidity — atomically or persistently — so that every `stopEpoch` attempt reverts before any accounting runs. The epoch then never ends: `isEpochRunning` stays true, deposits stay paused, withdraw requests can't be claimed, and no default is declared because `getFundsFromBorrower` is never reached.

### Impact Explanation
Temporary freezing of 100% of pool NAV (all lender principal plus pending withdraw reserves) for as long as the attacker keeps the external vault illiquid — renewable each block at only the cost of vault-side borrow interest or repeated withdrawals, with no IdleCDO-side escape other than waiting. Pending withdraw requests maturing at epoch end are unclaimable during the freeze.

### Likelihood Explanation
Requires only that the configured ERC4626 (e.g., a MetaMorpho-style pooled vault) has open liquidity the attacker can consume — a normal, permissionless interaction with a third-party vault the attacker is explicitly allowed to use. No privileged role, no insider, no oracle manipulation: the exhaustion is real liquidity removal. Cost scales with vault depth, and shared-liquidity vaults routinely run near-full utilization, making the shortfall cheap to induce at epoch boundaries whose timing is publicly known (`epochEndDate`).

### Recommendation
Cap the liquidity demanded per `stopEpoch` or let `onStopEpoch` return a retryable "partial liquidity" result that still settles accounting with available cash instead of reverting; alternatively, unwind the vault position proactively via `emergencyExitVault` before `stopEpoch` and treat `StopEpochVaultLiquidityUnavailable` as recoverable only after a bounded number of retries, after which the default path should engage rather than an indefinite freeze.

### Proof of Concept
Foundry fork PoC (mainnet, real MetaMorpho vault as in `test/foundry/ProgrammableBorrowerCreditVault.t.sol`):

```solidity
function testVaultLiquiditySqueezeFreezesStopEpoch() external {
  // setup identical to testProgrammableBorrowerStopEpochAutoRealizesInterestWithRealVault
  uint256 amount = 10_000 * oneScale;
  vm.prank(owner); cdoEpoch.setIsInterestMinted(true);
  idleCDO.depositAA(amount);
  // a lender queues a withdraw request during buffer so pendingWithdraws > 0
  _startEpochAndCheckPrices(0);           // all `amount` now sits in morphoVault

  vm.warp(cdoEpoch.epochEndDate() + 1);

  // Attacker: an ordinary user of the external vault drains its available liquidity
  // (withdraw own shares / borrow max on underlying markets) so that
  // morphoVault.maxWithdraw(ProgrammableBorrower) < shortfall, leaving shares valuable but illiquid.
  uint256 liquid = IERC20(USDC).balanceOf(address(morphoVault)); // or market-level available liquidity
  vm.prank(attacker);
  morphoVault.withdraw(liquid, attacker, attacker);            // or borrow on every enabled market

  // Honest manager can never stop the epoch while vault is illiquid
  vm.prank(manager);
  vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
  cdoEpoch.stopEpoch(0, 0);

  // repeat: attacker re-squeezes whenever liquidity returns => epoch never ends,
  // deposits stay paused and pending withdraw requests stay unclaimable.
  assertTrue(cdoEpoch.isEpochRunning());
}
```

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L214-216)
```text
    uint256 startAssets = underlyingToken.balanceOf(address(this)) + currentVaultAssets;
    // Any idle balance left on the contract between epochs is parked immediately into the vault.
    _depositToVault(underlyingToken.balanceOf(address(this)), 0);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L241-253)
```text
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

**File:** contracts/IdleCDOEpochVariant.sol (L374-376)
```text
    bool _mintInterest = isInterestMinted && !_isRequestingAllFunds;
    // In minted mode IdleCDO only needs cash for withdraw requests, not for epoch interest itself.
    uint256 _amountToPullFromBorrower = _mintInterest ? 0 : _expectedInterest;
```

**File:** contracts/IdleCDOEpochVariant.sol (L395-404)
```text
    if (isProgrammableBorrower) {
      // Ask the programmable borrower to recall ERC4626 liquidity before IdleCDO pulls funds.
      // Hook reverts bubble so transient ERC4626 liquidity failures can be retried.
      if (!IProgrammableBorrower(_borrower()).onStopEpoch(_amountToPullFromBorrower + _pendingWithdraws, _isRequestingAllFunds)) {
        // Emit the exact cash liability requested from the borrower, including recalled principal
        // in close-pool mode and excluding interest fronted through minted accounting.
        _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
        return;
      }
    }
```

**File:** contracts/IdleCreditVaultFactory.sol (L137-141)
```text
    cvParams.apr = 0;
    cvParams.isInterestMinted = true;
    cvParams.disableInstantWithdraw = true;
    cvParams.isDepositDuringEpochDisabled = true;
    (IdleCDOEpochVariant cv, IdleCreditVault strategy) = _deployBaseCreditVault(strategyData, cvParams);
```
