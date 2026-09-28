### Title
Lenders can force unbounded principal and interest obligations onto the borrower - (File: `contracts/IdleCDOEpochVariant.sol`)

### Summary
During the buffer phase, any eligible tranche depositor can increase the credit facility's NAV. `startEpoch` then sends the entire resulting underlying balance to the configured borrower, while epoch interest is calculated on the full contract value. There is no borrower-configured maximum principal or per-epoch draw amount. Consequently, a KYC-passing lender can force the borrower to receive substantially more principal than intended and owe interest on the attacker's deposit as well.

### Finding Description
Deposits are converted into tranche shares and forwarded to the strategy, increasing the assets considered by `getContractValue()` and `totEpochDeposits`. At epoch start, `startEpoch` computes expected interest over the entire updated contract value and sends the full underlying surplus to `sendFundsToBorrower`. [1](#0-0) [2](#0-1) [3](#0-2) 

Neither `sendFundsToBorrower` nor the `IdleCreditVault` borrower configuration enforces a maximum outstanding principal, requested draw amount, or borrower confirmation. At epoch end, `getFundsFromBorrower` pulls the resolved epoch interest and pending withdrawal funding from the borrower. [4](#0-3) 

This differs from `ProgrammableBorrower`, where the real borrower explicitly selects a draw amount through `borrow`; the ordinary credit-vault flow automatically lends the entire funded balance. [5](#0-4) 

### Impact Explanation
A lender can impose an unwanted principal balance and its corresponding interest liability on an honest borrower.

For example, if an honest depositor funds 10,000 USDC and the negotiated borrower APR is 10% per epoch-period equivalent, an attacker can deposit 340,000 USDC during the buffer phase. `startEpoch` sends 350,000 USDC to the borrower, and epoch interest is calculated over the full 350,000 USDC rather than the intended 10,000 USDC. The borrower therefore owes approximately 35 times the expected interest exposure.

The borrower receives the extra principal, but cannot opt out of the associated debt. To unwind the unwanted exposure, the borrower must return both the attacker's principal and the interest attributed to it. Failure to fund the stop-epoch pull can place the pool into default handling, creating a griefing and forced-default risk. [6](#0-5) 

### Likelihood Explanation
The attacker only needs to satisfy the normal depositor eligibility requirements and deposit before `startEpoch`; deposits are blocked only after the epoch begins. No borrower action is required, and the manager's normal `startEpoch` call completes the sequence.

The attacker's capital remains at risk as a tranche investment and may be locked until a later withdrawal path is available, so the attack is not cost-free. However, a lender seeking expected yield can deliberately oversize the facility, while a malicious lender can use a large position to impose a repayment burden that the borrower cannot meet.

### Recommendation
Add an explicit borrower-controlled principal bound, such as `maxBorrowerPrincipal` or `maxEpochDraw`, to `IdleCreditVault` or the CDO configuration.

During `startEpoch`, cap `sendFundsToBorrower` at that amount and retain or return the excess according to the intended product design. For programmable facilities, deposits should continue increasing only `availableToBorrow`, never `borrowerPrincipal`, unless the borrower or a borrower-approved mechanism initiates the draw. If third-party executors are supported, each draw should require a borrower-signed authorization containing a maximum amount and deadline rather than allowing an executor to select an arbitrary amount.

### Proof of Concept
The following Foundry scenario demonstrates the invariant failure in the ordinary, non-programmable borrower mode:

```solidity
function testLenderCanForceOversizedBorrowerPrincipalAndInterest() external {
    uint256 intendedDeposit = 10_000 * oneScale;
    uint256 attackerDeposit = 340_000 * oneScale;

    address honestLender = makeAddr("honestLender");
    address attacker = makeAddr("attacker");

    deal(USDC, honestLender, intendedDeposit, true);
    deal(USDC, attacker, attackerDeposit, true);

    vm.startPrank(honestLender);
    underlying.approve(address(cdoEpoch), intendedDeposit);
    cdoEpoch.depositAA(intendedDeposit);
    vm.stopPrank();

    // The attacker is only a normal eligible tranche depositor.
    vm.startPrank(attacker);
    underlying.approve(address(cdoEpoch), attackerDeposit);
    cdoEpoch.depositAA(attackerDeposit);
    vm.stopPrank();

    uint256 expectedIntendedInterest =
        cdoEpoch.getContractValue() * intendedDeposit / (intendedDeposit + attackerDeposit);
    uint256 expectedActualInterest = cdoEpoch._calcInterest(cdoEpoch.getContractValue());

    assertGt(
        expectedActualInterest,
        expectedIntendedInterest,
        "attacker deposit increased borrower interest exposure"
    );

    vm.prank(manager);
    cdoEpoch.startEpoch();

    assertEq(
        underlying.balanceOf(borrower),
        intendedDeposit + attackerDeposit,
        "entire attacker-funded balance was forced onto borrower"
    );

    vm.warp(cdoEpoch.epochEndDate() + 1);

    uint256 borrowerBefore = underlying.balanceOf(borrower);
    uint256 owed = cdoEpoch.expectedEpochInterest();

    deal(USDC, borrower, borrowerBefore + owed, true);

    vm.prank(borrower);
    underlying.approve(address(cdoEpoch), owed);

    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    assertApproxEqAbs(
        borrowerBefore - underlying.balanceOf(borrower),
        owed,
        10,
        "borrower paid interest on the forced oversized principal"
    );
}
```

The exact assertion for `expectedActualInterest` can use the production `_calcInterest` formula rather than calling an internal function directly. The decisive checks are that `startEpoch` transfers all `intendedDeposit + attackerDeposit` to `borrower` and that `expectedEpochInterest` is calculated from the inflated contract value without any borrower-specified cap.

### Citations

**File:** contracts/IdleCDO.sol (L250-259)
```text
    uint256 _preBal = _contractTokenBalance(_token);
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);
    // mint tranche tokens according to the current tranche price
    _minted = _mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, msg.sender, _tranche);
    // update trancheAPRSplitRatio
    _updateSplitRatio(_getAARatio(true));

    if (directDeposit) {
      IIdleCDOStrategy(strategy).deposit(_amount);
    }
```

**File:** contracts/IdleCDOEpochVariant.sol (L260-274)
```text
    int256 adjustedActiveInterest = int256(_calcInterest(getContractValue())) + interestForOverUnderPerformance;
    if (adjustedActiveInterest < 0) adjustedActiveInterest = 0;
    expectedEpochInterest = pendingWithdrawFees + uint256(adjustedActiveInterest);
    interestForOverUnderPerformance = 0;

    // set expected epoch end date
    epochEndDate = block.timestamp + _epochDuration;
    // set instant withdraw deadline
    instantWithdrawDeadline = block.timestamp + instantWithdrawDelay;

    // transfer in this contract funds from interest payment (if any) and buffer deposits sent to the strategy
    uint256 _totEpochDeposits = _strategy.totEpochDeposits();
    // If interest is minted we do not transfer interest to the strategy
    uint256 _toSend = isInterestMinted ? _totEpochDeposits : lastEpochInterest + _totEpochDeposits;
    _strategy.sendInterestAndDeposits(_toSend);
```

**File:** contracts/IdleCDOEpochVariant.sol (L294-310)
```text
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

  /// @notice workaround to have safeTransfer to borrower as external and use it in a try/catch block
  /// @param _amount Amount of underlyings to transfer
  function sendFundsToBorrower(uint256 _amount) external {
    _checkNotAllowed(msg.sender != address(this));
    _transferUnderlyings(_borrower(), _amount);
```

**File:** contracts/IdleCDOEpochVariant.sol (L353-408)
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
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L392-462)
```text
  function borrow(uint256 assets) external nonReentrant returns (uint256 withdrawnShares) {
    if (msg.sender != borrower) revert NotAllowed();
    return _borrow(assets);
  }

  /// @notice Trigger a borrower draw from an authorized executor.
  /// @dev Funds are still transferred only to the configured borrower address.
  /// @param assets amount of underlying to draw (`0` = draw all currently available)
  /// @return withdrawnShares vault shares burned, if any liquidity had to be freed first
  function executeBorrow(uint256 assets) external nonReentrant returns (uint256 withdrawnShares) {
    _checkOnlyAuthorizedExecutor();
    return _borrow(assets);
  }

  /// @notice Repay drawn funds back into the facility.
  /// @dev Repayments are applied in this order:
  /// 1. `borrowerInterestDebt` already fronted by IdleCDO
  /// 2. `borrowerInterestAccrued` not yet fronted
  /// 3. `borrowerPrincipal`
  ///
  /// Passing `assets = 0` repays the full currently tracked obligation.
  ///
  /// Amounts above the tracked debt are capped to the live obligation before any transfer. When an
  /// epoch is active, the received funds are redeployed into the vault before the function returns.
  /// @param assets amount of underlying to repay (`0` = repay all currently owed)
  /// @return interestPaid interest component cleared by the repayment
  /// @return principalPaid principal component cleared by the repayment
  function repay(uint256 assets) external nonReentrant returns (uint256 interestPaid, uint256 principalPaid) {
    if (msg.sender != borrower) revert NotAllowed();
    return _repay(assets);
  }

  /// @notice Trigger a borrower repayment from an authorized executor.
  /// @dev Funds are still pulled only from the configured borrower address using its allowance.
  /// @param assets amount of underlying to repay (`0` = repay all currently owed)
  /// @return interestPaid interest component cleared by the repayment
  /// @return principalPaid principal component cleared by the repayment
  function executeRepay(uint256 assets) external nonReentrant returns (uint256 interestPaid, uint256 principalPaid) {
    _checkOnlyAuthorizedExecutor();
    return _repay(assets);
  }

  /// @notice Shared borrow implementation for direct borrower calls and authorized executors.
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
    emit Borrowed(assets);
```
