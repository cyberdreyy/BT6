### Title
Unvalidated ERC4626 deposit enables vault-share inflation theft - ([File: `contracts/strategies/idle/ProgrammableBorrower.sol`])

### Summary
`ProgrammableBorrower.onStartEpoch` deposits all idle pool assets into a configurable ERC4626 vault without specifying a minimum acceptable share amount or checking that the resulting share position still represents the deposited principal. [1](#0-0) [2](#0-1)  An unprivileged ERC4626 vault user can front-run an honest epoch-start transaction with the classic first-depositor/donation sequence, cause the programmable borrower to receive few or zero shares, and then redeem the inflated shares for the pool's assets. [3](#0-2)  The resulting principal loss is only netted against epoch interest by `totalInterestDueNow`, so an interest-minted stop can succeed while the CDO continues valuing already-stolen principal at par. [4](#0-3) [5](#0-4) 

### Finding Description
During the buffer phase, the programmable borrower can hold idle underlying but no vault shares. [6](#0-5)  An attacker deposits a dust amount into the configured ERC4626 vault, directly donates underlying to inflate its assets-per-share quote, and then sequences around the manager's honest `startEpoch` call. [7](#0-6) 

`IdleCDOEpochVariant.startEpoch` transfers surplus pool underlying to the borrower and then invokes the programmable-borrower epoch hook. [8](#0-7)  `onStartEpoch` computes a principal baseline before redepositing all idle cash, but `_depositToVault` accepts whatever number of shares `vault.deposit` returns. [9](#0-8) [2](#0-1) 

After the deposit, the attacker redeems their vault shares and receives both their donation and most of the pool deposit, while `ProgrammableBorrower`'s remaining shares convert to little or no underlying. [3](#0-2)  `_vaultNetInterest` correctly sees the large net delta as a loss, but `totalInterestDueNow` floors the result at zero rather than surfacing principal impairment to the CDO. [10](#0-9) 

In minted-interest mode with no pending withdrawals, `_amountToPullFromBorrower` is zero, so `onStopEpoch(0, false)` succeeds and the CDO mints no interest; the stale strategy-token balance continues to represent full principal even though the external vault position was drained. [11](#0-10) [12](#0-11)  The theft therefore produces delayed insolvency rather than an immediate revert, and withdrawal funding or a later liquidity recall eventually exposes the missing principal. [13](#0-12) 

### Impact Explanation
The attacker directly steals the pool assets that `ProgrammableBorrower` deposits into the manipulated ERC4626 vault. [14](#0-13)  The maximum direct loss is the entire idle balance redeployed by `onStartEpoch`, excluding only dust or rounding retained by the borrower's remaining vault shares. [1](#0-0) 

Because `getContractValue` is based on minted strategy-token balances rather than the programmable borrower's recoverable vault assets, the theft can remain unpriced until a withdrawal requires cash. [15](#0-14)  When that happens, the pool either defaults or remaining tranche holders absorb the loss through recovery accounting, so the broken invariant is both solvency and fair tranche pricing. [16](#0-15) 

### Likelihood Explanation
The attacker needs no privileged role: being able to deposit into, donate to, and redeem from the configured ERC4626 vault is sufficient. [3](#0-2)  The manager remains honest and merely executes a normal `startEpoch`, which the attacker can front-run in the same block. [7](#0-6) 

The cleanest execution requires a vault state in which the attacker can materially manipulate the minted-share quote, such as an empty or near-empty ERC4626 vault lacking inflation protection. [2](#0-1)  No existing skim, KYC, borrower, or only-CDO guard protects this path because the inflation occurs inside the external vault while `onStartEpoch` treats the returned shares as fully valued. [17](#0-16) [18](#0-17) 

### Recommendation
Require `_depositToVault` to preview or bound the received share amount and revert if `vault.convertToAssets(shares)` is materially below `_assetAmount`. [2](#0-1)  Use `deposit` only after checking a minimum acceptable share count derived from the pre-deposit quote, or use a mint call with an explicit maximum asset cost where available.

At epoch start, recheck `_currentVaultAssets()` after the deposit and revert if it is below the recorded baseline by more than a bounded rounding amount. [4](#0-3)  At stop, distinguish principal impairment from zero net interest so a vault loss cannot be silently floored at zero while CDO principal remains priced at par. [19](#0-18) 

### Proof of Concept
A Foundry PoC should fork the configured underlying token, initialize `IdleCDOEpochVariant`, `IdleCreditVault`, and `ProgrammableBorrower` in minted-interest mode, and point the borrower at an ERC4626 vault whose share quote can be inflated by an unprivileged first depositor and donation. [18](#0-17) 

```solidity
// Attacker front-runs the manager's startEpoch transaction.
usdc.approve(address(externalVault), 1);
externalVault.deposit(1, attacker);                 // 1 attacker share
usdc.transfer(address(externalVault), inflation);   // inflate assets/share

// Honest manager starts the epoch; the CDO sends pool cash to the borrower.
vm.prank(manager);
cdo.startEpoch();

// ProgrammableBorrower deposited `poolAmount` but received near-zero shares.
uint256 pbShares = externalVault.balanceOf(address(programmableBorrower));
assertLt(externalVault.convertToAssets(pbShares), poolAmount / 100);

// The attacker redeems the inflated share for the donation plus pool cash.
vm.prank(attacker);
externalVault.redeem(1, attacker, attacker);

assertGt(programmableBorrower.vaultLoss(), poolAmount * 99 / 100);

// Minted mode and no pending withdrawals: stop succeeds without pulling cash.
vm.warp(cdo.epochEndDate() + 1);
vm.prank(manager);
cdo.stopEpoch(0, 0);

assertEq(cdo.lastEpochInterest(), 0);
assertGt(cdo.virtualPrice(address(aaTranche)), 0);
```

The final assertions demonstrate that the external-vault loss is reported only through `vaultLoss`, while `totalInterestDueNow` returns zero and the successful stop leaves tranche principal accounting overstated. [10](#0-9) [20](#0-19)

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L201-224)
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
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L239-249)
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
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L330-346)
```text
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
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L378-385)
```text
  function _depositToVault(uint256 _assetAmount, uint256 _principalAssets) internal {
    if (_assetAmount == 0) return;
    uint256 shares = vault.deposit(_assetAmount, address(this));
    if (epochAccountingActive && _principalAssets != 0) {
      epochDepositedToVault += _principalAssets;
    }
    emit DepositedIntoVault(_assetAmount, shares);
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L546-549)
```text
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L233-245)
```text
  function startEpoch() external {
    _checkOnlyOwnerOrManager();

    // Check that buffer period passed (and epoch is not running as epochEndDate is set)
    // and that the pool is not closed (ie epochDuration == 0)
    uint256 _epochDuration = epochDuration; 
    _checkNotAllowed(defaulted || block.timestamp < (epochEndDate + bufferPeriod) || _epochDuration == 0);
    _checkProgrammableBorrowerMode();
    // Remove raw donated underlyings before calculating epoch interest or borrower transfer amounts.
    _skimDonatedAssets();

    isEpochRunning = true;
    // prevent deposits
```

**File:** contracts/IdleCDOEpochVariant.sol (L294-298)
```text
    uint256 _toBorrower = totUnderlyings - pendingInstant;
    try this.sendFundsToBorrower(_toBorrower) {
      // funds transferred correctly
      _startEpochProgrammableBorrower(_pendingWithdraws);
    } catch {
```

**File:** contracts/IdleCDOEpochVariant.sol (L373-380)
```text
    uint256 _grossInterest = _isRequestingAllFunds ? _expectedInterest - _totBorrowed : _expectedInterest;
    bool _mintInterest = isInterestMinted && !_isRequestingAllFunds;
    // In minted mode IdleCDO only needs cash for withdraw requests, not for epoch interest itself.
    uint256 _amountToPullFromBorrower = _mintInterest ? 0 : _expectedInterest;
    if (_mintInterest && _interest > 1) {
      uint256 _maxApr = _strategy.maxApr();
      _checkNotAllowed(_maxApr != 0 && _grossInterest > _calcInterestWithApr(getContractValue(), _maxApr) + _pendingWithdrawFees);
    }
```

**File:** contracts/IdleCDOEpochVariant.sol (L395-504)
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
```

**File:** contracts/IdleCDOCreditVault.sol (L125-137)
```text
  function getContractValue() public override view returns (uint256) {
    // Credit vault strategy tokens are minted 1:1 with underlyings and use the same decimals.
    return _contractTokenBalance(strategyToken) + _contractTokenBalance(token) - unclaimedFees;
  }

  /// @notice Calculates the current managed net TVL.
  /// @dev Raw underlyings held by the CDO are excluded because unsolicited transfers are skimmed on interactions.
  /// @return Strategy-token-backed TVL net of accrued fees.
  function _managedContractValue() internal virtual view returns (uint256) {
    uint256 strategyTokenBalance = _contractTokenBalance(strategyToken);
    uint256 fees = unclaimedFees;
    return strategyTokenBalance > fees ? strategyTokenBalance - fees : 0;
  }
```
