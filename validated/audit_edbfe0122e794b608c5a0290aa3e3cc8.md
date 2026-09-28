### Title
A reverting/illiquid ERC4626 vault in `ProgrammableBorrower` can permanently wedge `stopEpoch` before the default path runs - ([File: contracts/strategies/idle/ProgrammableBorrower.sol])

### Summary
`IdleCDOEpochVariant._stopEpoch` calls `IProgrammableBorrower.onStopEpoch` before the `try/catch` around `getFundsFromBorrower`. If the programmable borrower’s ERC4626 sleeve reports enough share value to cover the shortfall but `vault.withdraw` reverts, `onStopEpoch` reverts `StopEpochVaultLiquidityUnavailable`, bubbles out of `_stopEpoch`, and never reaches the borrower-default handler. Because `setVault` is blocked while `epochAccountingActive` or vault shares remain, and `epochAccountingActive` is cleared only on a successful stop, the epoch can remain running indefinitely with user withdrawals disabled. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Finding Description
The credit-vault analog of the adapter-DoS bug is the single ERC4626 “adapter” used by `ProgrammableBorrower`. During `stopEpoch`, IdleCDO resolves interest, previews loss-adjusted withdrawals, then calls `onStopEpoch(_amountToPullFromBorrower + _pendingWithdraws, _isRequestingAllFunds)` outside the later `try/catch`. In `onStopEpoch`, if `shortfall <= _currentVaultAssets()` the code attempts `vault.withdraw(shortfall, ...)`; any revert is converted into `StopEpochVaultLiquidityUnavailable`. That revert is intentionally retryable for transient liquidity failures, but it bypasses `_handleBorrowerDefault` because the catchable borrower pull happens only afterward. [5](#0-4) [6](#0-5) 

The stuck state is durable for a malfunctioning ERC4626: `onStopEpoch` clears `epochAccountingActive` only after the withdrawal succeeds, while `setVault` refuses to change vaults when `epochAccountingActive` or `vault.balanceOf(address(this))` is nonzero. Meanwhile `startEpoch` paused deposits and disabled withdrawal requests, and `_beforeUnpause` blocks unpausing while `isEpochRunning` remains true. [3](#0-2) [4](#0-3) [7](#0-6) [8](#0-7) 

### Impact Explanation
The broken invariant is liveness/recoverability of the epoch state machine: a covered external-vault withdrawal failure is neither retried to completion nor converted into the existing default path. The frozen value is the pool capital parked in or accounted through the ERC4626 sleeve plus matured pending withdrawals; in the worst case this is the entire active TVL because the epoch cannot stop, default, or close. This is stronger than a pure gas/DoS nuisance because tranche users’ requests were already disabled at `startEpoch`, `claimWithdrawRequest` depends on a later successful stop/finalization, and the owner cannot switch vaults while shares remain and accounting is active. [7](#0-6) [9](#0-8) [10](#0-9) 

### Likelihood Explanation
Likelihood depends on the configured ERC4626 vault’s liquidity/reliability, but the triggering actor can be an unprivileged user of that vault. An external vault user can withdraw available cash or otherwise put the ERC4626 into a state where `convertToAssets`/`balanceOf` still value the borrower’s shares above the shortfall while `withdraw`/`redeem` reverts or cannot pay out. The code deliberately treats the covered-shortfall case as retryable and reserves default only for insufficient share value or borrower transfer failure, so an ordinary vault outage is enough to wedge settlement without making the owner, manager, borrower, or queue malicious. [11](#0-10) [1](#0-0) [12](#0-11) 

### Recommendation
Do not let `onStopEpoch` propagate a covered-but-failed ERC4626 withdrawal as an unbounded retry. Make `onStopEpoch` return `false` for withdrawal failures so `_stopEpoch` enters `_handleBorrowerDefault`, or catch the vault failure inside `onStopEpoch`, mark accounting inactive only under an explicit owner/manager recovery path, and allow `setVault`/emergency redemption after settling or defaulting the sleeve. Add an owner/manager escape that can force default or close-pool settlement when `vault.withdraw`/`redeem` reverts despite positive share value. [1](#0-0) [13](#0-12) [14](#0-13) 

### Proof of Concept
Foundry fork test sketch:

1. Deploy `IdleCDOEpochVariant` + `IdleCreditVault` in programmable-borrower mode with a real forked ERC4626 vault as `ProgrammableBorrower.vault`.
2. Start an epoch normally: KYC’d LPs deposit in buffer, owner calls `startEpoch`, funds are parked via `onStartEpoch -> _depositToVault`.
3. As an unprivileged ERC4626 vault user, remove underlying liquidity / put the fork vault into a paused-withdrawal state while PB still holds shares and `_currentVaultAssets()` reports value at least equal to the stop shortfall.
4. Warp past `epochEndDate` and have honest owner/manager call `stopEpoch(_newApr, _interest)`.
5. `_stopEpoch` reaches `onStopEpoch`; `shortfall <= _currentVaultAssets()` so it tries `vault.withdraw`, the external vault reverts, PB reverts `StopEpochVaultLiquidityUnavailable`, and the call never reaches `getFundsFromBorrower`/`_handleBorrowerDefault`.
6. Assert `isEpochRunning == true`, `paused() == true`, `allowAAWithdrawRequest == false`, `allowBBWithdrawRequest == false`, `epochAccountingActive == true`, and `setVault` reverts while shares remain. Repeat `stopEpoch` after user vault operations fail to restore liquidity; the epoch remains stopped only by reverting, so requests and claims stay frozen.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L244-250)
```text
    isEpochRunning = true;
    // prevent deposits
    _pause();

    // prevent withdrawals requests
    allowAAWithdrawRequest = false;
    allowBBWithdrawRequest = false;
```

**File:** contracts/IdleCDOEpochVariant.sol (L353-505)
```text
    uint256 _totBorrowed = _beforeStopEpoch(_isRequestingAllFunds);
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();

    _interest = _resolveStopEpochInterest(_interest);

    // Base interest for stopEpoch: explicit override (>1) or precomputed expected epoch interest.
    uint256 _expectedInterest;
    // Strategy finalizes APR0 bucket state and returns adjusted stopEpoch values.
    (_expectedInterest, _pendingWithdrawFees) = _strategy.prepareStopEpochWithApr0(_interest);
    // Pending withdraws may be increased during APR0 settlement, so read after prepareStopEpochWithApr0.
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();

    // special case where we get everything back from the borrower
    if (_isRequestingAllFunds) {
      // Recall gross strategy-token principal, including fee backing excluded from net CDO NAV.
      // Prefunded variants also add queue deposits already sent directly to the borrower.
      _totBorrowed += _contractTokenBalance(strategyToken);
      _expectedInterest += _totBorrowed;
    }
    uint256 _grossInterest = _isRequestingAllFunds ? _expectedInterest - _totBorrowed : _expectedInterest;
    bool _mintInterest = isInterestMinted && !_isRequestingAllFunds;
    // In minted mode IdleCDO only needs cash for withdraw requests, not for epoch interest itself.
    uint256 _amountToPullFromBorrower = _mintInterest ? 0 : _expectedInterest;
    if (_mintInterest && _interest > 1) {
      uint256 _maxApr = _strategy.maxApr();
      _checkNotAllowed(_maxApr != 0 && _grossInterest > _calcInterestWithApr(getContractValue(), _maxApr) + _pendingWithdrawFees);
    }

    // Checkpoint management fees before borrower funds are pulled in so the elapsed-period
    // accrual applies only to the pre-stop live NAV, not to newly received epoch interest.
    _accrueManagementFee();

    // Persist only resolved epoch interest for recovery accounting. In close-pool mode `_interest == 1`
    // is a sentinel: `_grossInterest` excludes the principal added to `_expectedInterest` above.
    expectedEpochInterest = _grossInterest;
    pendingWithdrawFees = _pendingWithdrawFees;

    // Pending receipts have no tranche identity, so their aggregate loss is pro rata across all
    // receipts. The remaining active loss is applied BB-first after the stop succeeds.
    (_pendingWithdraws, _lossAmount) = _strategy.previewLossAdjustedWithdrawFunds(_lossAmount);

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

    // accrue interest to idleCDO, this will increase tranche prices.
    // Send also tot withdraw requests amount to the IdleCreditVault contract
    try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
      // transfer in strategy and decrease pendingWithdraws
        _strategy.collectWithdrawFunds(_pendingWithdraws);
      // Only settle borrower interest when CDO is fronting it (minted mode, not closing pool).
      // When requesting all funds (_interest == 1) the CDO pulls cash directly, no fronting.
      if (_mintInterest && isProgrammableBorrower) {
        IProgrammableBorrower(_borrower()).settleBorrowerInterest();
      }
      // Split pending withdraw fees before update accounting
      // NOTE: Fees are sent with 2 different transfer calls, here and after updateAccounting, to avoid complicated calculations
      if (!_mintInterest) {
        _transferFeeUnderlyings(_pendingWithdrawFees);
      }

      if (_isRequestingAllFunds) {
        // we already have strategyTokens equal to _totBorrowed in this contract
        // so we transfer _totBorrowed to the strategy to avoid double counting for getContractValue
        _transferUnderlyings(address(_strategy), _totBorrowed);
      }

      if (_mintInterest) {
        // if interest is not transferred we mint strategy tokens equal to the full epoch interest
        if (_grossInterest != 0) _strategy.mintStrategyTokens(_grossInterest);
        // and increase unclaimedFees by pending withdraw fees before _updateAccounting
        unclaimedFees += _pendingWithdrawFees;
      }

      // update tranche prices and unclaimed fees
      _updateAccounting();

      // transfer fees
      uint256 _fees = unclaimedFees;
      if (_mintInterest) {
        // If interest is minted then we mint new shares for fee receivers instead of transferring underlyings
        if (_fees != 0) {
          uint256 feeReceiverAmount = _feeReceiverAmount(_fees);
          if (feeReceiverAmount != 0) {
            _mintSharesAtCurrPrice(feeReceiverAmount, feeReceiver, AATranche);
          }
          _mintSharesAtCurrPrice(_fees - feeReceiverAmount, owner(), AATranche);
          _updateSplitRatio(_getAARatio(true));
        }
      } else {
        // Cash-funded fees can only use gross interest not already owed to pending withdrawals.
        uint256 _availableForFees = _grossInterest > _pendingWithdrawFees ? _grossInterest - _pendingWithdrawFees : 0;
        if (_fees > _availableForFees) {
          _fees = _availableForFees;
        }
        _transferFeeUnderlyings(_fees);
      }
      // Any fee that cannot be paid in cash remains accrued and continues reducing NAV.
      unclaimedFees -= _fees;

      uint256 _totalFees = _fees + (_mintInterest ? 0 : _pendingWithdrawFees);
      // save net gain (this does not include interest gained for pending withdrawals)
      uint256 netInterest = _grossInterest > _totalFees ? _grossInterest - _totalFees : 0;
      lastEpochInterest = netInterest;
      // mint strategyTokens equal to interest and send underlying to strategy to avoid double counting for NAV
      _strategy.deposit(_mintInterest ? 0 : netInterest);

      // save last apr, unscaled
      lastEpochApr = _strategy.unscaledApr();
      // set apr for next epoch
      _setScaledApr(_newApr);

      // stop epoch
      isEpochRunning = false;
      expectedEpochInterest = 0;
      pendingWithdrawFees = 0;

      if (!skipDefaultCheck) {
        // Reopen ordinary deposits and requests only when operations were not explicitly shut down.
        _unpause();
        allowAAWithdrawRequest = true;
        allowBBWithdrawRequest = true;
      }
      // block instant withdraws claims as these can be done only after the deadline
      // or only if borrower is repaying all funds
      allowInstantWithdraw = _isRequestingAllFunds;

      if (_isRequestingAllFunds) {
        // user will request only normal withdraw and can claim right after
        disableInstantWithdraw = true;
        epochDuration = 0;
        epochEndDate = 0;
      }

      emit AccrueInterest(_expectedInterest - _totBorrowed, _totalFees);
      if (_lossAmount != 0) {
        _strategy.burnStrategyTokens(_lossAmount);
        // Realize the active loss immediately through the ordinary BB-first waterfall.
        _forceUpdateAccounting();
      }
    } catch {
      // if borrower defaults, prev instant withdraw requests can still be withdrawn
      // as were already fullfilled prior to the default (all funds already sent to the strategy)
      _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
    }
```

**File:** contracts/IdleCDOEpochVariant.sol (L577-598)
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
```

**File:** contracts/IdleCDOEpochVariant.sol (L634-637)
```text
  /// @notice Prevent external unpause while an epoch, emergency shutdown, or hard default is active.
  function _beforeUnpause() internal view override {
    _checkNotAllowed(defaulted || isEpochRunning || skipDefaultCheck);
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L153-169)
```text
  /// @notice Set the vault used to deploy idle funds.
  /// @dev Owner or manager. This does not migrate an existing position. The operator must first
  /// withdraw from the old vault and wait until epoch accounting is inactive, otherwise assets can
  /// remain stranded there and the live accounting views will stop including them after the switch.
  /// @param _vault new ERC4626 vault address
  function setVault(address _vault) external {
    _checkOnlyOwnerOrManager();
    if (_vault == address(0) || IERC4626(_vault).asset() != address(underlyingToken)) {
      revert InvalidAddress();
    }
    // Switching the accounting source is only safe once the current epoch is fully settled and the
    // old vault position has been unwound.
    if (epochAccountingActive || vault.balanceOf(address(this)) != 0) revert NotAllowed();
    underlyingToken.safeApprove(address(vault), 0);
    vault = IERC4626(_vault);
    _allowUnlimitedSpend(address(underlyingToken), _vault);
    emit VaultUpdated(_vault);
```

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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L263-266)
```text
    // Stop reserving epoch-end withdraw liquidity once IdleCDO has started the stop flow.
    epochPendingWithdraws = 0;
    epochAccountingActive = false;
    emit EpochAccountingStopped();
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L361-371)
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
```
