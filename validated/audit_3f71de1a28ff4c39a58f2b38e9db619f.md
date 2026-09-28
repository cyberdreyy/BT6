### Title
External ERC4626 vault liquidity withdrawal can indefinitely delay pending redemptions - ([File: `contracts/strategies/idle/ProgrammableBorrower.sol`])

### Summary

In programmable-borrower mode, matured withdrawal requests are economically reserved through `epochPendingWithdraws`, but the underlying liquidity is still deposited into a shared ERC4626 vault. Another vault depositor can consume the vault's available liquidity before `stopEpoch`, causing `ProgrammableBorrower.onStopEpoch` to revert even though the programmable borrower's vault shares still cover the obligation. This leaves `stopEpoch` retryable but unsuccessful and keeps the pending withdraw receipts temporarily frozen. [1](#0-0) [2](#0-1) 

### Finding Description

When a user calls `requestWithdraw`, the CDO burns the tranche tokens and `IdleCreditVault.requestWithdraw` records the claim in `pendingWithdraws` for the following epoch-end settlement. [3](#0-2) [4](#0-3) 

At `startEpoch`, the CDO sends the available assets to the programmable borrower and invokes `onStartEpoch(_pendingWithdraws)`. The borrower records that amount in `epochPendingWithdraws`, but then deposits its full on-hand balance into the external ERC4626 vault. [5](#0-4) [1](#0-0) 

The reservation is enforced only against the protocol's borrower: `availableToBorrow` subtracts `epochPendingWithdraws` from total on-hand and vault assets, and `_borrow` rejects draws above that amount. [6](#0-5) [7](#0-6) 

At `stopEpoch`, the CDO asks the programmable borrower to make `_amountToPullFromBorrower + _pendingWithdraws` liquid. If the borrower lacks on-hand assets, `onStopEpoch` checks only that its vault shares economically cover the shortfall and then calls `vault.withdraw(shortfall, ...)`. If the shared vault has insufficient currently withdrawable assets, the vault call reverts and `onStopEpoch` deliberately reverts with `StopEpochVaultLiquidityUnavailable`, reverting the entire CDO `stopEpoch` transaction. [8](#0-7) [2](#0-1) 

The broken invariant is that accounting value is treated as immediately withdrawable liquidity. A third-party ERC4626 depositor can redeem or withdraw vault liquidity after the pending request has been reserved from the internal borrower but before `stopEpoch`, leaving `convertToAssets` coverage intact while cash liquidity is below `pendingWithdraws`. [6](#0-5) [2](#0-1) 

### Impact Explanation

A matured withdrawal receipt remains unfunded because `pendingWithdraws` is not reduced and `collectWithdrawFunds` is never reached. The epoch remains `isEpochRunning`, so users cannot create or settle the next normal withdrawal lifecycle until external vault liquidity returns or the vault otherwise frees enough assets. [9](#0-8) [10](#0-9) 

The quantified temporary freeze is at least `pendingWithdraws`; in close-pool mode it can extend to the full principal-plus-interest amount being recalled because `_interest == 1` adds the strategy-token principal to the amount pulled from the borrower. [11](#0-10) 

This is not a borrower default: the failure occurs before `getFundsFromBorrower` and leaves `defaulted == false`, `isEpochRunning == true`, and borrower epoch accounting active. The repository already contains a deterministic regression demonstrating this exact sequence and state outcome. [12](#0-11) 

### Likelihood Explanation

Likelihood depends on the configured ERC4626 vault having shared, finite withdrawable liquidity. The attacker needs only to be a permitted depositor or liquidity user of that external vault, an explicitly unprivileged actor, and needs enough vault liquidity access to reduce available assets below the CDO's pending withdrawal requirement. [13](#0-12) [14](#0-13) 

No privileged Idle role is required. The borrower cannot bypass the reserve through `borrow`, but that guard does not reserve vault liquidity against unrelated vault shareholders or external borrowers. [15](#0-14) 

### Recommendation

Do not deposit assets backing `epochPendingWithdraws` into the shared vault during `onStartEpoch`; keep that amount on hand, or transfer it to `IdleCreditVault`/a dedicated reserve before deploying surplus assets. Alternatively, use an isolated vault/liquidity facility for pending withdrawals so external ERC4626 withdrawals cannot consume settlement liquidity. [1](#0-0) 

For close-pool recalls, consider recalling external vault exposure progressively before epoch end or treating sustained external-vault illiquidity as an operational settlement state that does not conflate covered accounting with unavailable cash. [2](#0-1) 

### Proof of Concept

The existing regression already models the failure:

```solidity
uint256 pendingWithdraws = strategy.pendingWithdraws();
assertGe(
    limitedVault.convertToAssets(limitedVault.balanceOf(address(programmableBorrower))),
    pendingWithdraws
);
limitedVault.setWithdrawLimit(pendingWithdraws - 1);

vm.warp(cdoEpoch.epochEndDate() + 1);
vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
vm.prank(manager);
cdoEpoch.stopEpoch(0, 0);

assertFalse(cdoEpoch.defaulted());
assertTrue(cdoEpoch.isEpochRunning());
assertEq(strategy.pendingWithdraws(), pendingWithdraws);
```

This exact test asserts share coverage but insufficient external-vault liquidity, and shows that the epoch remains running while the full receipt amount stays pending. [12](#0-11) 

A mainnet-fork reproduction can reuse `TestProgrammableBorrowerCreditVault` with the real Gauntlet MetaMorpho vault configured at fork block `24850150`: deposit AA, create a normal withdrawal request, start the epoch so the programmable borrower parks assets in the vault, have an existing unprivileged vault LP redeem enough shares to leave less than `pendingWithdraws` withdrawable, warp past `epochEndDate`, and call `stopEpoch(0, 0)`. The expected result is `StopEpochVaultLiquidityUnavailable`, `defaulted == false`, `isEpochRunning == true`, and unchanged `pendingWithdraws`. [16](#0-15) [17](#0-16) [18](#0-17)

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L36-40)
```text
  IERC20Detailed public underlyingToken;
  /// @notice ERC4626 vault where idle funds are deployed
  IERC4626 public vault;
  /// @notice IdleCDOEpochVariant address allowed to pull funds
  address public idleCDO;
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L153-168)
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
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L201-216)
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L349-355)
```text
  /// @notice current borrowable liquidity excluding reserved withdraw requests
  function availableToBorrow() public view returns (uint256) {
    uint256 totalAssets = underlyingToken.balanceOf(address(this)) + _currentVaultAssets();
    // Interest is minted (not pulled as cash), so only pending withdraw requests need reservation.
    uint256 reserved = epochPendingWithdraws;
    return totalAssets <= reserved ? 0 : totalAssets - reserved;
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L435-461)
```text
  function _borrow(uint256 assets) internal returns (uint256 withdrawnShares) {
    if (!epochAccountingActive) revert NotAllowed();
    // `0` is treated as "draw the full currently borrowable amount" after reserving epoch-end obligations.
    uint256 borrowable = availableToBorrow();
    if (assets == 0) {
      if (borrowable == 0) revert InvalidAmount();
      assets = borrowable;
    }

    // Borrows are capped by the live "reserved vs free" view so epoch-end obligations always stay covered.
    if (assets > borrowable) revert InsufficientBorrowable();

    _accrueBorrowerInterest();

    uint256 onHand = underlyingToken.balanceOf(address(this));
    if (onHand < assets) {
      uint256 shortfall = assets - onHand;
      withdrawnShares = vault.withdraw(shortfall, address(this), address(this));
      if (epochAccountingActive) {
        epochWithdrawnFromVault += shortfall;
      }
      emit WithdrawnFromVault(shortfall, withdrawnShares, address(this));
    }

    // Executors can trigger the draw, but proceeds are always delivered to the borrower wallet.
    borrowerPrincipal += assets;
    underlyingToken.safeTransfer(borrower, assets);
```

**File:** contracts/IdleCDOEpochVariant.sol (L270-298)
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
```

**File:** contracts/IdleCDOEpochVariant.sol (L338-350)
```text
    _checkNotAllowed(
      // Check that epoch is running
      !isEpochRunning || 
      // Check that end date is passed
      block.timestamp < epochEndDate || 
      // Check that there are no pending instant withdraws, ie `getInstantWithdrawFunds` was called
      // before closing the epoch
      _pendingInstant() != 0 ||
      // Check that overridden interest, if passed (ie > 1), is greater than pending withdraw fees and the apr is 0 
      // otherwise withdrawal requests may not be fullfilled as they consider also the interest gained in the next epoch 
      (_interest > 1 && (_interest < _pendingWithdrawFees || _newApr != 0)) ||
      // Closing already recalls all principal, so applying a separate loss burn would strand returned cash.
      (_isRequestingAllFunds && _lossAmount != 0)
```

**File:** contracts/IdleCDOEpochVariant.sol (L366-376)
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
```

**File:** contracts/IdleCDOEpochVariant.sol (L395-410)
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

    // accrue interest to idleCDO, this will increase tranche prices.
    // Send also tot withdraw requests amount to the IdleCreditVault contract
    try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
      // transfer in strategy and decrease pendingWithdraws
        _strategy.collectWithdrawFunds(_pendingWithdraws);
```

**File:** contracts/IdleCDOEpochVariant.sol (L772-790)
```text
    uint256 principal = _underlyings;
    (uint256 interest, int256 diff) = _calcInterestWithdrawRequest(_underlyings, _tranche);
    uint256 totalFees = _totalWithdrawFees(principal, interest);
    // user is requesting principal + interest minus upfront management fee and net performance fee
    _underlyings = principal + interest - totalFees;
    // add expected fees to pending withdraw fees counter
    pendingWithdrawFees += totalFees;

    /// if there is an AA withdrawal the overperformance that the amount withdrawed would have generated for BB tranches
    /// is saved in interestForOverUnderPerformance. This is used to calculate the interest that should be added to the
    /// expectedEpochInterest at the startEpoch.
    /// If there is a BB withdrawal this amount is subtracted from the expectedEpochInterest
    interestForOverUnderPerformance += diff;

    // The receipt is fixed now and leaves live NAV. Charge management fees upfront
    // for the time it waits outside live NAV: remaining buffer plus the next epoch.
    creditVault.requestWithdraw(_underlyings, msg.sender, principal);
    // burn tranche tokens and decrease NAV without interest for the next epoch as it was not yet counted in NAV
    _withdrawOps(_amount, principal, _tranche);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L272-280)
```text
    // burn strategy tokens from cdo (we don't burn future interest here, only the principal)
    _burn(msg.sender, _principal);
    // mint equal amount of strategy tokens to the user as receipt (interest included), useful in case of default
    _mint(_user, _amount);
    // A successfully closed pool already recalled all funds and has no later stopEpoch.
    if (!isClosed) {
      // Global amount that stopEpoch must source from borrower/strategy for all pending receipts.
      pendingWithdraws += _amount;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-429)
```text
  function collectWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    uint256 pendingBasis = pendingWithdraws;
    if (_amount < pendingBasis) {
      // Legacy receipts do not have per-epoch ownership data, so they can only be fully funded.
      if (!defaultRecoveryInitialized) revert NotAllowed();
      uint256 lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis;
      // Avoid storing a zero price, which is indistinguishable from "no loss-adjusted epoch".
      if (lossRecoveryPrice == 0) revert NotAllowed();
      pendingWithdraws = 0;
      lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;
    } else {
      // A plain implementation upgrade may leave legacy normal receipts pending. Their next
      // successful stop can fully fund the aggregate before lazy initialization occurs.
      pendingWithdraws = pendingBasis - _amount;
    }
    if (_amount != 0) {
      underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
    }
```

**File:** test/foundry/ProgrammableBorrowerCreditVault.t.sol (L89-97)
```text
  address internal constant TL_MULTISIG = address(0xFb3bD022D5DAcF95eE28a6B07825D4Ff9C5b3814);
  address internal constant USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;
  address internal constant MORPHO_BLUE = 0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb;
  address internal constant STEAKHOUSE_USDC = 0xBEEF01735c132Ada46AA9aA4c54623cAA92A64CB;
  address internal constant GAUNTLET_USDC_PRIME = 0x8c106EEDAd96553e64287A5A6839c3Cc78afA3D0;
  address internal constant MORPHO_AAVE_USDC = 0xA5269A8e31B93Ff27B887B56720A25F844db0529;
  uint256 internal constant FORK_BLOCK = 19225935;
  uint256 internal constant GAUNTLET_FORK_BLOCK = 24850150;
  string internal constant BORROWER_NAME = "testBorrower";
```

**File:** test/foundry/ProgrammableBorrowerCreditVault.t.sol (L124-184)
```text
  function _setUpProgrammableBorrowerCreditVault(uint256 forkBlock, address vaultAddress) internal {
    vm.createSelectFork("mainnet", forkBlock);

    strategy = new IdleCreditVault();
    stdstore.target(address(strategy)).sig(strategy.token.selector).checked_write(address(0));
    strategy.initialize(USDC, owner, manager, placeholderBorrower, BORROWER_NAME, initialProvidedApr);

    cdoEpoch = new IdleCDOEpochVariant();
    stdstore.target(address(cdoEpoch)).sig(cdoEpoch.token.selector).checked_write(address(0));
    cdoEpoch.initialize(0, USDC, address(this), owner, rebalancer, address(strategy), 100000);
    idleCDO = IdleCDO(address(cdoEpoch));

    underlying = IERC20Detailed(USDC);
    aaTranche = IdleCDOTranche(idleCDO.AATranche());
    bbTranche = IdleCDOTranche(idleCDO.BBTranche());
    oneScale = 10 ** underlying.decimals();

    vm.prank(owner);
    strategy.setWhitelistedCDO(address(cdoEpoch));
    vm.prank(owner);
    strategy.setMaxApr(0);

    vm.startPrank(owner);
    cdoEpoch.setIsAYSActive(false);
    cdoEpoch.setFeeParams(TL_MULTISIG, 0, 100000, 0);
    cdoEpoch.setInstantWithdrawParams(3 days, 1.5e18, false);
    cdoEpoch.setEpochParams(36.5 days, 5 days);
    cdoEpoch.setKeyringParams(address(0), 0);
    vm.stopPrank();

    deal(USDC, address(this), 1_000_000 * oneScale, true);
    underlying.approve(address(cdoEpoch), type(uint256).max);

    ProgrammableBorrower programmableBorrowerImplementation = new ProgrammableBorrower();
    programmableBorrower = ProgrammableBorrower(address(new TransparentUpgradeableProxy(
      address(programmableBorrowerImplementation),
      makeAddr("programmableBorrowerProxyAdmin"),
      abi.encodeWithSelector(
        ProgrammableBorrower.initialize.selector,
        vaultAddress,
        address(cdoEpoch),
        address(this),
        manager,
        revolvingBorrower,
        365e18
      )
    )));
    morphoVault = IMMVault(vaultAddress);

    vm.prank(owner);
    strategy.setBorrower(address(programmableBorrower));

    vm.prank(owner);
    cdoEpoch.setIsProgrammableBorrower(true);

    vm.prank(manager);
    strategy.setAprs(0, 0);

    vm.prank(revolvingBorrower);
    underlying.approve(address(programmableBorrower), type(uint256).max);
  }
```

**File:** test/foundry/ProgrammableBorrowerCreditVault.t.sol (L344-369)
```text
  function testProgrammableBorrowerStopEpochDoesNotTrustBrokenMaxWithdraw() external {
    _setUpProgrammableBorrowerCreditVault(GAUNTLET_FORK_BLOCK, GAUNTLET_USDC_PRIME);

    IERC4626 gauntletVault = IERC4626(GAUNTLET_USDC_PRIME);
    uint256 amount = 10_000 * oneScale;

    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);

    idleCDO.depositAA(amount);

    uint256 withdrawReceipt = cdoEpoch.requestWithdraw(aaTranche.balanceOf(address(this)) / 2, address(aaTranche));
    assertGt(withdrawReceipt, 0, "expected a pending withdraw receipt");

    _startEpochAndCheckPrices(0);

    assertGt(gauntletVault.balanceOf(address(programmableBorrower)), 0, "idle funds not parked in gauntlet vault");
    assertEq(gauntletVault.maxWithdraw(address(programmableBorrower)), 0, "regression prerequisite changed");
    assertGt(strategy.pendingWithdraws(), 0, "stop epoch should need liquidity recall");

    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    assertFalse(cdoEpoch.defaulted(), "stop epoch should not default just because maxWithdraw is zero");
    assertEq(strategy.pendingWithdraws(), 0, "pending withdraws should be funded");
```

**File:** test/foundry/ProgrammableBorrowerCreditVault.t.sol (L372-399)
```text
  function testProgrammableBorrowerStopEpochRevertsWhenVaultLiquidityUnavailable() external {
    MockStopEpochLiquidityVault limitedVault = new MockStopEpochLiquidityVault(USDC);
    uint256 amount = 10_000 * oneScale;

    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);
    vm.prank(manager);
    programmableBorrower.setVault(address(limitedVault));

    idleCDO.depositAA(amount);
    cdoEpoch.requestWithdraw(aaTranche.balanceOf(address(this)) / 2, address(aaTranche));
    _startEpochAndCheckPrices(0);

    uint256 pendingWithdraws = strategy.pendingWithdraws();
    assertGt(pendingWithdraws, 1, "stop epoch should need liquidity recall");
    assertGe(limitedVault.convertToAssets(limitedVault.balanceOf(address(programmableBorrower))), pendingWithdraws);
    limitedVault.setWithdrawLimit(pendingWithdraws - 1);

    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.expectRevert(abi.encodeWithSelector(StopEpochVaultLiquidityUnavailable.selector));
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    assertFalse(cdoEpoch.defaulted(), "vault liquidity failure should not default");
    assertTrue(cdoEpoch.isEpochRunning(), "epoch should remain running after retryable failure");
    assertTrue(programmableBorrower.epochAccountingActive(), "borrower accounting should remain active");
    assertEq(strategy.pendingWithdraws(), pendingWithdraws, "withdraw requests should remain pending");
  }
```
