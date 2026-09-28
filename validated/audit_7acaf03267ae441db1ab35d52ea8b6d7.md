### Title
Setting `IdleCreditVault.borrower` to the CDO causes user deposits to be treated as donations and lost - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`IdleCreditVault.setBorrower` only rejects the zero address and does not prevent the owner from configuring the vault's own `idleCDO` as the borrower. [1](#0-0)  If this happens, `startEpoch` sends user principal back to the CDO, while epoch-end settlement still attempts to pull that principal from the CDO through `transferFrom`. [2](#0-1)  Because the CDO does not grant itself an ERC-20 allowance, the pull fails, the vault defaults, and default finalization skims the stranded balance to `feeReceiver` instead of treating it as recovery collateral. [3](#0-2) 

### Finding Description
`IdleCreditVault.setBorrower` accepts any nonzero address, including `idleCDO`, the same contract that originates the epoch funding transfer. [1](#0-0) 

During `startEpoch`, the CDO retrieves deposits and accrued interest from the strategy, reserves pending instant withdrawals if needed, and sends the remaining balance to the configured borrower through `sendFundsToBorrower`. [4](#0-3)  When `borrower == idleCDO`, that transfer returns the funds to the CDO itself and still succeeds.

At `stopEpoch`, the CDO calls the external self-call `getFundsFromBorrower`, which performs `transferFrom(_borrower(), address(this), amount)`. [5](#0-4)  With the borrower set to the CDO, standard ERC-20 implementations require an allowance even when source and destination are identical, so the call reverts. The enclosing catch converts this into a borrower default. [6](#0-5) 

Default finalization then invokes `_skimDonatedAssets`, whose documented behavior is to send raw CDO-held underlyings to `feeReceiver` before strategy recovery accounting runs. [7](#0-6)  The principal that was accidentally returned to the CDO is therefore excluded from `defaultRecoveryReserve` and removed before claimants receive recovery.

### Impact Explanation
A lender's entire active principal can be defaulted on and redirected to `feeReceiver` even though the assets never left protocol control and no borrower credit loss occurred. If `finalizeDefault(0, address(0))` is executed before those assets are returned, active tranche claims finalize at a zero recovery ratio while the stranded principal is skimmed as a donation. This produces direct loss of user funds and breaks the solvency and one-receipt-one-recovery invariants.

The issue is the credit-vault analogue of configuring a destination equal to the source chain: a transfer is accepted and accounted as sent to an external destination, but the funds actually return to the source contract. Settlement later treats the same balance as unavailable borrower collateral and defaults the position.

### Likelihood Explanation
Likelihood is low because the owner or an equivalent deployment process must misconfigure `borrower` as the CDO address. The exposed validation gap is nevertheless direct: the setter only checks `_borrower != address(0)`, while the CDO and strategy are known trusted protocol addresses at configuration time. [1](#0-0)  Once set, an ordinary KYC-passing depositor can trigger the loss path without privileged access or malicious borrower behavior.

Existing checks do not stop this. `sendFundsToBorrower` only enforces that it is invoked through the CDO's internal self-call, not that the resolved borrower differs from the CDO. [8](#0-7)  `setWhitelistedCDO` likewise validates only that the CDO address is nonzero. [9](#0-8) 

### Recommendation
Reject protocol-controlled addresses in `IdleCreditVault.setBorrower` and during initialization. At minimum, enforce:

```solidity
if (
  _borrower == address(0) ||
  _borrower == address(this) ||
  _borrower == idleCDO
) revert InvalidAddress();
```

When `setWhitelistedCDO` changes `idleCDO`, also reject `borrower == _cdo` or require a simultaneous valid borrower update. Add deployment and upgrade checks that fail if the strategy's `borrower`, `idleCDO`, or `address(strategy)` aliases overlap.

For defense in depth, `finalizeDefault` should distinguish accounting-legitimate CDO-held epoch principal from unsolicited donations, or provide a dedicated way to move it into strategy recovery reserve before calculating `defaultRecoveryPrice`.

### Proof of Concept
The following Foundry test can be added to the existing credit-vault test harness. It assumes the production `IdleCreditVault`, `IdleCDOEpochVariant`, USDC-compatible underlying, `owner`, `manager`, `feeReceiver`, and standard `ONE_SCALE` setup used by `test/foundry/IdleCreditVault.t.sol`.

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

function testBorrowerSetToCdoDefaultsAndSkimsPrincipal() external {
    address user = makeAddr("user");
    uint256 amount = 100_000 * ONE_SCALE;

    // Honest owner accidentally configures the CDO as its own borrower.
    vm.prank(owner);
    strategy.setBorrower(address(cdoEpoch));
    assertEq(strategy.borrower(), address(cdoEpoch));

    // Ordinary lender deposits during the buffer phase.
    deal(address(underlying), user, amount);
    vm.startPrank(user);
    underlying.approve(address(cdoEpoch), amount);
    cdoEpoch.depositAA(amount);
    vm.stopPrank();

    // startEpoch succeeds: the CDO "sends funds to borrower", but the borrower
    // is the CDO, so principal returns to the source contract.
    vm.prank(manager);
    cdoEpoch.startEpoch();
    assertEq(underlying.balanceOf(address(cdoEpoch)), amount);
    assertTrue(cdoEpoch.isEpochRunning());

    // At settlement, getFundsFromBorrower does transferFrom(cdo, cdo).
    // No self-approval exists, so the call fails and the catch path defaults.
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    assertTrue(cdoEpoch.defaulted());
    assertFalse(cdoEpoch.isEpochRunning());
    assertEq(underlying.balanceOf(address(cdoEpoch)), amount);

    // Default finalization treats raw CDO-held principal as a donation.
    vm.prank(manager);
    cdoEpoch.finalizeDefault(0, address(0));

    assertEq(underlying.balanceOf(address(cdoEpoch)), 0);
    assertEq(underlying.balanceOf(feeReceiver), amount);
    assertEq(strategy.defaultRecoveryPrice(), 0);

    // The lender's tranche claim has been finalized with zero recovery even
    // though the full principal remained inside protocol contracts.
    assertEq(idleCDO.virtualPrice(idleCDO.AATranche()), 0);
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L189-194)
```text
  /// @notice set borrower address
  /// @param _borrower address of the new borrower
  function setBorrower(address _borrower) external onlyOwner {
    require(_borrower != address(0), "IS_0");
    borrower = _borrower;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L953-957)
```text
  /// @notice allow to update whitelisted address
  function setWhitelistedCDO(address _cdo) external onlyOwner {
    require(_cdo != address(0), "IS_0");
    idleCDO = _cdo;
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L194-205)
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
```

**File:** contracts/IdleCDOEpochVariant.sol (L270-302)
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
```

**File:** contracts/IdleCDOEpochVariant.sol (L306-311)
```text
  /// @notice workaround to have safeTransfer to borrower as external and use it in a try/catch block
  /// @param _amount Amount of underlyings to transfer
  function sendFundsToBorrower(uint256 _amount) external {
    _checkNotAllowed(msg.sender != address(this));
    _transferUnderlyings(_borrower(), _amount);
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

**File:** contracts/IdleCDOEpochVariant.sol (L548-553)
```text
  /// @dev Get funds from borrower through an external self-call so callers can use try/catch.
  /// @param _amount Total amount to transfer
  function getFundsFromBorrower(uint256 _amount) external {
    _checkNotAllowed(msg.sender != address(this));
    _transferUnderlyingsFrom(_borrower(), address(this), _amount);
  }
```
