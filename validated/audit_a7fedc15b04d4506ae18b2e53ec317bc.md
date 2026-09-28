### Title
Withdrawal-receipt donation lets a dust tranche holder steal subsequent deposits - ([File: contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol))

### Summary
An allowed lender can retain a dust tranche position, request withdrawal of the rest, transfer the resulting `IdleCreditVault` receipt tokens back to the CDO, and inflate the tranche price before a victim deposits. Because deposits have no `minShares` protection and share minting rounds down, the victim can receive zero or severely underpriced tranche tokens while the attacker’s dust position later claims nearly all contributed value. [1](#0-0) [2](#0-1) 

### Finding Description
`IdleCreditVault.requestWithdraw` burns the requested principal from the CDO and mints receipt strategy tokens to the requester. [3](#0-2)  The strategy token is the `IdleCreditVault` ERC20 itself, and withdrawal receipts are ordinary transferable balances. [4](#0-3) [5](#0-4) 

During the buffer phase, an attacker deposits into AA, retains a dust tranche balance, and requests withdrawal of substantially all remaining shares. The attacker then directly transfers the minted receipt tokens to the CDO address. `getContractValue` treats every strategy-token balance held by the CDO as active NAV, while `_skimDonatedAssets` removes only raw underlying tokens and does not isolate donated strategy tokens. [6](#0-5) [7](#0-6) 

The next `_updateAccounting` therefore recognizes the donated receipt balance as a gain and assigns it to the attacker’s remaining tranche supply when the opposite tranche has no supply. [8](#0-7) [9](#0-8)  A victim deposit then mints `amount * ONE_TRANCHE_TOKEN / tranchePrice`; if the inflated price exceeds the deposited amount, integer division returns zero, and otherwise the depositor loses the proportional excess value. [2](#0-1) 

The buffer-phase deposit path calls `_skimDonatedAssets`, but that guard cannot remove strategy tokens and consequently does not prevent this inflation. [10](#0-9) [7](#0-6) 

### Impact Explanation
The attacker converts a withdrawal receipt into artificial active NAV and captures subsequent lender principal. For example, retaining one tranche wei while donating `999_000e6` receipt tokens can push the dust share’s claim near that amount, subject to fees and rounding. A victim depositing less than the resulting tranche price receives zero shares, while a larger deposit receives materially fewer shares than fair value. [2](#0-1) [11](#0-10) 

The attacker can then submit a withdrawal request for the retained dust tranche balance; `requestWithdraw` converts tranche tokens to underlying at the inflated saved price and creates a claimable receipt. [12](#0-11) [13](#0-12)  This breaks fair mint/burn and enables direct theft of victim deposits, with loss approximately equal to the victim deposit less any shares received and residual value.

### Likelihood Explanation
The attacker only needs to pass the same wallet allowlist applied to ordinary deposits and withdrawal requests and to hold enough underlying for the initial deposit and request. [14](#0-13) [15](#0-14)  The attack occurs in the normal buffer phase before `startEpoch`, when deposits and withdrawal requests are enabled and the attacker’s actions consist only of deposit, withdrawal request, ERC20 transfer, and another withdrawal request. [16](#0-15) 

The profitable path requires timing around a victim deposit and enough donated value to make the victim’s minted amount round down materially. Existing KYC, pause, tranche-address, zero-supply, and raw-donation checks do not prevent it because all attacker calls are permitted and the donated asset is a strategy token rather than raw underlying. [1](#0-0) [17](#0-16) 

### Recommendation
Treat CDO-held `IdleCreditVault` tokens as claim/accounting state rather than freely countable active NAV, or maintain an internal active-strategy-token balance instead of using `balanceOf(address(this))`. At minimum, extend donation skimming to strategy-token balances not accounted for by `lastNAVAA + lastNAVBB`, pending receipts, and expected interest. [6](#0-5) [7](#0-6) 

Additionally, require a nonzero minimum minted amount and preferably expose a `minShares` parameter on `depositAA`, `depositBB`, and epoch deposits. For a more complete ERC4626-style mitigation, seed a minimum locked liquidity amount or use virtual shares/assets so a direct donation cannot produce a discontinuous price for dust supply. [18](#0-17) [2](#0-1) 

### Proof of Concept
The following Foundry-style sequence assumes `cdo` is an `IdleCDOEpochVariant`, `vault` is its `IdleCreditVault`, `aa` is the AA tranche token, both users are allowlisted, and the pool is in the buffer phase.

```solidity
function testReceiptDonationInflatesFirstDustShare() public {
    uint256 attackerDeposit = 1_000_000e6;
    uint256 victimDeposit   =   500_000e6;

    deal(address(underlying), attacker, attackerDeposit);
    deal(address(underlying), victim, victimDeposit);

    // Attacker creates a tranche position.
    vm.startPrank(attacker);
    underlying.approve(address(cdo), attackerDeposit);
    uint256 shares = cdo.depositAA(attackerDeposit);

    // Keep dust active and convert the rest into transferable vault receipts.
    uint256 dust = 1;
    uint256 receiptAmount = cdo.requestWithdraw(shares - dust, address(aa));

    // Donate the receipt strategy tokens to the CDO. This is not skimmed.
    IERC20(address(vault)).transfer(address(cdo), receiptAmount);
    vm.stopPrank();

    // Victim's next interaction causes accounting to absorb the donation.
    vm.startPrank(victim);
    underlying.approve(address(cdo), victimDeposit);
    uint256 victimShares = cdo.depositAA(victimDeposit);
    vm.stopPrank();

    // If receiptAmount is large enough relative to dust and victimDeposit,
    // victimShares rounds to zero or is materially below fair value.
    assertLt(victimShares, victimDeposit * 1e18 / cdo.tranchePrice(address(aa)) + 1);

    // Attacker's retained dust carries nearly all donated plus deposited NAV.
    uint256 attackerClaim = cdo.requestWithdraw(dust, address(aa));
    assertGt(attackerClaim, receiptAmount + victimDeposit - 1);
}
```

Mechanically, the deposit’s preceding accounting call calculates `totalGain = current strategy-token NAV - saved NAV`, attributes the donated balance to the dust AA supply, and stores the inflated price. [19](#0-18) [11](#0-10)  The victim’s transfer is then priced through `_mintSharesAtCurrPrice`, whose integer division permits a zero-share deposit and provides no minimum-share check. [20](#0-19) [2](#0-1)

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L233-250)
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
    _pause();

    // prevent withdrawals requests
    allowAAWithdrawRequest = false;
    allowBBWithdrawRequest = false;
```

**File:** contracts/IdleCDOEpochVariant.sol (L643-669)
```text
  /// @notice Deposit funds in the vault. Overrides the parent method and adds a check for wallet 
  function _deposit(uint256 _amount, address _tranche) internal override whenNotPaused returns (uint256) {
    _checkNotAllowed(!isWalletAllowed(msg.sender));
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();
    // do the inherited deposit flow
    return super._deposit(_amount, _tranche);
  }

  /// @notice Deposit during an active epoch with prorated interest
  /// @param _amount Amount of underlyings
  /// @param _tranche Tranche to deposit into
  /// @return _minted Amount of tranche tokens minted
  function depositDuringEpoch(uint256 _amount, address _tranche) external virtual returns (uint256 _minted) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == BBTranche && !isBBDepositEnabled) ||
      isDepositDuringEpochDisabled ||
      skipDefaultCheck ||
      // programmable borrowers use APR=0 so mid-epoch deposits would dilute existing depositors
      isProgrammableBorrower ||
      // check if AYS is active as we don't support deposits during epoch in that case
      isAYSActive ||
      // check if epoch is still running even if not manually stopped yet
      !isEpochRunning || block.timestamp >= epochEndDate ||
      !isWalletAllowed(msg.sender)
    );
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

**File:** contracts/IdleCDOEpochVariant.sol (L793-796)
```text
  /// @notice Transfer donated assets to the feeReceiver
  function _skimDonatedAssets() internal {
    _transferUnderlyings(feeReceiver, _contractTokenBalance(token));
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L95-110)
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
  }
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

**File:** contracts/IdleCDOCreditVault.sol (L222-248)
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

    // Ordinary losses exhaust BB before reducing AA. Stop normal interactions once BB is wiped,
    // or when an AA-only vault is fully wiped, so the loss must be crystallized explicitly.
    if ((_totalBBGain < 0 && -_totalBBGain >= int256(_lastNAVBB)) || (_lastNAV != 0 && nav == 0)) {
      shutdown = true;
      if (!skipDefaultCheck) revert Default();
      // Keep a total wipe distinguishable from an uninitialized vault when no BB NAV existed.
      if (nav == 0) _priceAA = 0;
      _emergencyShutdown(true);
    }
    priceAA = _priceAA;
    priceBB = _priceBB;
```

**File:** contracts/IdleCDOCreditVault.sol (L287-336)
```text
  function _virtualPriceAux(
    address _tranche,
    uint256 _nav,
    uint256 _lastNAV,
    uint256 _lastTrancheNAV,
    uint256 _trancheAPRSplitRatio
  ) internal virtual view returns (uint256 _virtualPrice, int256 _totalTrancheGain) {
    // A zero supply identifies a tranche that was never initialized. A non-zero supply with
    // zero NAV is an economically wiped tranche and must keep its saved zero price.
    uint256 trancheSupply = _trancheSupply(_tranche);
    if (trancheSupply == 0) return (oneToken, 0);
    if (_lastNAV == 0 && _nav == 0) return (0, 0);

    // In order to correctly split the interest generated between AA and BB tranche holders
    // (according to the trancheAPRSplitRatio) we need to know how much interest/loss we gained
    // since the last price update (during a depositXX/withdrawXX)
    // To do that we need to get the current value of the assets in this contract
    // and the last saved one (always during a depositXX/withdrawXX)
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
```

**File:** contracts/IdleCDOCreditVault.sol (L344-347)
```text
  function _mintSharesAtCurrPrice(uint256 _amount, address _to, address _tranche) internal virtual returns (uint256 _minted) {
    // calculate # of tranche token to mint based on current tranche price: _amount / tranchePrice
    _minted = _amount * ONE_TRANCHE_TOKEN / _tranchePrice(_tranche);
    _mintShares(_tranche, _to, _minted, _amount);
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L243-295)
```text
  function requestWithdraw(uint256 _amount, address _user, uint256 _principal) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    if (_amount == 0) return;
    if (defaultRecoveryFinalized) {
      // user should first claim old already-funded withdraw requests before requesting new ones after default
      if (_hasWithdrawRequest(_user) || instantWithdrawsRequests[_user] != 0 || postDefaultRequests[_user] != 0) {
        revert NotAllowed();
      }
      // Preserve request/claim UX after default without increasing borrower-facing pendingWithdraws.
      // The CDO passes an already-haircut amount because finalization lowered virtualPrice first.
      _burn(msg.sender, _amount);
      _mint(_user, _amount);
      postDefaultRequests[_user] = _amount;
      return;
    }
    bool isClosed = IIdleCDOEpochVariant(idleCDO).epochEndDate() == 0;
    uint256 currentEpoch = epochNumber;
    uint256 lossEpoch = lastWithdrawRequest[_user];
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (
      lossRecoveryPrice != 0 &&
      (withdrawsRequestsByEpoch[_user][lossEpoch] != 0 ||
      (apr0Users[_user].principal != 0 && apr0Users[_user].principalEpoch == lossEpoch))
    ) {
      // A loss-adjusted receipt must be claimed before opening a later request, otherwise
      // `lastWithdrawRequest` would stop pointing to the epoch that stores its haircut.
      revert NotAllowed();
    }
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
  }
```
