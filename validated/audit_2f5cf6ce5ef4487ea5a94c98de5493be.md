### Title
Buffer-period deposits mint tranches at a stale NAV that excludes accrued programmable-borrower interest - (contracts/IdleCDOCreditVault.sol)

### Summary
In programmable-borrower mode, borrower interest continues accruing during the buffer after `stopEpoch`, but `getContractValue()` only counts strategy tokens and raw underlying held by the CDO, so deposits are priced without that receivable. [1](#0-0) [2](#0-1) 

### Finding Description
After a successful `stopEpoch`, the CDO unpauses and reopens normal deposits and withdrawal requests for the buffer period. [3](#0-2)  If borrower principal remains outstanding, `ProgrammableBorrower.borrowerInterestAccruedNow()` continues adding time-based interest even while epoch accounting is inactive. [2](#0-1) 

A KYC-passing attacker can call `depositAA()` during that buffer. [4](#0-3)  `_deposit()` calls `_updateAccounting()` and then `_mintSharesAtCurrPrice()`, but both functions derive value only from the CDO's strategy-token balance, underlying balance, and `unclaimedFees`. [5](#0-4) [1](#0-0) 

The already-accrued borrower-interest receivable is not represented in either saved NAV or `getContractValue()` at that point. [6](#0-5)  On the following epoch start, `onStartEpoch()` checkpoints that buffer-period accrual into `borrowerInterestAccrued`, and a later `totalInterestDueNow()` returns it as pool-facing epoch interest. [7](#0-6) [8](#0-7) 

Therefore, the attacker receives shares based on NAV `N`, while the economically correct pre-deposit NAV is `N + bufferInterest`; when the next stop mints the accrued interest, those excess shares receive part of yield that accrued entirely before the deposit. [9](#0-8) [10](#0-9) 

### Impact Explanation
This is direct dilution and theft of unclaimed yield from existing tranche holders. With approximately `10,000 USDC` AA NAV, `4,000 USDC` outstanding borrower principal, and a `365%` borrower APR, five buffer days accrue roughly `200 USDC`; depositing another `10,000 USDC` gives the attacker almost half of the tranche supply and therefore nearly `100 USDC` of yield that accrued before their deposit. [11](#0-10) 

The loss scales with outstanding borrower principal, borrower APR, buffer duration, and attacker deposit size, and it repeats whenever unpaid borrower interest accrues between the prior stop and the next epoch. [12](#0-11) [8](#0-7) 

### Likelihood Explanation
The attack only requires a wallet allowed by `isWalletAllowed()` and does not require the borrower, manager, owner, or another privileged role to misbehave. [13](#0-12) [14](#0-13) 

The vulnerable window exists in every programmable-borrower buffer in which borrower principal remains outstanding and borrower interest continues accruing. [15](#0-14)  Existing donation skimming does not stop it because the missing asset is contractual borrower interest, not raw underlying held by the CDO. [16](#0-15) 

### Recommendation
For programmable-borrower deployments, include the live uncredited borrower receivable in deposit pricing, or prevent ordinary deposits while unpaid borrower interest is accumulating outside the strategy-token NAV. The accounting adjustment should add `IProgrammableBorrower(_borrower()).borrowerInterestAccruedNow()` to the NAV used by `_virtualPriceAux()` and `_mintSharesAtCurrPrice()`, while avoiding double-counting `borrowerInterestDebt` that was already minted and settled at a previous `stopEpoch`. [6](#0-5) [17](#0-16) 

### Proof of Concept
Add this test to `test/foundry/ProgrammableBorrowerCreditVault.t.sol`:

```solidity
function testBufferDepositDilutesUncreditedBorrowerInterest() external {
  uint256 amount = 10_000 * oneScale;
  uint256 drawAmount = 4_000 * oneScale;
  address attacker = makeAddr("bufferDepositor");

  vm.prank(owner);
  cdoEpoch.setIsInterestMinted(true);

  idleCDO.depositAA(amount);
  _startEpochAndCheckPrices(0);

  vm.prank(revolvingBorrower);
  programmableBorrower.borrow(drawAmount);

  vm.warp(cdoEpoch.epochEndDate() + 1);
  vm.prank(manager);
  cdoEpoch.stopEpoch(0, 0);

  // Outstanding borrower principal continues accruing during the buffer.
  vm.warp(cdoEpoch.epochEndDate() + cdoEpoch.bufferPeriod());
  uint256 bufferInterest = programmableBorrower.borrowerInterestAccruedNow();
  assertGt(bufferInterest, 0, "expected uncredited buffer interest");

  uint256 staleNav = cdoEpoch.lastNAVAA();
  uint256 supplyBefore = aaTranche.totalSupply();
  uint256 depositAmount = 10_000 * oneScale;

  deal(USDC, attacker, depositAmount, true);
  vm.prank(attacker);
  underlying.approve(address(cdoEpoch), type(uint256).max);

  // Fair minting must price the already-accrued borrower receivable.
  uint256 fairShares = depositAmount * supplyBefore / (staleNav + bufferInterest);

  vm.prank(attacker);
  uint256 actualShares = idleCDO.depositAA(depositAmount);
  assertGt(actualShares, fairShares, "deposit ignored accrued borrower interest");

  // Stop future borrower APR so the measured dilution is attributable to the
  // pre-deposit buffer accrual; vault yield only makes the final delta larger.
  vm.prank(manager);
  programmableBorrower.setBorrowerApr(0);

  vm.prank(manager);
  cdoEpoch.startEpoch();
  vm.warp(cdoEpoch.epochEndDate() + 1);
  vm.prank(manager);
  cdoEpoch.stopEpoch(0, 0);

  uint256 finalPrice = cdoEpoch.virtualPrice(address(aaTranche));
  uint256 excessValue = (actualShares - fairShares) * finalPrice / ONE_TRANCHE_TOKEN;

  // With the sample values, the depositor obtains roughly half of ~200 USDC
  // of interest that accrued before the deposit.
  assertGt(excessValue, bufferInterest / 10, "pre-deposit yield was not diluted");
}
```

### Citations

**File:** contracts/IdleCDOCreditVault.sol (L99-109)
```text
  function depositAA(uint256 _amount) external returns (uint256) {
    return _deposit(_amount, AATranche);
  }

  /// @notice pausable in _deposit
  /// @dev msg.sender should approve this contract first to spend `_amount` of `token`
  /// @param _amount amount of `token` to deposit
  /// @return BB tranche tokens minted
  function depositBB(uint256 _amount) external returns (uint256) {
    _checkNotAuthorized(!isBBDepositEnabled);
    return _deposit(_amount, BBTranche);
```

**File:** contracts/IdleCDOCreditVault.sol (L125-128)
```text
  function getContractValue() public override view returns (uint256) {
    // Credit vault strategy tokens are minted 1:1 with underlyings and use the same decimals.
    return _contractTokenBalance(strategyToken) + _contractTokenBalance(token) - unclaimedFees;
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L172-179)
```text
  function virtualPrice(address _tranche) public virtual view returns (uint256 _virtualPrice) {
    (_virtualPrice, ) = _virtualPriceAux(
      _tranche,
      _managedContractValue(), // nav
      lastNAVAA + lastNAVBB, // lastNAV
      _lastSavedNAV(_tranche), // lastTrancheNAV
      trancheAPRSplitRatio
    );
```

**File:** contracts/IdleCDOCreditVault.sol (L191-211)
```text
  function _deposit(uint256 _amount, address _tranche) internal virtual whenNotPaused returns (uint256 _minted) {
    if (_amount == 0) {
      return _minted;
    }
    // check that we are not depositing more than the contract available limit
    _guarded(_amount);
    // interest accrued since last depositXX/withdrawXX is splitted between AA and BB
    // according to trancheAPRSplitRatio. NAVs of AA and BB are updated and tranche
    // prices adjusted accordingly
    _updateAccounting();
    // get underlyings from sender
    address _token = token;
    uint256 _preBal = _contractTokenBalance(_token);
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);
    // mint tranche tokens according to the current tranche price
    _minted = _mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, msg.sender, _tranche);
    // update trancheAPRSplitRatio
    _updateSplitRatio(_getAARatio(true));

    // direct deposit in the strategy
    IIdleCDOStrategy(strategy).deposit(_amount);
```

**File:** contracts/IdleCDOCreditVault.sol (L344-347)
```text
  function _mintSharesAtCurrPrice(uint256 _amount, address _to, address _tranche) internal virtual returns (uint256 _minted) {
    // calculate # of tranche token to mint based on current tranche price: _amount / tranchePrice
    _minted = _amount * ONE_TRANCHE_TOKEN / _tranchePrice(_tranche);
    _mintShares(_tranche, _to, _minted, _amount);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L44-57)
```text
  /// @notice fixed APR charged to borrower debt (100e18 = 100% APR)
  uint256 public borrowerApr;
  /// @notice outstanding borrower principal
  uint256 public borrowerPrincipal;
  /// @notice borrower contractual interest accrued but not yet settled to IdleCDO
  /// @dev This tracks only the real borrower's credit-facility interest and is independent from
  /// gains or losses generated by the vault sleeve.
  uint256 public borrowerInterestAccrued;
  /// @notice borrower contractual interest already settled to IdleCDO but not yet repaid
  /// @dev This can be greater than the same epoch's net pool interest if the vault sleeve had a
  /// loss. Vault PnL stays in pool accounting; borrower debt stays purely contractual.
  uint256 public borrowerInterestDebt;
  /// @notice last timestamp borrower interest was accrued
  uint256 public lastBorrowerAccrual;
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L201-223)
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
    emit EpochAccountingStarted(startAssets);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L231-267)
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
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L271-282)
```text
  /// @dev Called by IdleCDO only on the success path of `stopEpoch`. If the CDO's `transferFrom`
  /// fails (default), this is never called and borrower interest stays in `borrowerInterestAccrued`.
  /// Borrower debt is intentionally settled at the full contractual borrower-interest amount,
  /// without netting vault gains or losses against it.
  function settleBorrowerInterest() external nonReentrant {
    _checkOnlyIdleCDO();
    uint256 settled = borrowerInterestAccrued;
    if (settled != 0) {
      borrowerInterestAccrued = 0;
      borrowerInterestDebt += settled;
    }
    emit BorrowerInterestSettled(settled);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L309-317)
```text
  /// @notice borrower interest accrued since epoch start up to now (includes uncheckpointed interest)
  function borrowerInterestAccruedNow() public view returns (uint256) {
    return borrowerInterestAccrued + _pendingBorrowerInterest(borrowerPrincipal, lastBorrowerAccrual);
  }

  /// @notice total borrower interest still owed by the real borrower
  function borrowerInterestOwedNow() public view returns (uint256) {
    return borrowerInterestDebt + borrowerInterestAccruedNow();
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L330-333)
```text
  function totalInterestDueNow() external view returns (uint256) {
    (uint256 vaultInterest, uint256 loss) = _vaultNetInterest();
    uint256 totalGain = vaultInterest + borrowerInterestAccruedNow() + bufferInterest;
    return totalGain > loss ? totalGain - loss : 0;
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L551-578)
```text
  /// @notice Compute uncheckpointed borrower interest since the last accrual timestamp.
  /// @dev `borrowerApr` is stored as a percentage scaled by `1e18`, so dividing by 100 converts
  /// it into a `1e18`-scaled rate fraction before prorating it over a year.
  function _pendingBorrowerInterest(uint256 principal, uint256 last) internal view returns (uint256) {
    if (principal == 0 || last == 0 || borrowerApr == 0) return 0;
    uint256 elapsed = block.timestamp - last;
    return elapsed == 0 ? 0 : _calcInterest(principal, elapsed);
  }

  /// @notice Compute simple time-based borrower interest for a principal over an elapsed period.
  function _calcInterest(uint256 principal, uint256 elapsed) internal view returns (uint256) {
    return principal * (borrowerApr / 100) * elapsed / (YEAR * ONE_TRANCHE_TOKEN);
  }

  /// @notice Checkpoint borrower interest up to `block.timestamp`.
  /// @dev The first call only seeds the accrual clock. When principal or APR is zero we still
  /// move the timestamp forward so interest starts accruing cleanly from the next state change.
  function _accrueBorrowerInterest() internal {
    uint256 last = lastBorrowerAccrual;
    uint256 principal = borrowerPrincipal;
    if (last == 0 || principal == 0 || borrowerApr == 0) {
      lastBorrowerAccrual = block.timestamp;
      return;
    }
    uint256 elapsed = block.timestamp - last;
    if (elapsed == 0) return;
    borrowerInterestAccrued += _calcInterest(principal, elapsed);
    lastBorrowerAccrual = block.timestamp;
```

**File:** contracts/IdleCDOEpochVariant.sol (L428-436)
```text
      if (_mintInterest) {
        // if interest is not transferred we mint strategy tokens equal to the full epoch interest
        if (_grossInterest != 0) _strategy.mintStrategyTokens(_grossInterest);
        // and increase unclaimedFees by pending withdraw fees before _updateAccounting
        unclaimedFees += _pendingWithdrawFees;
      }

      // update tranche prices and unclaimed fees
      _updateAccounting();
```

**File:** contracts/IdleCDOEpochVariant.sol (L473-483)
```text
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
```

**File:** contracts/IdleCDOEpochVariant.sol (L643-650)
```text
  /// @notice Deposit funds in the vault. Overrides the parent method and adds a check for wallet 
  function _deposit(uint256 _amount, address _tranche) internal override whenNotPaused returns (uint256) {
    _checkNotAllowed(!isWalletAllowed(msg.sender));
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();
    // do the inherited deposit flow
    return super._deposit(_amount, _tranche);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L981-987)
```text
  /// @notice Check if wallet is allowed to interact with the contract
  /// @param _user User address
  /// @return true if wallet is allowed or keyring address is not set
  function isWalletAllowed(address _user) public view returns (bool) {
    address _keyring = keyring;
    return _keyring == address(0) || IKeyring(_keyring).checkCredential(keyringPolicyId, _user);
  }
```
