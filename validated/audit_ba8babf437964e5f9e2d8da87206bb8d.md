### Title
Unprotected first-depositor inflation of the programmable borrower’s ERC4626 vault can steal epoch principal - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
`ProgrammableBorrower` accepts an arbitrary ERC4626 vault and deposits all idle pool capital through `_depositToVault` without enforcing a minimum share amount or using a protected initial deposit. [1](#0-0) [2](#0-1) 

An unprivileged depositor in a newly configured or empty permissionless vault can mint one share, donate enough underlying to inflate the share price, and cause the programmable borrower’s epoch principal deposit to mint zero shares. The attacker then redeems the single share and receives both the donation and the credit vault’s principal. [3](#0-2) 

### Finding Description
The factory supports a programmable-borrower deployment with a caller-supplied ERC4626 vault. [4](#0-3)  The borrower adapter validates only the vault address and asset, but does not require existing liquidity, protected shares, a whitelist, or a minimum amount of shares returned by `deposit`. [1](#0-0) 

At epoch start, `IdleCDOEpochVariant.startEpoch` sends pooled underlyings to the configured borrower, then calls `onStartEpoch`. [5](#0-4)  `ProgrammableBorrower.onStartEpoch` snapshots the pre-deposit balance as `epochStartVaultAssets` and deposits all on-hand underlyings into the external vault. [6](#0-5) 

For a vault with one attacker share and `D` donated assets, a common ERC4626 share calculation gives the programmable borrower approximately `P * 1 / (D + 1)` shares for a `P` deposit. Choosing `D >= P` can mint zero shares while transferring all `P` underlying to the vault. The attacker owns the entire share supply and can redeem it for approximately `D + P + 1`, for a net theft of the deposited pool principal. [2](#0-1) 

The resulting loss is not limited to a transient accounting error: `_vaultNetInterest` uses `vault.convertToAssets` as the source of truth, so the adapter later reports a principal-scale vault loss while IdleCDO’s strategy-token ledger still treats the position as backed. [7](#0-6)  A close-pool stop then asks the borrower for the recorded strategy-token principal, the subsequent `transferFrom` fails after the vault position has been drained, and the pool enters the default path. [8](#0-7) [9](#0-8) 

### Impact Explanation
The attacker can steal substantially all epoch principal routed into an inadequately initialized external vault. For a pool deposit of `P`, the attacker contributes roughly `P` as the donation and withdraws roughly `2P`, producing net proceeds of approximately `P`. The credit vault is left with zero or dust external-vault shares while its strategy-token accounting still records the principal until withdrawal or close-pool settlement exposes the insolvency. [10](#0-9) [11](#0-10) 

### Likelihood Explanation
The attack does not require the owner, manager, borrower, or Keyring administrator to act maliciously. It only requires the honestly configured ERC4626 vault to be empty or thin enough for the attacker to control its initial share price before the first programmable-borrower deposit. The deployment path explicitly accepts arbitrary vault addresses, and the adapter performs no defense against this standard ERC4626 inflation condition. [12](#0-11) 

Likelihood is lower for mature vaults whose share supply and liquidity are already large, or vaults that enforce permissioned deposits, minimum initial shares, or virtual-share defenses. It is highest during deployment or `setVault` migration to a newly created permissionless vault, where the attacker can seed the vault before the first epoch deposit. [13](#0-12) 

### Recommendation
Do not trust arbitrary ERC4626 share issuance as principal accounting. Before enabling a vault, require it to have sufficient existing liquidity and protected initial shares, or initialize it atomically with a non-withdrawable seed deposit. At minimum, `_depositToVault` should calculate expected shares, enforce `shares >= minShares`, and revert on zero-share deposits. The vault should also be screened for donation-sensitive share pricing, permissionless first deposits, and external share-supply manipulation. [2](#0-1) 

### Proof of Concept
This Foundry test can be added to `test/foundry/ProgrammableBorrowerCreditVault.t.sol`. It forks mainnet for real USDC and uses the existing deterministic ERC4626 fixture to model a vault without inflation protection.

```solidity
function testFirstDepositorInflationStealsEpochPrincipal() external {
  InflationVault vulnerableVault = new InflationVault(USDC);
  _setUpProgrammableBorrowerCreditVault(FORK_BLOCK, address(vulnerableVault));

  address attacker = makeAddr("attacker");
  uint256 poolPrincipal = 10_000 * oneScale;
  uint256 donation = poolPrincipal;

  // Attacker becomes the first depositor and inflates assets per share.
  deal(USDC, attacker, donation + 1, true);
  vm.startPrank(attacker);
  underlying.approve(address(vulnerableVault), type(uint256).max);
  vulnerableVault.deposit(1, attacker);
  underlying.transfer(address(vulnerableVault), donation);
  vm.stopPrank();

  // Honest LP deposits and honest manager starts the epoch.
  idleCDO.depositAA(poolPrincipal);
  vm.prank(manager);
  cdoEpoch.startEpoch();

  // The programmable borrower transferred the principal but received no shares.
  assertEq(vulnerableVault.balanceOf(address(programmableBorrower)), 0);
  assertEq(vulnerableVault.balanceOf(attacker), 1);

  // The attacker redeems the only share and extracts donation + pool principal.
  uint256 attackerBefore = underlying.balanceOf(attacker);
  vm.prank(attacker);
  uint256 extracted = vulnerableVault.redeem(1, attacker, attacker);

  assertEq(extracted, donation + poolPrincipal + 1);
  assertEq(underlying.balanceOf(attacker), attackerBefore + extracted);

  // A close-pool stop exposes the missing principal and defaults the facility.
  vm.warp(cdoEpoch.epochEndDate() + 1);
  vm.prank(manager);
  cdoEpoch.stopEpoch(0, 1);
  assertTrue(cdoEpoch.defaulted());
}
```

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L108-135)
```text
  function initialize(
    address _vault, address _idleCDO, address _owner,
    address _manager, address _borrower, uint256 _borrowerApr
  ) external initializer {
    if (
      _vault == address(0) || _owner == address(0) || _manager == address(0) ||
      _borrower == address(0) || _idleCDO == address(0)
    ) {
      revert InvalidAddress();
    }
    address _underlyingToken = IIdleCDOToken(_idleCDO).token();
    if (_underlyingToken == address(0) || IERC4626(_vault).asset() != _underlyingToken) revert InvalidAddress();

    __Ownable_init();
    __ReentrancyGuard_init();
    transferOwnership(_owner);

    underlyingToken = IERC20Detailed(_underlyingToken);
    vault = IERC4626(_vault);
    idleCDO = _idleCDO;
    manager = _manager;
    borrower = _borrower;
    borrowerApr = _borrowerApr;

    // Set approvals for vault and IdleCDO
    _allowUnlimitedSpend(_underlyingToken, _vault);
    _allowUnlimitedSpend(_underlyingToken, _idleCDO);
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L153-170)
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
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L198-224)
```text
  /// @notice Hook called by IdleCDOEpochVariant when a new epoch starts.
  /// It deposits all on-hand assets into the vault and starts accounting.
  /// @param _pendingWithdraws pending withdraw requests amount to reserve for epoch end.
  function onStartEpoch(uint256 _pendingWithdraws) external nonReentrant {
    _checkOnlyIdleCDO();
    _accrueBorrowerInterest();
    // Reserve the amount IdleCDO expects to pull back at stopEpoch before the real borrower can draw again.
    epochPendingWithdraws = _pendingWithdraws;
    uint256 currentVaultAssets = _currentVaultAssets();
    uint256 bufferStartAssets = bufferStartVaultAssets;
    // Carry vault PnL generated while the pool was in the buffer into the new active epoch so it
    // is eventually realized in tranche prices at the next stopEpoch.
    bufferedVaultDelta = int256(currentVaultAssets) - int256(bufferStartAssets);
    bufferStartVaultAssets = 0;
    // Snapshot total assets before re-depositing idle cash so the epoch principal baseline uses the
    // exact pre-deposit amount instead of a post-deposit share-conversion round-down.
    uint256 startAssets = underlyingToken.balanceOf(address(this)) + currentVaultAssets;
    // Any idle balance left on the contract between epochs is parked immediately into the vault.
    _depositToVault(underlyingToken.balanceOf(address(this)), 0);
    // Reset the epoch accounting baseline from the exact pre-start total assets so the new epoch
    // does not treat the just-deposited idle balance as fresh vault profit or loss.
    epochStartVaultAssets = startAssets;
    epochDepositedToVault = 0;
    epochWithdrawnFromVault = 0;
    epochAccountingActive = true;
    emit EpochAccountingStarted(startAssets);
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L325-347)
```text
  /// @notice Total net epoch interest due to the pool at stop.
  /// @dev This is the single value read by IdleCDO to price the epoch: borrower contractual
  /// interest plus paid buffer interest plus positive vault PnL minus vault losses. It is a
  /// pool-facing value, so it can be lower than `borrowerInterestDebt` when the borrower still
  /// owes full contractual interest but the vault sleeve suffered a loss.
  function totalInterestDueNow() external view returns (uint256) {
    (uint256 vaultInterest, uint256 loss) = _vaultNetInterest();
    uint256 totalGain = vaultInterest + borrowerInterestAccruedNow() + bufferInterest;
    return totalGain > loss ? totalGain - loss : 0;
  }

  /// @notice Compute the net vault delta split into interest and loss (mutually exclusive).
  function _vaultNetInterest() internal view returns (uint256 interest, uint256 loss) {
    if (!epochAccountingActive) return (0, 0);
    uint256 earnedAssets = _currentVaultAssets() + epochWithdrawnFromVault;
    uint256 principalAssets = epochStartVaultAssets + epochDepositedToVault;
    int256 netDelta = bufferedVaultDelta + int256(earnedAssets) - int256(principalAssets);
    if (netDelta > 0) {
      interest = uint256(netDelta);
    } else {
      loss = uint256(-netDelta);
    }
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L374-385)
```text
  /// @notice Move a specific amount of idle underlying into the vault.
  /// @param _assetAmount Amount of underlying to deposit
  /// @param _principalAssets Portion of the deposited assets that should extend the epoch principal
  /// baseline instead of being recognized as current-epoch profit.
  function _depositToVault(uint256 _assetAmount, uint256 _principalAssets) internal {
    if (_assetAmount == 0) return;
    uint256 shares = vault.deposit(_assetAmount, address(this));
    if (epochAccountingActive && _principalAssets != 0) {
      epochDepositedToVault += _principalAssets;
    }
    emit DepositedIntoVault(_assetAmount, shares);
  }
```

**File:** contracts/IdleCreditVaultFactory.sol (L126-150)
```text
  function deployRevolvingCreditVault(
    CreditVaultParams memory cvParams,
    StrategyData memory strategyData,
    ProgrammableBorrowerParams memory programmableBorrowerParams,
    AncillaryParams memory ancillaryParams
  ) external {
    if (ancillaryParams.writeOffImplementation != address(0)) revert WriteOffUnsupported();
    _checkMinimumFees(cvParams);
    address manager = strategyData.manager;
    if (manager == address(0)) revert Is0();

    cvParams.apr = 0;
    cvParams.isInterestMinted = true;
    cvParams.disableInstantWithdraw = true;
    cvParams.isDepositDuringEpochDisabled = true;
    (IdleCDOEpochVariant cv, IdleCreditVault strategy) = _deployBaseCreditVault(strategyData, cvParams);
    address keyringWhitelist = _deployKeyring(ancillaryParams);
    _configureCreditVault(cv, strategy, cvParams, keyringWhitelist, manager);

    ProgrammableBorrower programmableBorrower = _deployProgrammableBorrower(
      programmableBorrowerParams,
      cv,
      strategy,
      manager
    );
```

**File:** contracts/IdleCreditVaultFactory.sol (L210-233)
```text
  function _deployProgrammableBorrower(
    ProgrammableBorrowerParams memory programmableBorrowerParams,
    IdleCDOEpochVariant cv,
    IdleCreditVault strategy,
    address manager
  ) internal returns (ProgrammableBorrower programmableBorrower) {
    programmableBorrower = ProgrammableBorrower(_deployProxy(
      programmableBorrowerParams.implementation,
      abi.encodeWithSelector(
        ProgrammableBorrower.initialize.selector,
        programmableBorrowerParams.vault,
        address(cv),
        address(this),
        manager,
        programmableBorrowerParams.borrower,
        programmableBorrowerParams.borrowerApr
      )
    ));

    strategy.setBorrower(address(programmableBorrower));
    // Programmable mode is explicit and should always be enabled on the revolving path.
    cv.setIsProgrammableBorrower(true);
    programmableBorrower.transferOwnership(treasury);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L270-304)
```text
    // transfer in this contract funds from interest payment (if any) and buffer deposits sent to the strategy
    uint256 _totEpochDeposits = _strategy.totEpochDeposits();
    // If interest is minted we do not transfer interest to the strategy
    uint256 _toSend = isInterestMinted ? _totEpochDeposits : lastEpochInterest + _totEpochDeposits;
    _strategy.sendInterestAndDeposits(_toSend);

    // we should first check if there are *instant* redeem requests pending 
    // and if yes we should send as much underlyings as possible to the IdleCreditVault contract
    // if there is any surplus then we send those to the borrower
    uint256 pendingInstant = _pendingInstant();
    uint256 totUnderlyings = _contractTokenBalance(token);
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();
    _strategy.collectInstantWithdrawFunds(pendingInstant > totUnderlyings ? totUnderlyings : pendingInstant);

    // if there are more requests than the current underlyings we simply send all underlyings
    // to the IdleCreditVault contract
    if (pendingInstant > totUnderlyings) {
      // if borrower is programmable, notify epoch start even if no funds were sent
      _startEpochProgrammableBorrower(_pendingWithdraws);
      return;
    }
    // allow instant withdraws right away without waiting for the deadline
    allowInstantWithdraw = true;
    // and transfer the surplus to the borrower
    uint256 _toBorrower = totUnderlyings - pendingInstant;
    try this.sendFundsToBorrower(_toBorrower) {
      // funds transferred correctly
      _startEpochProgrammableBorrower(_pendingWithdraws);
    } catch {
      // The borrower did not receive the funds, so keep the strategy-token backing in the strategy.
      _transferUnderlyings(address(_strategy), _toBorrower);
      _strategy.reserveDefaultRecovery(_toBorrower);
      _handleBorrowerDefault(_toBorrower);
    }
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L366-404)
```text
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
```

**File:** contracts/IdleCDOEpochVariant.sol (L408-505)
```text
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

**File:** contracts/IdleCDOEpochVariant.sol (L576-599)
```text
  /// @notice Handle borrower default
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L160-176)
```text
  /// @notice strategy token decimals
  /// @dev equal to underlying token decimals
  /// @return number of decimals
  function decimals() public view override returns (uint8) {
    return uint8(tokenDecimals);
  }

  /// @notice strategy token address
  function strategyToken() external view override returns (address) {
    return address(this);
  }

  /// @notice return strategy token price which is always 1
  /// @return price in underlyings
  function price() public view virtual override returns (uint256) {
    return oneToken;
  }
```
