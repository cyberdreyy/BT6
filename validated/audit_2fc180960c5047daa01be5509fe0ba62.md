### Title
Uninitialized `idleCDO` allows permissionless APR poisoning before strategy binding - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary

`IdleCreditVault.setApr` disables its caller check whenever `idleCDO == address(0)`. While this is intended for setup, the contract does not restrict the setup call to the initializer, owner, or manager. If an `IdleCreditVault` is initialized and its CDO initialization or `setWhitelistedCDO` binding occurs in a later transaction, any unprivileged account can poison `lastApr` before the boundary is established. Once linked, `IdleCDOEpochVariant.startEpoch` prices the epoch from that poisoned APR and can force an unjustified borrower default, freezing LP principal and pending withdrawals. [1](#0-0) [2](#0-1) 

### Finding Description

The vulnerable guard is conditional rather than fail-closed:

```solidity
if (_cdo != address(0)) {
  if (msg.sender != _cdo && msg.sender != manager) revert NotAllowed();
}
```

When `idleCDO` has not yet been configured, the entire authorization check is skipped, leaving only the `maxApr` bound. [1](#0-0) 

The boundary can be installed later through the owner-only `setWhitelistedCDO`, so a non-atomic deployment creates a permissionless configuration window. [2](#0-1) 

`IdleCDOEpochVariant.startEpoch` calculates `expectedEpochInterest` from `getContractValue()` and the strategy's stored APR, then sends the surplus underlying to the borrower. [3](#0-2)  Interest is calculated as principal multiplied by the stored scaled APR and epoch duration. [4](#0-3) 

If the poisoned obligation is not covered at epoch stop, the catch path invokes `_handleBorrowerDefault`, marks the vault defaulted, pauses deposits, stops the epoch, and disables both withdrawal-request paths. [5](#0-4) 

### Impact Explanation

An attacker can set `lastApr` to `maxApr`, normally `20e18`, before the CDO boundary exists. For the default 30-day epoch, this creates approximately `1.6438%` of active principal as required epoch interest:

```text
interest = TVL * (20e18 / 100) * 30 days / 365 days / 1e18
         ≈ 0.016438 * TVL
```

For example, a vault with 10,000,000 USDC would demand approximately 164,384 USDC of epoch interest. If the borrower has funded or approved only the principal plus the operator's intended lower interest, `getFundsFromBorrower` fails and the CDO enters the borrower-default state. [6](#0-5) [5](#0-4) 

The resulting invariant violation is an attacker-created credit obligation outside the agreed configured APR. It can temporarily freeze all active LP funds and pending receipts until default recovery is finalized, and it can convert an otherwise repayable facility into a hard default.

### Likelihood Explanation

Exploitation requires a deployment or upgrade flow where `IdleCreditVault.initialize`, `IdleCDOEpochVariant.initialize`, and `setWhitelistedCDO` are not atomic, and where operators do not explicitly reset the APR after binding the CDO. The public `setWhitelistedCDO` method permits this non-atomic setup pattern. [2](#0-1) 

No privileged attacker is required: the poisoned `setApr` transaction can originate from any EOA during the uninitialized-boundary window. If the production factory always initializes, binds, and configures the strategy atomically, practical exploitability is reduced to manual deployments, upgrades, and delayed-binding integrations.

### Recommendation

Fail closed while `idleCDO` is unset:

```solidity
function setApr(uint256 _apr) public {
  address _cdo = idleCDO;
  if (_cdo == address(0)) revert NotAllowed();
  if (msg.sender != _cdo && msg.sender != manager) revert NotAllowed();

  uint256 _maxApr = maxApr;
  if (_maxApr != 0 && _apr > _maxApr) revert NotAllowed();
  lastApr = _apr;
}
```

If setup-time APR configuration is required, expose a separate owner- or manager-only initialization method that cannot be called after `idleCDO` is configured. The deployment flow should also bind `idleCDO` and configure APR atomically, and regression tests should verify that `setApr`, `setAprs`, and `setAprsWithBuffer` revert from an arbitrary account both before and after `setWhitelistedCDO`.

### Proof of Concept

A Foundry fork PoC can reproduce the sequence using the existing `IdleCreditVault` and `IdleCDOEpochVariant` deployment fixture:

```solidity
function testUnboundStrategyAllowsAprPoisoningAndDefault() public {
  // Arrange: initialize the strategy and CDO, but leave strategy.idleCDO unset.
  // This state is possible because setWhitelistedCDO is a separate transaction.
  IdleCreditVault strategy = IdleCreditVault(cdoEpoch.strategy());
  uint256 epochDuration = cdoEpoch.epochDuration();

  // Assume the intended configured annual APR is 5%.
  uint256 intendedApr = 5e18;
  uint256 tvl = 10_000_000 * ONE_SCALE;
  uint256 intendedInterest = _calcInterest(tvl, intendedApr, epochDuration);

  // Before the strategy is bound to the CDO, an arbitrary EOA reaches setApr.
  address attacker = makeAddr("attacker");
  vm.prank(attacker);
  strategy.setApr(strategy.maxApr());
  assertEq(strategy.lastApr(), strategy.maxApr());

  // Honest owner installs the advertised boundary only now.
  vm.prank(owner);
  strategy.setWhitelistedCDO(address(cdoEpoch));

  // Honest KYC-passing lenders deposit into the buffer epoch.
  _depositWithUser(makeAddr("lp1"), tvl / 2, true);
  _depositWithUser(makeAddr("lp2"), tvl / 2, true);

  // Honest manager starts the epoch. IdleCDO prices it from the poisoned APR.
  vm.prank(manager);
  cdoEpoch.startEpoch();

  uint256 poisonedInterest = cdoEpoch.expectedEpochInterest();
  assertGt(poisonedInterest, intendedInterest);

  // Borrower supplies enough for principal plus intended interest, but not the
  // attacker-added interest. Therefore the CDO's transferFrom fails.
  uint256 borrowerFunding = tvl + intendedInterest;
  deal(defaultUnderlying, borrower, borrowerFunding);

  vm.warp(cdoEpoch.epochEndDate() + 1);
  vm.prank(manager);
  cdoEpoch.stopEpoch(0, 0);

  // Failure to pull the poisoned obligation is treated as borrower default.
  assertTrue(cdoEpoch.defaulted());
  assertTrue(cdoEpoch.paused());
  assertFalse(cdoEpoch.allowAAWithdrawRequest());
  assertFalse(cdoEpoch.allowBBWithdrawRequest());
}
```

The critical assertion is that `attacker` successfully changes `strategy.lastApr()` while `strategy.idleCDO()` is unset, after which the otherwise honest start-and-stop epoch sequence produces `defaulted == true`.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L225-234)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L954-956)
```text
  function setWhitelistedCDO(address _cdo) external onlyOwner {
    require(_cdo != address(0), "IS_0");
    idleCDO = _cdo;
```

**File:** contracts/IdleCDOEpochVariant.sol (L260-274)
```text
    int256 adjustedActiveInterest = int256(_calcInterest(getContractValue())) + interestForOverUnderPerformance;
    if (adjustedActiveInterest < 0) adjustedActiveInterest = 0;
    expectedEpochInterest = pendingWithdrawFees + uint256(adjustedActiveInterest);
    interestForOverUnderPerformance = 0;

    // set expected epoch end date
    epochEndDate = block.timestamp + _epochDuration;
    // set instant withdraw deadline
    instantWithdrawDeadline = block.timestamp + instantWithdrawDelay;

    // transfer in this contract funds from interest payment (if any) and buffer deposits sent to the strategy
    uint256 _totEpochDeposits = _strategy.totEpochDeposits();
    // If interest is minted we do not transfer interest to the strategy
    uint256 _toSend = isInterestMinted ? _totEpochDeposits : lastEpochInterest + _totEpochDeposits;
    _strategy.sendInterestAndDeposits(_toSend);
```

**File:** contracts/IdleCDOEpochVariant.sol (L550-573)
```text
  function getFundsFromBorrower(uint256 _amount) external {
    _checkNotAllowed(msg.sender != address(this));
    _transferUnderlyingsFrom(_borrower(), address(this), _amount);
  }

  /// @notice Get funds from borrower to fullfill instant withdraw requests
  /// Manager should call this method after instantWithdrawDeadline (when epoch is running)
  /// @dev Instant withdrawals are not supported when a programmable borrower is configured.
  function getInstantWithdrawFunds() external {
    _checkOnlyOwnerOrManager();
    // Check that programmable mode is disabled, the epoch is running and the deadline passed.
    _checkNotAllowed(isProgrammableBorrower || !isEpochRunning || block.timestamp < instantWithdrawDeadline);

    IdleCreditVault _strategy = IdleCreditVault(strategy);
    uint256 _instantWithdraws = _pendingInstant();
    // transfer funds for instant withdraw to this contract
    try this.getFundsFromBorrower(_instantWithdraws) {
      // transfer funds to IdleCreditVault and decrease pendingInstantWithdraws
      _strategy.collectInstantWithdrawFunds(_instantWithdraws);
      // allow instant withdraws
      allowInstantWithdraw = true;
    } catch {
      _handleBorrowerDefault(_instantWithdraws);
    }
```

**File:** contracts/IdleCDOEpochVariant.sol (L577-599)
```text
  function _handleBorrowerDefault(uint256 funds) internal {
    defaulted = true;
    // Do not reopen instant claims here. They remain disabled when funding is pending;
    // successful full funding is the only path that enables them before finalization.

    if (isProgrammableBorrower) {
      IProgrammableBorrower(_borrower()).onDefault();
    }

    // deposits should be already prevented
    if (!paused()) {
      _pause();
    }

    // stop the current epoch
    isEpochRunning = false;

    // prevent withdrawals requests
    allowAAWithdrawRequest = false;
    allowBBWithdrawRequest = false;

    emit BorrowerDefault(funds);
  }
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
