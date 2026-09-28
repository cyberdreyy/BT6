### Title
Attacker-controlled ERC4626 vault illiquidity permanently reverts `stopEpoch` via `StopEpochVaultLiquidityUnavailable` - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
The external bug is an infinite loop reachable by an attacker-controlled backend server (mod_proxy_ftp never exits its read loop). The credit-vault analog is a control flow that can never reach its exit because an attacker-controlled external counterparty keeps it looping/reverting: `ProgrammableBorrower.onStopEpoch` unconditionally calls `vault.withdraw(shortfall, ...)` inside a `try/catch` that reverts `StopEpochVaultLiquidityUnavailable` on any failure. A permissionless user of the vault's underlying Morpho markets can keep `vault.withdraw` permanently reverting by borrowing all available liquidity, making `IdleCDOEpochVariant.stopEpoch` uncallable forever and freezing all vault-held lender funds plus pending withdraw claims.

### Finding Description
In `onStopEpoch`, when on-hand balance is less than `_amountRequired`, the contract computes `shortfall` and, after checking it is covered by `_currentVaultAssets()`, calls `vault.withdraw` and reverts on any failure [1](#0-0) . The comment explicitly frames this as a "retryable" path, i.e., the intended behavior is that `stopEpoch` keeps being callable until the vault frees liquidity — the exact shape of the Apache bug: a loop whose exit condition is controlled by an attacker.

`vault` is an arbitrary ERC4626 (production deployments use MetaMorpho). MetaMorpho's `withdraw` iterates the withdraw queue over Morpho markets and reverts when the markets have no borrowable liquidity. Morpho borrowing is permissionless, so any EOA can borrow all available liquidity from every market in the vault's withdraw queue and keep it outstanding indefinitely by servicing interest (or opening fresh borrows on any repaid liquidity).

Meanwhile `IdleCDOEpochVariant` has no alternate settlement path: the revert inside `onStopEpoch` propagates out of `stopEpoch` before the `transferFrom`/`settleBorrowerInterest`/default branch is reached [2](#0-1) . `onDefault` is only invoked after a failed `transferFrom` on the success path of `onStopEpoch` [3](#0-2) , so the vault can never be defaulted out of this state. `emergencyExitVault` is owner/manager-only and also calls `vault.redeem`, which hits the same illiquidity revert [4](#0-3) .

Existing guards do not stop it: the `shortfall > _currentVaultAssets()` early-return only fires when the vault's *share value* doesn't cover the shortfall — share value stays intact during a liquidity crunch, only withdrawal fails [5](#0-4) .

### Impact Explanation
Temporary freezing of all funds parked in the ERC4626 vault, for as long as the attacker maintains the borrows. For a credit vault where most NAV is parked in the vault (the code deposits all idle balance each `onStartEpoch` [6](#0-5) ), this is effectively the entire pool: epochs cannot stop, pending withdraw requests cannot be funded, and defaulted/unpaid interest settlement is blocked. The attacker is an ordinary permissionless Morpho borrower — explicitly in scope as "a user of the programmable borrower's ERC4626 vault". Cost of attack is borrow interest on the drained markets; frozen amount equals the vault's full share value (e.g., the ~10k USDC scale of the existing tests, or the full TVL in production).

### Likelihood Explanation
Requires a MetaMorpho vault whose withdraw-queue markets have borrowable liquidity, plus an attacker willing to post collateral and pay borrow APR to keep the markets empty. Because `totalInterestDueNow`/`virtualPrice` do not depend on vault liquidity, there is no price signal deterring it. Any borrow spike (not necessarily malicious) also triggers the same freeze — the loop-exit condition is outside the vault's control, same as the attacker-controlled FTP backend.

### Recommendation
In `onStopEpoch`, do not revert inside the `catch`. Instead, return `false` (or a dedicated status) so `IdleCDOEpochVariant.stopEpoch` can route to the real-default/loss path, or cap the withdrawal at `vault.maxWithdraw(address(this))` and let the subsequent `transferFrom` shortfall trigger the existing default machinery. Alternatively, allow `stopEpoch` to proceed with a `lossAmount` haircut so lenders can exit rather than be frozen. The honest-actor default path already exists; it just needs to be reachable when the vault cannot pay.

### Proof of Concept
Fork test (extend `test/foundry/ProgrammableBorrowerCreditVault.t.sol`):

```solidity
function testAttackerFreezesStopEpochByDrainingVaultLiquidity() external {
    uint256 amount = 10_000 * oneScale;
    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);
    idleCDO.depositAA(amount);
    _startEpochAndCheckPrices(0);               // all idle balance parked in MetaMorpho vault

    // Attacker (any EOA): borrow all available liquidity in each Morpho market
    // in morphoVault.withdrawQueue() so that vault.withdraw() always reverts.
    _drainMorphoMarkets(morphoVault);           // helper: loop withdrawQueue, borrow max per market

    vm.warp(cdoEpoch.epochEndDate() + 1);

    // Honest manager tries to stop the epoch — permanently reverts
    vm.prank(manager);
    vm.expectRevert(ProgrammableBorrower.StopEpochVaultLiquidityUnavailable.selector);
    cdoEpoch.stopEpoch(0, 0);

    // No way out: stopEpoch retries keep reverting while attacker maintains borrows;
    // emergencyExitVault also reverts (vault.redeem hits same illiquidity);
    // default path is unreachable because onStopEpoch reverts before transferFrom.
    vm.prank(manager);
    vm.expectRevert();
    programmableBorrower.emergencyExitVault(0);
}
```

`_drainMorphoMarkets` iterates `morphoVault.withdrawQueueLength()`/`supplyQueue(i)`, and for each `IMorpho.Market` calls `morpho.borrow` with attacker-supplied collateral up to `market.totalBorrowAssets`-available liquidity. This requires no privileged role and demonstrates the permanently unreachable exit condition of the stop-epoch flow.

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L214-216)
```text
    uint256 startAssets = underlyingToken.balanceOf(address(this)) + currentVaultAssets;
    // Any idle balance left on the contract between epochs is parked immediately into the vault.
    _depositToVault(underlyingToken.balanceOf(address(this)), 0);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L231-268)
```text
  function onStopEpoch(uint256 _amountRequired, bool _isRequestingAllFunds) external nonReentrant returns (bool success) {
    _checkOnlyIdleCDO();
    _accrueBorrowerInterest();
    // if we want to close the pool and the borrower still owes any amount, consider it a failure and let IdleCDO handle it as a default instead of a close. 
    if (_isRequestingAllFunds && (borrowerPrincipal != 0 || borrowerInterestDebt != 0 || borrowerInterestAccrued != 0)) {
      return false;
    }

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
    }

    // `totalInterestDueNow()` was already read by IdleCDO before calling this hook, so once the
    // stop flow begins we can clear the previous carry and snapshot the remaining vault sleeve
    // as the baseline for measuring buffer-period vault PnL before the next epoch starts.
    bufferedVaultDelta = 0;
    bufferInterest = 0;
    bufferStartVaultAssets = _currentVaultAssets();

    // Stop reserving epoch-end withdraw liquidity once IdleCDO has started the stop flow.
    epochPendingWithdraws = 0;
    epochAccountingActive = false;
    emit EpochAccountingStopped();
    success = true;
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L285-301)
```text
  /// @notice Abort active epoch accounting after IdleCDO defaulted the facility.
  /// @dev This keeps borrower-side epoch state aligned with IdleCDO's stopped/defaulted state
  /// without depending on a live ERC4626 valuation. A hard default is terminal for the normal
  /// epoch flow, so there is no next buffer period that needs a `bufferStartVaultAssets` baseline.
  function onDefault() external nonReentrant {
    _checkOnlyIdleCDO();
    if (!epochAccountingActive) return;

    bufferedVaultDelta = 0;
    bufferInterest = 0;
    // Do not call `convertToAssets` here. Even if the external vault's valuation view is
    // unavailable during stress, CDO default handling must still be able to shut down borrowing.
    bufferStartVaultAssets = 0;
    epochPendingWithdraws = 0;
    epochAccountingActive = false;
    emit EpochAccountingStopped();
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L361-372)
```text
  function emergencyExitVault(uint256 _shares) external nonReentrant returns (uint256 assets) {
    _checkOnlyOwnerOrManager();
    if (_shares == 0) {
      _shares = vault.balanceOf(address(this));
    }
    if (_shares == 0) revert InvalidAmount();
    assets = vault.redeem(_shares, address(this), address(this));
    if (epochAccountingActive) {
      epochWithdrawnFromVault += assets;
    }
    emit RedeemedFromVault(_shares, assets, address(this));
  }
```
