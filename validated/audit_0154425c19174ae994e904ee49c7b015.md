### Title
Missing zero-address validation for `_borrower` in `IdleCreditVault.initialize` forces borrower default at first epoch start - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.initialize` assigns `borrower`, `manager`, and `token` without validating them for non-zero values, even though the later setter `setBorrower` explicitly enforces `_borrower != address(0)` [1](#0-0) . If the vault is initialized with `_borrower == address(0)`, the first `startEpoch` on `IdleCDOEpochVariant` transfers epoch funds to `address(0)`; the underlying transfer reverts, is caught, and triggers `_handleBorrowerDefault`, permanently marking the pool defaulted and freezing lender funds behind the default-recovery path.

### Finding Description
The initializer stores `borrower = _borrower` and `manager = _manager` with no zero checks [2](#0-1) . In `startEpoch`, the CDO self-calls `sendFundsToBorrower`, which does `_transferUnderlyings(_borrower(), _amount)` where `_borrower()` reads `IdleCreditVault(strategy).borrower()` [3](#0-2) . [4](#0-3) . With a zero borrower, the ERC20 transfer to `address(0)` reverts, the catch branch parks the funds in the strategy via `reserveDefaultRecovery` and calls `_handleBorrowerDefault` [5](#0-4) . The same unchecked parameter is fixable via `setBorrower` before the first epoch, but once `defaulted = true` is set, `restoreOperations` cannot clear it (`_checkNotAllowed(defaulted ...)`) and only `finalizeDefault` can unwind funds [6](#0-5) .

### Impact Explanation
A zero borrower turns the first epoch start into an involuntary default: `defaulted` latches true, `isEpochRunning` clears, withdrawal requests are disabled, and the pool pauses [7](#0-6) . Principal is preserved in `defaultRecoveryReserve`, but all lender claims are frozen until owner/manager run `finalizeDefault`, and receipt payouts route through the haircut recovery path rather than normal claims [8](#0-7) . The inconsistency between the unchecked initializer and the checked `setBorrower` setter shows the zero check was intended but missed at init, mirroring the reference finding.

### Likelihood Explanation
Exploitation requires a deployment-time misconfiguration (owner initializes with zero `_borrower`), so it is not an attacker-driven path — the honest-owner assumption means this can only occur through deployer error or a miswired factory call. Once it occurs, however, the failure is deterministic at the first `startEpoch`, because the zero-address transfer always reverts inside the try/catch. I could not fully verify whether `IdleCreditVaultFactory` independently validates the borrower before calling `initialize`; if it does not, the exposure is real at deploy time.

### Recommendation
Add zero-address validation in `IdleCreditVault.initialize` for `_underlyingToken`, `_owner`, `_manager`, and `_borrower`, matching the `setBorrower` guard, e.g. `require(_borrower != address(0) && _underlyingToken != address(0), "IS_0")`. Optionally also validate in `IdleCreditVaultFactory`/deployment scripts so a misconfigured vault cannot be wired into a live `IdleCDOEpochVariant`.

### Proof of Concept
```solidity
// Fork test: deploy IdleCreditVault with borrower = address(0)
IdleCreditVault strat = new IdleCreditVault();
strat.initialize(address(USDC), owner, manager, address(0), "Borrower", apr);

IdleCDOEpochVariant cdo = deployCDO(token, address(strat));
// KYC'd lender deposits during buffer
cdo.depositAA(1_000_000e6); // succeeds

// owner/manager starts the first epoch
vm.warp(epochEndDate + bufferPeriod + 1);
cdo.startEpoch();

// sendFundsToBorrower -> safeTransfer(address(0), amount) reverts -> catch ->
// _handleBorrowerDefault; assert pool is defaulted and claims are frozen
assertTrue(cdo.defaulted());
assertFalse(cdo.isEpochRunning());
assertFalse(cdo.allowAAWithdrawRequest());
vm.expectRevert(); // normal claimWithdrawRequest path is gated off
cdo.requestWithdraw(0, cdo.AATranche());
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L141-142)
```text
    borrower = _borrower;
    manager = _manager;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L191-194)
```text
  function setBorrower(address _borrower) external onlyOwner {
    require(_borrower != address(0), "IS_0");
    borrower = _borrower;
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L194-225)
```text
  function finalizeDefault(uint256 _recoveredAmount, address _recoverySource) external {
    _checkOnlyOwnerOrManager();
    // Send raw CDO underlying to feeReceiver as donated assets; recovery must enter through the strategy.
    _skimDonatedAssets();
    // Recovery math and reserve accounting live in the strategy where receipt claims are paid.
    uint256 defaultBBNav = IdleCreditVault(strategy).finalizeDefaultRecovery(_recoveredAmount, _recoverySource);

    // Default recovery should not keep accruing fees or leave old fee claims senior to LP recovery.
    fee = 0;
    managementFee = 0;
    unclaimedFees = 0;
    latestHarvestBlock = block.timestamp;

    // The strategy returns BB's final recovered active NAV. Writing both final NAVs directly
    // makes hard default the sole exception to the ordinary BB-first loss waterfall.
    lastNAVBB = defaultBBNav;
    lastNAVAA = _contractTokenBalance(strategyToken) - defaultBBNav;

    // Crystallize the strategy-token rebalance so virtualPrice/tranchePrice expose the realized loss.
    _forceUpdateAccounting();

    expectedEpochInterest = 0;
    pendingWithdrawFees = 0;
    allowAAWithdrawRequest = true;
    allowBBWithdrawRequest = true;
    // This flag gates claimInstantWithdrawRequest. New instant requests stay disabled below.
    allowInstantWithdraw = true;
    disableInstantWithdraw = true;
    epochDuration = 0;
    epochEndDate = 0;
    _setScaledApr(0);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L295-303)
```text
    try this.sendFundsToBorrower(_toBorrower) {
      // funds transferred correctly
      _startEpochProgrammableBorrower(_pendingWithdraws);
    } catch {
      // The borrower did not receive the funds, so keep the strategy-token backing in the strategy.
      _transferUnderlyings(address(_strategy), _toBorrower);
      _strategy.reserveDefaultRecovery(_toBorrower);
      _handleBorrowerDefault(_toBorrower);
    }
```

**File:** contracts/IdleCDOEpochVariant.sol (L577-599)
```text
  function _handleBorrowerDefault(uint256 funds) internal {
    defaulted = true;
    // Do not reopen instant claims here. They remain disabled when funding is pending;
    // successful full funding is the only path that enables them before finalization.

    if (isProgrammableBorrower) {
      IProgrammableBorrower(_borrower()).onDefault();
    }

    // deposits should be already prevented
    if (!paused()) {
      _pause();
    }

    // stop the current epoch
    isEpochRunning = false;

    // prevent withdrawals requests
    allowAAWithdrawRequest = false;
    allowBBWithdrawRequest = false;

    emit BorrowerDefault(funds);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L619-632)
```text
  function restoreOperations() external override {
    _checkOnlyOwner();
    // Check if the pool was defaulted
    _checkNotAllowed(defaulted || priceAA == 0);
    skipDefaultCheck = false;
    // During an epoch ordinary deposits and withdrawal requests must remain disabled. Clearing
    // the emergency flag intentionally restores only the dedicated depositDuringEpoch path.
    if (isEpochRunning) return;
    if (paused()) {
      _unpause();
    }
    allowAAWithdrawRequest = true;
    allowBBWithdrawRequest = true;
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L827-829)
```text
  function _borrower() internal view returns (address) {
    return IdleCreditVault(strategy).borrower();
  }
```
