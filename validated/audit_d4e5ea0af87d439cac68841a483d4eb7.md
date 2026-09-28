### Title
Permissionless `initialize` on `IdleCreditVaultFactory` can be front-run, giving an attacker `treasury`/`proxyAdmin` and ownership of every subsequently deployed credit vault - (File: contracts/IdleCreditVaultFactory.sol)

### Summary
`IdleCreditVaultFactory.initialize(address _treasury, address _proxyAdmin)` is an unpermissioned `initializer` on an upgradeable contract. The two roles it sets are the most powerful in the deployment pipeline: `treasury` receives `transferOwnership` of every deployed `IdleCDOEpochVariant`, `IdleCreditVault` strategy, `IdleCDOEpochQueue` and `ProgrammableBorrower`, and `proxyAdmin` is baked into every `TransparentUpgradeableProxy` the factory creates. If the factory proxy is deployed in a transaction separate from its initialization, an unprivileged attacker can front-run `initialize`, seize `treasury` and `proxyAdmin`, and thereby own and be able to upgrade every vault deployed through the factory afterward.

### Finding Description
The initializer has no access control and no deployer binding:

```solidity
// contracts/IdleCreditVaultFactory.sol
function initialize(address _treasury, address _proxyAdmin) external initializer {
  if (_treasury == address(0) || _proxyAdmin == address(0)) revert Is0();
  treasury = _treasury;
  proxyAdmin = _proxyAdmin;
  _setFeeSplit(DEFAULT_FEE_SPLIT);
}
``` [1](#0-0) 

The consequences of controlling these two slots are severe because the deployment flows are themselves permissionless (`deployCreditVault` / `deployRevolvingCreditVault` are `external` with no caller check) and funnel ownership to `treasury`:

```solidity
// contracts/IdleCreditVaultFactory.sol
strategy.transferOwnership(treasury);
cv.transferOwnership(treasury);
``` [2](#0-1) [3](#0-2) 

and every proxy is created with the factory's `proxyAdmin`:

```solidity
// contracts/IdleCreditVaultFactory.sol
return address(new TransparentUpgradeableProxy(implementation, proxyAdmin, data));
``` [4](#0-3) 

Because `queue.transferOwnership(treasury)` and `programmableBorrower.transferOwnership(treasury)` also execute inside the same flows (`_deployQueue`, `_deployProgrammableBorrower`), a single front-run `initialize` hands the attacker the owner and the upgrade admin of the whole vault stack.

Note the other in-scope contracts do not share this exposure: `IdleCreditVaultWriteOffEscrow` and `ProgrammableBorrower` call `_disableInitializers()` in their constructors and are always atomically initialized inside `_deployProxy`, and `KeyringIdleWhitelist` is constructor-based with `admin` set at deployment. The factory is the only contract whose `initialize` sets a role (`treasury`) that later receives ownership of funds-bearing contracts plus a `proxyAdmin` with upgrade power.

### Impact Explanation
Broken invariant: access control over ownership and upgrades of the credit-vault stack.

- `treasury` = attacker → any later `deployCreditVault`/`deployRevolvingCreditVault` call (permissionless, so no cooperation needed; the normal ops flow suffices) produces an `IdleCDOEpochVariant`, `IdleCreditVault` strategy, queue and `ProgrammableBorrower` all owned by the attacker.
- `proxyAdmin` = attacker → the attacker can upgrade the vault/strategy proxies deployed by the factory to a malicious implementation and drain all deposited underlying (`_depositToVault` / vault balances / pending withdraw reserves).
- `setFeeSplit` is gated by `msg.sender != treasury`, so the attacker also controls fee split for all deployments.

Loss: up to 100% of TVL in vaults deployed through a hijacked factory, plus theft of unclaimed yield and write-off escrow fees — a direct-theft impact, not a DoS.

### Likelihood Explanation
Conditional but realistic: the bug materializes whenever the factory proxy is deployed without initialization in the same transaction (a common deploy-script split: deploy implementation, deploy proxy, call `initialize`). The Nouns Builder `Manager.initialize` report this maps to was accepted at Medium for the same pattern. Within the repo, nothing prevents it — `initialize` is `external initializer` with no `onlyOwner`/deployer check, and `deployCreditVault` requires no privilege, so post-hijack exploitation needs only ordinary factory usage. Likelihood is bounded by deployment hygiene rather than by any on-chain guard.

### Recommendation
- Initialize the factory atomically in the same transaction as proxy deployment (e.g., via a `deployProxy`-style helper that passes `initialize` calldata to the `TransparentUpgradeableProxy` constructor, exactly as `_deployProxy` already does for the vaults it creates).
- Optionally bind initialization to the deployer (`if (msg.sender != DEPLOYER) revert`) or hardcode the initial `treasury`/`proxyAdmin`, and emit the implementation with `_disableInitializers()` already set (currently done in the constructor, which is correct — keep it).

### Proof of Concept
Foundry test (fork or local) sketch:

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import "../contracts/IdleCreditVaultFactory.sol";
import "@openzeppelin/contracts/proxy/transparent/TransparentUpgradeableProxy.sol";

contract FactoryInitFrontrunTest is Test {
    IdleCreditVaultFactory impl;
    IdleCreditVaultFactory factory;
    address proxyAdmin = makeAddr("proxyAdmin");
    address treasury = makeAddr("treasury");
    address attacker = makeAddr("attacker");

    function setUp() public {
        impl = new IdleCreditVaultFactory();
        // Deployment step 1: proxy deployed WITHOUT init data (split deployment)
        factory = IdleCreditVaultFactory(
            address(new TransparentUpgradeableProxy(address(impl), proxyAdmin, ""))
        );
        // Step 2: attacker front-runs the planned initialize(treasury, proxyAdmin)
        vm.prank(attacker);
        factory.initialize(attacker, attacker);
        // Honest init now reverts (already initialized)
        vm.expectRevert();
        factory.initialize(treasury, proxyAdmin);
    }

    function testAttackerOwnsDeployedVaults() public {
        // credit vault impl + strategy impl mocks/real deployments
        IdleCreditVaultFactory.CreditVaultParams memory cvp = ...; // valid params
        IdleCreditVaultFactory.StrategyData memory sd = ...;
        IdleCreditVaultFactory.AncillaryParams memory ap = ...;

        // anyone can call deployCreditVault; ownership goes to treasury == attacker
        factory.deployCreditVault(cvp, sd, ap);

        // assert: cv.owner() == attacker, strategy.owner() == attacker,
        // and TransparentUpgradeableProxy admin of deployed proxies == attacker,
        // enabling upgrade to a malicious impl that sweeps `token` balances.
    }
}
```

Key assertion: after the front-run, every contract emitted in `CreditVaultDeployed` has `owner() == attacker` and proxy admin `attacker`, so the attacker can upgrade the credit vault proxy and call arbitrary storage writes / `safeTransfer` of the underlying — draining deposits, pending withdraw reserves and escrowed write-off tranches.

Caveat on scope: the finding depends on the factory being deployed behind an uninitialized proxy at some point in the pipeline; the atomicity gap is in the deployment layer, not enforced on-chain. This mirrors the accepted Nouns `Manager.initialize` issue exactly (unpermissioned initializer handing over an upgrade-capable role), so it is the faithful analog — all other initializers in the repo (`IdleCreditVault`, `IdleCDOCreditVault`, `ProgrammableBorrower`, `IdleCreditVaultWriteOffEscrow`, `IdleCDOEpochQueue`) are either atomically initialized inside `_deployProxy` or disabled in the implementation constructor, leaving `IdleCreditVaultFactory` as the exposed surface.

### Citations

**File:** contracts/IdleCreditVaultFactory.sol (L45-50)
```text
  function initialize(address _treasury, address _proxyAdmin) external initializer {
    if (_treasury == address(0) || _proxyAdmin == address(0)) revert Is0();
    treasury = _treasury;
    proxyAdmin = _proxyAdmin;
    _setFeeSplit(DEFAULT_FEE_SPLIT);
  }
```

**File:** contracts/IdleCreditVaultFactory.sol (L113-114)
```text
    strategy.transferOwnership(treasury);
    cv.transferOwnership(treasury);
```

**File:** contracts/IdleCreditVaultFactory.sol (L155-156)
```text
    strategy.transferOwnership(treasury);
    cv.transferOwnership(treasury);
```

**File:** contracts/IdleCreditVaultFactory.sol (L280-286)
```text
  function _deployProxy(address implementation, bytes memory data) internal returns (address) {
    return address(new TransparentUpgradeableProxy(
      implementation,
      proxyAdmin,
      data
    ));
  }
```
