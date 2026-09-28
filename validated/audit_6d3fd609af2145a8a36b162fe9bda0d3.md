### Title

Rebasable underlying makes funded withdrawal and recovery claims insolvent - (File: `contracts/strategies/idle/IdleCreditVault.sol`)

### Summary

`IdleCreditVault` fixes withdrawal, instant-withdrawal, loss-adjusted, and default-recovery claims in absolute underlying-token units, but later pays them from the strategy’s mutable `balanceOf`. A negative rebase after claims are funded therefore breaks the funded-claim invariant: early claimants can withdraw their full recorded amount, while later claimants are left undercollateralized and their claims revert. The factory accepts an arbitrary `cvParams.underlying` and does not exclude rebasing tokens. [1](#0-0) [2](#0-1) 

### Finding Description

Normal requests mint fixed-denomination receipt balances and add their nominal claim to `pendingWithdraws`. [3](#0-2)  Instant requests similarly record a fixed amount in `instantWithdrawsRequests` and `pendingInstantWithdraws`. [4](#0-3) 

When the epoch is stopped, `collectWithdrawFunds` reduces `pendingWithdraws` and transfers the recorded nominal amount into the strategy, but no variable subsequently tracks the strategy’s actual claim backing. [2](#0-1)  The funded-claim path later burns the fixed receipt amount and calls `_transferFundedClaim` for the fixed payout. [5](#0-4)  `_transferFundedClaim` only protects `defaultRecoveryReserve`; it does not scale the payout to the current rebased balance or reserve the aggregate funded-withdrawal balance. [6](#0-5) 

The same mismatch exists after default finalization. `finalizeDefaultRecovery` calculates `defaultRecoveryReserve` and `defaultRecoveryPrice` from the nominal amount held or recovered at finalization. [7](#0-6)  Recovery claims then decrement that reserve by a fixed claim amount and transfer that amount. [8](#0-7)  If the reserve token rebases downward, `defaultRecoveryReserve` remains at its old nominal amount while the actual token balance is lower, so the final claimants cannot be paid.

### Impact Explanation

For a rebasing underlying, a 10% negative rebase after 100 tokens have been collected for withdrawal claims leaves only 90 tokens backing 100 tokens of recorded claims. The first claimant can receive 100 if it is the only claimant, or claimants can drain the reserve pro rata in arrival order until the balance is exhausted; the final claimant then experiences an unrecoverable `NotAllowed` or ERC20 transfer failure. The same effect permanently strands part of `defaultRecoveryReserve`, even though `defaultRecoveryPrice` promised those claims a fixed recovery amount. This is loss of funded withdrawals and permanent freezing of part of users’ claimable assets, proportional to the negative rebase.

### Likelihood Explanation

The issue requires the vault to be deployed with a rebasing pool currency and a negative rebase to occur while the strategy holds funded claims or recovery assets. The initializer and factory do not restrict `underlying` to non-rebasing tokens. [9](#0-8)  Any eligible tranche holder can create a pending withdrawal through `requestWithdraw`, and the ordinary honest manager/borrower epoch flow funds it. [10](#0-9)  The existing donation skim does not mitigate this because it applies to raw tokens held by the CDO, whereas the affected funds are held by `IdleCreditVault` and represent already-funded claims. [11](#0-10) 

### Recommendation

Do not treat a nominal underlying amount stored before a rebase as equivalent to the strategy’s post-rebase balance. Either:

- Explicitly reject rebasing underlying tokens during deployment and document that only fixed-supply ERC20s are supported; or
- Track funded-claim backing by an invariant proportional claim model. For each funded bucket, store claim shares or a receipt-to-backing index and calculate payouts as `claimShares * currentBucketBalance / totalClaimShares`. Apply the same treatment to normal claims, instant claims, loss-adjusted claims, `postDefaultRequests`, and `defaultRecoveryReserve`.
- On each claim, reconcile `defaultRecoveryReserve` and other funded buckets to `underlyingToken.balanceOf(address(this))`, and update recovery indexes so a negative rebase is socialized pro rata rather than imposed entirely on late claimants.

### Proof of Concept

A Foundry fork PoC should use a deployed credit vault whose `underlying` is an actual rebasing token such as AMPL. The test can prank that token’s authorized rebasing role to apply the negative rebase; the unprivileged users only deposit, request, and claim.

```solidity
function test_RebaseBreaksFundedWithdrawals() public {
    // Deploy strategy + IdleCDOEpochVariant through IdleCreditVaultFactory with
    // cvParams.underlying = REBASING_TOKEN.

    deal(REBASING_TOKEN, alice, 100e18);
    deal(REBASING_TOKEN, bob,   100e18);

    vm.startPrank(alice);
    IERC20(REBASING_TOKEN).approve(address(cdo), 100e18);
    cdo.depositAA(100e18);
    cdo.requestWithdraw(0, cdo.AATranche());
    vm.stopPrank();

    vm.startPrank(bob);
    IERC20(REBASING_TOKEN).approve(address(cdo), 100e18);
    cdo.depositAA(100e18);
    cdo.requestWithdraw(0, cdo.AATranche());
    vm.stopPrank();

    // Honest epoch lifecycle.
    vm.prank(manager);
    cdo.startEpoch();
    vm.warp(cdo.epochEndDate() + 1);

    // Borrower repays enough to fund both pending requests.
    vm.prank(borrower);
    IERC20(REBASING_TOKEN).approve(address(cdo), type(uint256).max);
    vm.prank(manager);
    cdo.stopEpoch(0, expectedInterest);

    assertEq(strategy.pendingWithdraws(), 0);
    uint256 backingBefore =
        IERC20(REBASING_TOKEN).balanceOf(address(strategy));
    assertEq(backingBefore, 200e18);

    // Mainnet fork: call the token's authorized rebase entry point.
    // For AMPL this is the monetary-policy role invoking rebase().
    // Apply a 10% supply reduction.
    vm.prank(REBASE_AUTHORITY);
    IRebasable(REBASING_TOKEN).rebase(nextEpoch, -int256(supply / 10));

    assertEq(
        IERC20(REBASING_TOKEN).balanceOf(address(strategy)),
        180e18
    );

    // Alice's fixed claim still pays the pre-rebase nominal amount.
    vm.prank(alice);
    cdo.claimWithdrawRequest();
    assertEq(IERC20(REBASING_TOKEN).balanceOf(alice), 100e18);

    // Bob's equal claim is no longer backed and permanently reverts.
    vm.prank(bob);
    vm.expectRevert();
    cdo.claimWithdrawRequest();
}
```

For the default-recovery path, repeat the setup with a borrower default and `finalizeDefault`. After `finalizeDefaultRecovery` stores `defaultRecoveryReserve`, execute the same negative rebase. Two users with equal recovery claims then produce the same result: the first claim consumes the old nominal amount, while the second claim cannot be paid from the reduced token balance.

### Citations

**File:** contracts/IdleCreditVaultFactory.sol (L182-207)
```text
    strategy = IdleCreditVault(_deployProxy(
      strategyData.implementation,
      abi.encodeWithSelector(
        IdleCreditVault.initialize.selector,
        cvParams.underlying,
        address(this),
        strategyData.manager,
        strategyData.borrower,
        strategyData.borrowerName,
        cvParams.apr
      )
    ));

    cv = IdleCDOEpochVariant(_deployProxy(
      cvParams.implementation,
      abi.encodeWithSelector(
        IdleCDOCreditVault.initialize.selector,
        cvParams.limit,
        cvParams.underlying,
        treasury,
        address(this),
        address(0),
        address(strategy),
        FULL_ALLOC
      )
    ));
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L123-140)
```text
  /// @param _underlyingToken address of the underlying token (pool currency)
  function initialize(
    address _underlyingToken,
    address _owner,
    address _manager,
    address _borrower,
    string memory borrowerName,
    uint256 _apr
  ) public virtual initializer {
    OwnableUpgradeable.__Ownable_init();
    ReentrancyGuardUpgradeable.__ReentrancyGuard_init();
    require(token == address(0), "Token is already initialized");

    //----- // -------//
    token = _underlyingToken;
    underlyingToken = IERC20Detailed(token);
    tokenDecimals = underlyingToken.decimals();
    oneToken = 10**(tokenDecimals);
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L319-349)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-374)
```text
  function requestInstantWithdraw(uint256 _amount, address _user) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    // burn strategy tokens from cdo
    _burn(msg.sender, _amount);
  
    // mint equal amount of strategy tokens to the user as receipt, useful in case of default
    _mint(_user, _amount);

    // increase the instant withdraw requests for the user
    instantWithdrawsRequests[_user] += _amount;
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-429)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L683-709)
```text
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-907)
```text
  function _transferFundedClaim(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    uint256 reserve = defaultRecoveryReserve;
    if (reserve != 0) {
      uint256 balance = underlyingToken.balanceOf(address(this));
      // This should be unreachable when accounting is consistent. Keep the guard so old funded
      // receipts can never spend underlyings reserved for default recovery claimants.
      if (balance < reserve || balance - reserve < _amount) revert NotAllowed();
    }
    underlyingToken.safeTransfer(_user, _amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L912-917)
```text
  function _transferDefaultRecovery(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    // Every defaulted or post-default claim consumes the isolated recovery reserve.
    defaultRecoveryReserve -= _amount;
    underlyingToken.safeTransfer(_user, _amount);
  }
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
