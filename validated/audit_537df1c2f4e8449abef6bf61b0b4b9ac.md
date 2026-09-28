### Title
Donated withdrawal receipts inflate the first-depositor share price - ([File: contracts/IdleCDOCreditVault.sol](contracts/IdleCDOCreditVault.sol))

### Summary
`IdleCDOCreditVault` protects NAV from unsolicited `token` donations, but it counts every transferable `IdleCreditVault` strategy-token receipt held by the CDO as active TVL. An attacker can obtain a withdrawal receipt, restart a tranche with a 1-wei deposit, donate the receipt to the CDO, and inflate the tranche price before a later depositor mints shares. [1](#0-0) [2](#0-1) 

### Finding Description
`getContractValue()` derives NAV from `strategyToken.balanceOf(CDO) + token.balanceOf(CDO) - unclaimedFees`, while `virtualPrice()` normally uses only the CDO’s managed strategy-token balance. [3](#0-2)  `_skimDonatedAssets()` removes only raw underlying tokens and does not isolate donated strategy-token withdrawal receipts. [2](#0-1) 

A withdrawal request burns the CDO’s principal-denominated strategy tokens and mints a transferable receipt to the requester, while the request remains recorded as a pending claim. [4](#0-3)  If the requester transfers that receipt to the CDO, the same claim is simultaneously a pending withdrawal liability and an active CDO asset counted in NAV. [5](#0-4) 

For an 18-decimal underlying, the attacker can then recreate the ERC4626 inflation pattern exactly:

1. Alice deposits `R` and calls `requestWithdraw(0, AATranche)`, leaving the tranche with zero supply and receiving an approximately `R`-denominated strategy-token receipt.
2. Alice deposits `1` wei, receiving `1` wei of tranche shares because an empty tranche mints at `oneToken`. [6](#0-5) [7](#0-6) 
3. Alice transfers her `R` receipt tokens to the CDO.
4. Bob deposits `2R`.
5. Bob’s deposit first executes `_updateAccounting()`, which treats the donated receipt as a gain over saved NAV and raises Alice’s 1-wei share price to approximately `R`. [8](#0-7) [9](#0-8) 
6. Bob receives only `floor(2R/(R+1)) = 1` share.
7. Alice and Bob each own one share while managed NAV is approximately `3R`; Alice can submit a withdrawal request for approximately `1.5R`.

The attack is profitable because Alice’s sacrificed `R` receipt returns approximately `1.5R`, stealing approximately `0.5R` from Bob before fees and rounding. [10](#0-9) 

### Impact Explanation
This is direct theft through unfair share minting: the victim’s deposit is captured by the attacker’s previously minted share because a pending withdrawal receipt is double-counted as active CDO backing. It also creates insolvency because the donated receipt remains represented by `withdrawsRequests`/`withdrawsRequestsByEpoch` while its token balance is counted again in CDO NAV. [11](#0-10) 

### Likelihood Explanation
The attacker needs only to be an allowed lender and make ordinary deposits and withdrawal requests; no owner, manager, borrower, or malicious token behavior is required. The profitable rounding condition is strongest for 18-decimal underlying tokens, where a 1-wei deposit mints 1 wei of shares; with lower-decimal assets, the extra share-decimal precision substantially reduces or eliminates the practical rounding capture. [6](#0-5) [12](#0-11) 

### Recommendation
Do not derive active NAV directly from `balanceOf(idleCDO)` for a strategy token that is also used as a transferable withdrawal receipt. Track active principal separately from receipt supply, or subtract strategy-token balances corresponding to outstanding withdrawal claims before calculating `getContractValue()` and `_managedContractValue()`. Alternatively, make withdrawal receipts non-transferable or introduce a distinct non-transferable claim token so a claimant cannot donate the receipt while retaining the recorded claim. [4](#0-3) [1](#0-0) 

### Proof of Concept
The following Foundry sequence uses the existing credit-vault fixture and assumes an 18-decimal underlying and two wallets that satisfy `isWalletAllowed`:

```solidity
function testReceiptDonationInflatesInitialSharePrice() external {
    uint256 donation = 1_000e18;
    uint256 victimDeposit = 2_000e18;

    address alice = makeAddr("alice");
    address bob = makeAddr("bob");

    deal(address(underlying), alice, donation + 1);
    deal(address(underlying), bob, victimDeposit);

    _allowWallet(alice);
    _allowWallet(bob);

    // Alice converts an ordinary deposit into a transferable withdraw receipt.
    vm.startPrank(alice);
    underlying.approve(address(cdoEpoch), type(uint256).max);
    uint256 firstShares = cdoEpoch.depositAA(donation);
    cdoEpoch.requestWithdraw(firstShares, address(AAtranche));

    uint256 donatedReceipt = strategy.balanceOf(alice);
    assertGt(donatedReceipt, 0);

    // Recreate the empty-tranche first deposit with 1 wei.
    cdoEpoch.depositAA(1);
    uint256 aliceShares = AAtranche.balanceOf(alice);
    assertEq(aliceShares, 1);

    // Donate the receipt. _skimDonatedAssets only removes raw underlying.
    strategy.transfer(address(cdoEpoch), donatedReceipt);
    vm.stopPrank();

    // Bob deposits 2x the donated receipt and receives only one share.
    vm.startPrank(bob);
    underlying.approve(address(cdoEpoch), type(uint256).max);
    cdoEpoch.depositAA(2 * donatedReceipt);
    uint256 bobShares = AAtranche.balanceOf(bob);
    vm.stopPrank();

    assertEq(bobShares, aliceShares);

    // Alice's single wei-denominated share is now entitled to about half
    // of donatedReceipt + 1 + 2 * donatedReceipt.
    vm.prank(alice);
    uint256 aliceRequest = cdoEpoch.requestWithdraw(0, address(AAtranche));

    assertGt(aliceRequest, donatedReceipt);
    assertApproxEqRel(
        aliceRequest,
        (donatedReceipt + 1 + 2 * donatedReceipt) / 2,
        0.01e18
    );
}
```

The critical state transition is that `strategy.balanceOf(cdoEpoch)` increases by the donated receipt while `lastNAVAA` remains at the attacker’s 1-wei deposit, causing `_virtualPriceAux()` to classify the receipt as a gain and mint later deposits at the inflated price. [13](#0-12) [14](#0-13)

### Citations

**File:** contracts/IdleCDOCreditVault.sol (L72-75)
```text
    uint256 _oneToken = 10**(IERC20Detailed(_guardedToken).decimals());
    oneToken = _oneToken;
    priceAA = _oneToken;
    priceBB = _oneToken;
```

**File:** contracts/IdleCDOCreditVault.sol (L123-136)
```text
  /// @notice calculates the current net TVL (in `token` terms)
  /// @dev `unclaimedFees` are not counted.
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
```

**File:** contracts/IdleCDOCreditVault.sol (L222-236)
```text
  function _updateAccounting() internal virtual returns (bool shutdown) {
    _accrueManagementFee();
    uint256 _lastNAVAA = lastNAVAA;
    uint256 _lastNAVBB = lastNAVBB;
    uint256 _lastNAV = _lastNAVAA + _lastNAVBB;
    uint256 nav = getContractValue();
    uint256 _aprSplitRatio = trancheAPRSplitRatio;
    // If gain is > 0, then collect some fees in `unclaimedFees`
    if (nav > _lastNAV) {
      unclaimedFees += (nav - _lastNAV) * fee / FULL_ALLOC;
    }
    (uint256 _priceAA, int256 _totalAAGain) = _virtualPriceAux(AATranche, nav, _lastNAV, _lastNAVAA, _aprSplitRatio);
    (uint256 _priceBB, int256 _totalBBGain) = _virtualPriceAux(BBTranche, nav, _lastNAV, _lastNAVBB, _aprSplitRatio);
    lastNAVAA = uint256(int256(_lastNAVAA) + _totalAAGain);
    lastNAVBB = uint256(int256(_lastNAVBB) + _totalBBGain);
```

**File:** contracts/IdleCDOCreditVault.sol (L305-347)
```text
    // Calculate the total gain/loss
    int256 totalGain = int256(_nav) - int256(_lastNAV);
    // Ordinary zero-delta interactions keep their saved price for compatibility. Forced
    // accounting recomputes NAV per share, which is required after discounted mid-epoch deposits.
    if (totalGain == 0 && !skipDefaultCheck) return (_tranchePrice(_tranche), 0);

    // Remove performance fee for gains
    if (totalGain > 0) {
      totalGain -= totalGain * int256(fee) / int256(FULL_ALLOC);
    }

    bool _isAATranche = _tranche == AATranche;
    // A class with no saved NAV cannot be revived by later gains. If only this class has saved
    // NAV, it receives the full gain or loss; otherwise both classes participate.
    if (_lastTrancheNAV == 0) {
      _totalTrancheGain = 0;
    } else if (_lastNAV == _lastTrancheNAV) {
      _totalTrancheGain = totalGain;
    } else {
      if (totalGain > 0) {
        // Split the net gain, according to _trancheAPRSplitRatio, with precision loss favoring the AA tranche.
        int256 totalBBGain = totalGain * int256(FULL_ALLOC - _trancheAPRSplitRatio) / int256(FULL_ALLOC);
        // The new NAV for the tranche is old NAV + total gain for the tranche
        _totalTrancheGain = _isAATranche ? (totalGain - totalBBGain) : totalBBGain;
      } else {
        int256 maxBBLoss = -int256(lastNAVBB);
        int256 totalBBLoss = totalGain > maxBBLoss ? totalGain : maxBBLoss;
        _totalTrancheGain = _isAATranche ? totalGain - totalBBLoss : totalBBLoss;
      }
    }
    // Split the new NAV (_lastTrancheNAV + _totalTrancheGain) per tranche token
    _virtualPrice = uint256(int256(_lastTrancheNAV) + _totalTrancheGain) * ONE_TRANCHE_TOKEN / trancheSupply;
  }

  /// @notice mint tranche tokens at current price and updates tranche last NAV
  /// @param _amount, in underlyings, to convert in tranche tokens
  /// @param _to receiver address of the newly minted tranche tokens
  /// @param _tranche tranche address
  /// @return _minted number of tranche tokens minted
  function _mintSharesAtCurrPrice(uint256 _amount, address _to, address _tranche) internal virtual returns (uint256 _minted) {
    // calculate # of tranche token to mint based on current tranche price: _amount / tranchePrice
    _minted = _amount * ONE_TRANCHE_TOKEN / _tranchePrice(_tranche);
    _mintShares(_tranche, _to, _minted, _amount);
```

**File:** contracts/IdleCDOEpochVariant.sol (L739-747)
```text
  function requestWithdraw(uint256 _amount, address _tranche) external returns (uint256 _underlyings) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == AATranche ? !allowAAWithdrawRequest : !allowBBWithdrawRequest) ||
      !isWalletAllowed(msg.sender)
    );
  
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();
```

**File:** contracts/IdleCDOEpochVariant.sol (L752-790)
```text
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

**File:** contracts/IdleCDOEpochVariant.sol (L793-796)
```text
  /// @notice Transfer donated assets to the feeReceiver
  function _skimDonatedAssets() internal {
    _transferUnderlyings(feeReceiver, _contractTokenBalance(token));
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L272-294)
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
      // increase the withdraw requests for the user
      // we record both per-user (old, kept for compatibility) and per-epoch so
      // on finalization we can distinguish "default-epoch pending receipts"
      // from old funded receipts.
      withdrawsRequests[_user] += _amount;
      withdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    }
```
