### Title
APR0 withdrawal interest is not reserved, allowing a forced programmable-borrower default - (`contracts/strategies/idle/ProgrammableBorrower.sol`)

### Summary
`ProgrammableBorrower` reserves only the initial `pendingWithdraws` principal when an epoch starts, but APR0 withdrawal claims can later grow by the borrower-funded interest allocated in `prepareStopEpochWithApr0` [1](#0-0) [2](#0-1) .

A KYC-passing lender can request an APR0 withdrawal during the buffer, after which the honest borrower may legitimately draw every asset except the stale principal reserve [3](#0-2) [4](#0-3) .

At epoch stop, the increased `pendingWithdraws` amount becomes a cash liability and can exceed the reserved vault liquidity, causing `getFundsFromBorrower` to fail and `_handleBorrowerDefault` to freeze the pool [5](#0-4) [6](#0-5) .

### Finding Description
An APR0 withdrawal request stores `_amount` in `pendingWithdraws` and `apr0TotalPrincipal`, while minting the user a receipt for that amount [7](#0-6) .

When the next epoch starts, `IdleCDOEpochVariant` passes the current `pendingWithdraws` value to `ProgrammableBorrower.onStartEpoch` [8](#0-7) .

`onStartEpoch` snapshots that value in `epochPendingWithdraws`, and `availableToBorrow` subtracts only that snapshot from the borrower’s total assets [1](#0-0) [9](#0-8) .

During `stopEpoch`, however, `prepareStopEpochWithApr0` calculates the APR0 requesters’ pro-rata share of realized pool interest and adds it to `pendingWithdraws` [10](#0-9) .

`IdleCDOEpochVariant` rereads the increased `pendingWithdraws` and demands that updated amount through `onStopEpoch` and `getFundsFromBorrower` [5](#0-4) [11](#0-10) .

The borrower-side reserve is still based on the pre-interest principal, so a permitted full borrow leaves less than the matured withdrawal liability [4](#0-3) .

This is the same stale-length class as the external report: the initial liability is calculated once, the payable amount is later increased, and the downstream operation still relies on the stale reserved amount.

### Impact Explanation
A lender can permanently freeze the credit pool by creating an APR0 withdrawal whose final cash obligation is not fully reserved [12](#0-11) .

When `stopEpoch` fails to pull the matured `pendingWithdraws`, `_handleBorrowerDefault` sets `defaulted`, pauses deposits, stops the epoch, and disables new withdrawal requests [12](#0-11) .

The loss equals the unreserved APR0 interest component `apr0Interest - availableVaultYieldOnReservedPrincipal`, and all remaining active NAV and withdrawal claims become dependent on default-recovery finalization rather than normal epoch settlement [2](#0-1) [13](#0-12) .

### Likelihood Explanation
The attacker needs only a KYC-allowed wallet, a funded tranche position, and a call to `requestWithdraw` while the vault APR is zero [14](#0-13) .

The borrower does not need to be malicious or colluding because `borrow(0)` is an authorized operation and naturally draws the maximum amount reported by `availableToBorrow` [15](#0-14) [4](#0-3) .

The issue is most reliable when borrower APR materially exceeds the ERC4626 yield earned on the principal left in reserve, because the vault gain then cannot cover the newly assigned APR0 interest [16](#0-15) .

Existing guards do not stop the sequence because the request is allowed before the epoch starts, the borrow amount is within the stale reserve accounting, and the default path treats the later transfer failure as a borrower default [17](#0-16) [18](#0-17) .

### Recommendation
Reserve the maximum matured APR0 claim, not merely its request-time principal.

`ProgrammableBorrower.onStartEpoch` should receive a conservative upper bound for `pendingWithdraws + projectedApr0Interest`, or `prepareStopEpochWithApr0` should be moved before the reservation boundary so the borrower adapter receives the final liability.

If exact projection is impossible because realized vault and borrower interest are only known at stop, restrict `borrow` by a configurable reserve buffer or prevent borrowers from drawing against the portion of principal that secures APR0 withdrawal interest.

Add a regression test that creates an APR0 request, starts the epoch, calls `borrow(0)`, accrues meaningful borrower interest, and verifies that `stopEpoch` can fund the increased `pendingWithdraws` without entering `defaulted`.

### Proof of Concept
The following Foundry test follows the programmable-borrower mainnet setup used by `test/foundry/ProgrammableBorrowerCreditVault.t.sol`, where the strategy is initialized at APR0 and the programmable borrower is configured as the strategy borrower [19](#0-18) .

```solidity
function testApr0PendingInterestExceedsEpochReserve() external {
    uint256 attackerAmount = 100_000 * oneScale;
    uint256 victimAmount = 900_000 * oneScale;
    address attacker = makeAddr("apr0-attacker");
    address victim = makeAddr("ordinary-lp");

    deal(USDC, attacker, attackerAmount, true);
    deal(USDC, victim, victimAmount, true);

    vm.prank(attacker);
    underlying.approve(address(cdoEpoch), type(uint256).max);
    vm.prank(victim);
    underlying.approve(address(cdoEpoch), type(uint256).max);

    vm.prank(victim);
    idleCDO.depositAA(victimAmount);

    vm.prank(attacker);
    idleCDO.depositAA(attackerAmount);

    // APR is zero, so this creates the APR0 principal bucket.
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(aaTranche));

    uint256 principalReserve = strategy.pendingWithdraws();
    assertEq(principalReserve, attackerAmount);

    // Only principal is communicated to and reserved by ProgrammableBorrower.
    vm.prank(manager);
    cdoEpoch.startEpoch();
    assertEq(programmableBorrower.epochPendingWithdraws(), principalReserve);

    // The honest borrower uses the facility normally and draws every non-reserved asset.
    vm.prank(revolvingBorrower);
    programmableBorrower.borrow(0);

    // Borrower contractual interest accrues at borrowerApr while only the
    // stale principal reserve remains in the vault sleeve.
    vm.warp(cdoEpoch.epochEndDate() + 1);
    _accrueMorphoVaultInterest();

    // prepareStopEpochWithApr0 adds the attacker's pro-rata interest to
    // pendingWithdraws, but epochPendingWithdraws was not increased.
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    // The pull exceeds the reserved vault liquidity and enters hard default.
    assertTrue(cdoEpoch.defaulted());
    assertTrue(cdoEpoch.paused());
    assertFalse(cdoEpoch.isEpochRunning());
    assertFalse(cdoEpoch.allowAAWithdrawRequest());
    assertFalse(cdoEpoch.allowBBWithdrawRequest());
}
```

The decisive assertion is that `epochPendingWithdraws` remains `principalReserve`, while `pendingWithdraws` becomes `principalReserve + apr0Interest` immediately before `IdleCDOEpochVariant` pulls funds [1](#0-0) [2](#0-1) .

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L200-205)
```text
  /// @param _pendingWithdraws pending withdraw requests amount to reserve for epoch end.
  function onStartEpoch(uint256 _pendingWithdraws) external nonReentrant {
    _checkOnlyIdleCDO();
    _accrueBorrowerInterest();
    // Reserve the amount IdleCDO expects to pull back at stopEpoch before the real borrower can draw again.
    epochPendingWithdraws = _pendingWithdraws;
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L325-334)
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L392-404)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L272-287)
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
    // save the epoch of the last withdraw request (buffer + epochDuration is 1 epoch)
    lastWithdrawRequest[_user] = currentEpoch;
    // APR=0 requests keep separate accounting and settle interest at stopEpoch.
    // `_amount` here is the post-management-fee principal bucket for that flow.
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
    } else {
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L510-540)
```text
    uint256 _apr0NetInterest;
    // APR0 allocation is computed only when stopEpoch receives a real override interest.
    // _expectedInterest == 1 is the "request all funds back" sentinel and is handled in IdleCDO.
    if (_expInterest > 1 && _expInterest > _pendingFees) {
      // Remove already booked withdraw fees from the interest base before splitting.
      uint256 _interestNetOfFees = _expInterest - _pendingFees;
      // Total principal used for the pro-rata split:
      // IdleCDO TVL (which excludes APR0 requested principal) + APR0 principal bucket.
      uint256 _totalPrincipalForSplit = _tvl + _principal;
      if (_totalPrincipalForSplit != 0) {
        // APR0 users get a pro-rata share of realized interest.
        uint256 _apr0InterestGross = _interestNetOfFees * _principal / _totalPrincipalForSplit;
        if (_apr0InterestGross != 0) {
          // Same fee model as normal withdraw interest.
          uint256 _apr0Fee = _apr0InterestGross * _cdo.fee() / FULL_ALLOC;
          _apr0NetInterest = _apr0InterestGross - _apr0Fee;
          _adjPendingWithdrawFees += _apr0Fee;
          _expInterest -= _apr0NetInterest;
        }
      }
    }

    // Finalize one-epoch APR0 interest for current epoch only.
    if (_apr0NetInterest != 0) {
      // Funds owed to withdraw requesters increase by APR0 net interest.
      pendingWithdraws += _apr0NetInterest;
      // Save per-epoch net rate; each APR0 request accrues exactly once on its request epoch.
      apr0RateByEpoch[epochNumber] = (_apr0NetInterest * 1e18) / _principal;
    }
    // Close current APR0 bucket so it cannot accrue again on later stopEpoch calls.
    apr0TotalPrincipal = 0;
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

**File:** contracts/IdleCDOEpochVariant.sol (L239-250)
```text
    _checkNotAllowed(defaulted || block.timestamp < (epochEndDate + bufferPeriod) || _epochDuration == 0);
    _checkProgrammableBorrowerMode();
    // Remove raw donated underlyings before calculating epoch interest or borrower transfer amounts.
    _skimDonatedAssets();

    isEpochRunning = true;
    // prevent deposits
    _pause();

    // prevent withdrawals requests
    allowAAWithdrawRequest = false;
    allowBBWithdrawRequest = false;
```

**File:** contracts/IdleCDOEpochVariant.sol (L279-297)
```text
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
```

**File:** contracts/IdleCDOEpochVariant.sol (L361-364)
```text
    // Strategy finalizes APR0 bucket state and returns adjusted stopEpoch values.
    (_expectedInterest, _pendingWithdrawFees) = _strategy.prepareStopEpochWithApr0(_interest);
    // Pending withdraws may be increased during APR0 settlement, so read after prepareStopEpochWithApr0.
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();
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

**File:** contracts/IdleCDOEpochVariant.sol (L501-505)
```text
    } catch {
      // if borrower defaults, prev instant withdraw requests can still be withdrawn
      // as were already fullfilled prior to the default (all funds already sent to the strategy)
      _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
    }
```

**File:** contracts/IdleCDOEpochVariant.sol (L576-598)
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
```

**File:** contracts/IdleCDOEpochVariant.sol (L739-790)
```text
  function requestWithdraw(uint256 _amount, address _tranche) external returns (uint256 _underlyings) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == AATranche ? !allowAAWithdrawRequest : !allowBBWithdrawRequest) ||
      !isWalletAllowed(msg.sender)
    );
  
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();

    // we trigger an update accounting to check for eventual losses
    _updateAccounting();

    IdleCreditVault creditVault = IdleCreditVault(strategy);
    if (_amount == 0) {
      _amount = _userTrancheBal(msg.sender, _tranche);
    }
    _underlyings = _trancheToUnderlyings(_amount, _tranche);

    // Programmable borrower deployments do not support instant withdrawals.
    // If apr decresed wrt last epoch, request instant withdraw and burn tranche tokens directly
    // we compare unscaled aprs
    if (_isInstantWithdrawEnabled()) {
      uint256 currentApr = creditVault.unscaledApr();
      if (lastEpochApr > (currentApr + instantWithdrawAprDelta)) {
        // burn strategy tokens from cdo and mint an equal amount to msg.sender as receipt
        creditVault.requestInstantWithdraw(_underlyings, msg.sender);
        // burn tranche tokens and decrease NAV
        _withdrawOps(_amount, _underlyings, _tranche);
        return _underlyings;
      }
    }

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

**File:** test/foundry/ProgrammableBorrowerCreditVault.t.sol (L120-180)
```text
  function setUp() public {
    _setUpProgrammableBorrowerCreditVault(FORK_BLOCK, STEAKHOUSE_USDC);
  }

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
```
