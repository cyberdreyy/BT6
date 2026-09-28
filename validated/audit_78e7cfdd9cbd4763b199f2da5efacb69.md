### Title
Fractional APR truncation permanently underpays epoch interest - ([File: contracts/IdleCDOEpochVariant.sol])

### Summary
`IdleCDOEpochVariant._calcInterestWithApr` divides the fixed-point APR by `100` before multiplying it by principal and duration, truncating every fractional percentage point and computing zero interest for any APR below `1%`. [1](#0-0)  The resulting value is stored as `expectedEpochInterest` at `startEpoch` and later determines how much is pulled from the borrower at `stopEpoch`. [2](#0-1) [3](#0-2) 

### Finding Description
APR values are expressed as percentages scaled by `1e18`, so `1.99e18` represents `1.99%`. [4](#0-3)  The expression `_apr / 100` discards all fractional-percent precision before `_apr` is scaled by principal and epoch duration. [1](#0-0) 

The problem is amplified by buffer scaling because `setAprsWithBuffer` converts the configured unscaled APR to `unscaledApr * (_duration + _buffer) / _duration`. [5](#0-4)  For the configured test parameters of a `36.5`-day epoch and `5`-day buffer, an intended `10%` APR becomes approximately `11.369863%`, but the accounting expression truncates it to `11%`. [6](#0-5) 

`ProgrammableBorrower._calcInterest` repeats the same division-before-multiplication pattern when calculating contractual borrower interest. [7](#0-6) 

### Impact Explanation
For a `10_000_000 USDC` pool, a `365`-day epoch, no buffer, and a configured `1.99%` APR, the intended interest is `199_000 USDC`, while the stored expected interest is only `100_000 USDC`, permanently underpaying lenders by `99_000 USDC`, or approximately `49.75%` of the expected yield. [2](#0-1) [1](#0-0)  Any scaled APR below `1e18` produces zero expected interest even though the configured rate is nonzero. [1](#0-0) 

This is not ordinary wei-level rounding: it can remove nearly half of an epoch’s yield and all yield below `1%`. [1](#0-0) 

### Likelihood Explanation
Fractional APRs are naturally created whenever the configured unscaled APR is scaled by `(epochDuration + bufferPeriod) / epochDuration`, which is the intended epoch configuration path. [5](#0-4) [8](#0-7)  No malformed input, overflow, privileged misconduct, donation, or external oracle manipulation is required once a fractional scaled APR is configured. [2](#0-1) 

### Recommendation
Perform multiplication before division:

```solidity
return _amount * _apr * epochDuration / (100 * 365 days * ONE_TRANCHE_TOKEN);
```

Apply the same ordering in `ProgrammableBorrower._calcInterest`:

```solidity
return principal * borrowerApr * elapsed / (100 * YEAR * ONE_TRANCHE_TOKEN);
```

If intermediate multiplication overflow is a concern for large principals, use `Math.mulDiv` instead of restoring division-first arithmetic. [1](#0-0) [7](#0-6) 

### Proof of Concept
The following test can be added to `test/foundry/IdleCreditVault.t.sol`, which already provides the mainnet-forked `TestIdleCreditVault` deployment and helper accounts. [9](#0-8) 

```solidity
function testFractionalAprTruncationUnderpaysEpochInterest() external {
    uint256 principal = 10_000_000 * ONE_SCALE;
    uint256 apr = 1.99e18; // 1.99% APR, scaled by 1e18

    // Configure an exact one-year epoch with no buffer scaling.
    vm.prank(owner);
    cdoEpoch.setEpochParams(365 days, 0);

    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprsWithBuffer(apr, 365 days, 0);

    idleCDO.depositAA(principal);

    vm.prank(owner);
    cdoEpoch.startEpoch();

    uint256 actual = cdoEpoch.expectedEpochInterest();
    uint256 intended = principal * apr * 365 days / (100 * 365 days * 1e18);
    uint256 truncated = principal * (apr / 100) * 365 days / (365 days * 1e18);

    assertEq(actual, truncated);
    assertEq(actual, 100_000 * ONE_SCALE);      // truncated to 1%
    assertEq(intended, 199_000 * ONE_SCALE);    // intended 1.99%
    assertEq(intended - actual, 99_000 * ONE_SCALE);
}
```

The assertion that `expectedEpochInterest` equals `100_000 USDC` rather than `199_000 USDC` directly demonstrates the broken fair-interest invariant in the live `startEpoch` accounting path. [2](#0-1) [1](#0-0)

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L254-263)
```text
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

**File:** contracts/IdleCDOEpochVariant.sol (L357-364)
```text
    _interest = _resolveStopEpochInterest(_interest);

    // Base interest for stopEpoch: explicit override (>1) or precomputed expected epoch interest.
    uint256 _expectedInterest;
    // Strategy finalizes APR0 bucket state and returns adjusted stopEpoch values.
    (_expectedInterest, _pendingWithdrawFees) = _strategy.prepareStopEpochWithApr0(_interest);
    // Pending withdraws may be increased during APR0 settlement, so read after prepareStopEpochWithApr0.
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();
```

**File:** contracts/IdleCDOEpochVariant.sol (L542-546)
```text
  /// @notice Set the scaled apr for the next epoch
  /// @param _newApr New apr to set for the next epoch
  function _setScaledApr(uint256 _newApr) internal {
    IdleCreditVault(strategy).setAprsWithBuffer(_newApr, epochDuration, bufferPeriod);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L804-809)
```text
  /// @notice Calculate the interest of an epoch for the given amount and apr
  /// @param _amount Amount of underlyings
  /// @param _apr Apr used for the calculation
  function _calcInterestWithApr(uint256 _amount, uint256 _apr) internal view returns (uint256) {
    return _amount * (_apr / 100) * epochDuration / (365 days * ONE_TRANCHE_TOKEN);
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L188-195)
```text
  /// @notice Set the fixed APR charged on drawn borrower principal (100e18 = 100% APR).
  /// @dev Owner or manager. Checkpoints accrued interest at the old rate before applying the new APR.
  /// @param _apr borrower APR expressed as a percentage scaled by 1e18
  function setBorrowerApr(uint256 _apr) external {
    _checkOnlyOwnerOrManager();
    _accrueBorrowerInterest();
    borrowerApr = _apr;
    emit BorrowerAprSet(_apr);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L551-563)
```text
  /// @notice Compute uncheckpointed borrower interest since the last accrual timestamp.
  /// @dev `borrowerApr` is stored as a percentage scaled by `1e18`, so dividing by 100 converts
  /// it into a `1e18`-scaled rate fraction before prorating it over a year.
  function _pendingBorrowerInterest(uint256 principal, uint256 last) internal view returns (uint256) {
    if (principal == 0 || last == 0 || borrowerApr == 0) return 0;
    uint256 elapsed = block.timestamp - last;
    return elapsed == 0 ? 0 : _calcInterest(principal, elapsed);
  }

  /// @notice Compute simple time-based borrower interest for a principal over an elapsed period.
  function _calcInterest(uint256 principal, uint256 elapsed) internal view returns (uint256) {
    return principal * (borrowerApr / 100) * elapsed / (YEAR * ONE_TRANCHE_TOKEN);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L217-220)
```text
  function setAprsWithBuffer(uint256 _unscaledApr, uint256 _duration, uint256 _buffer) external {
    unscaledApr = _unscaledApr;
    setApr(_duration == 0 ? _unscaledApr : _unscaledApr * (_duration + _buffer) / _duration);
  }
```

**File:** test/foundry/IdleCreditVault.t.sol (L27-63)
```text
contract TestIdleCreditVault is TestIdleCDOLossMgmt {
  using stdStorage for StdStorage;

  uint256 internal constant FORK_BLOCK = 18678289;
  uint256 internal constant ONE_TRANCHE = 1e18;
  address internal constant USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;
  string internal constant borrowerName = 'testBorrower';
  address internal manager = makeAddr('manager');
  address internal borrower = makeAddr('borrower');

  address internal defaultUnderlying = USDC;
  IdleCDOEpochVariant internal cdoEpoch;
  IdleCDOCreditVault internal creditVaultBase;
  uint256 internal initialProvidedApr = 10e18;

  event AccrueInterest(uint256 interest, uint256 fees);
  event BorrowerDefault(uint256 payment);
  event Unpaused(address user);

  function setUp() public override {
    vm.createSelectFork("mainnet", FORK_BLOCK);
    super.setUp();
  }

  function _deployCDO() internal override returns (IdleCDO _cdo) {
    _cdo = IdleCDO(address(new IdleCDOEpochVariant()));
  }

  function _deployStrategy(address _owner) internal override returns (address _strategy, address _underlying) {
    _underlying = defaultUnderlying;
    strategy = new IdleCreditVault();
    strategyToken = IERC20Detailed(address(strategy));
    _strategy = address(strategy);

    stdstore.target(_strategy).sig(strategy.token.selector).checked_write(address(0));
    IdleCreditVault(_strategy).initialize(_underlying, _owner, manager, borrower, borrowerName, initialProvidedApr);
  }
```

**File:** test/foundry/IdleCreditVault.t.sol (L101-121)
```text
    uint256 epochDuration = 36.5 days;
    uint256 buffer = 5 days;
    // For testing let's support both tranches with AYS
    vm.startPrank(_owner);
    cdoEpoch.setBBDepositEnabled(true);
    cdoEpoch.setIsAYSActive(true);
    cdoEpoch.setInstantWithdrawParams(3 days, 1.5e18, false);
    cdoEpoch.setEpochParams(epochDuration, buffer); // set this to have an epoch during 1/10 of the year
    cdoEpoch.setKeyringParams(address(0), 0); // deactivate keyring
    vm.stopPrank();

    // we set the apr again manually for tests because we changed epoch params
    // scaled by the buffer period
    vm.startPrank(cv.manager());
    cv.setApr(_scaleAprWithBuffer(initialProvidedApr));
    vm.stopPrank();
  }

  function _scaleAprWithBuffer(uint256 _apr) internal view returns (uint256) {
    uint256 _duration = cdoEpoch.epochDuration();
    return _apr * (_duration + cdoEpoch.bufferPeriod()) / _duration;
```
