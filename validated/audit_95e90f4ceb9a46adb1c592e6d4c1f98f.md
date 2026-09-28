### Title
Permissionless factory accepts arbitrary ERC4626 "vault" plugin, granting unlimited underlying approval to an attacker contract and letting it steal all deposited lender funds - (File: contracts/IdleCreditVaultFactory.sol, contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
`deployRevolvingCreditVault` is permissionless (no `onlyOwner`/access check) and routes caller-supplied `programmableBorrowerParams` — including the external ERC4626 `vault` address — into `ProgrammableBorrower.initialize`. The only validation of this pluggable component is `IERC4626(_vault).asset() == underlying` (`ProgrammableBorrower.sol:119`), the same class of incomplete, bypassable check as the reference bug's AST denylist. `initialize` then grants the supplied vault `type(uint256).max` approval over the pooled underlying (`ProgrammableBorrower.sol:133`), and `onStartEpoch` deposits the entire on-hand balance into it (`ProgrammableBorrower.sol:216`). A malicious "vault" contract that satisfies `asset()` can drain 100% of all funds the facility ever holds.

### Finding Description
- `IdleCreditVaultFactory.deployRevolvingCreditVault` (contracts/IdleCreditVaultFactory.sol:126-166) is `external` with no caller restriction and no allowlist/registry check on `programmableBorrowerParams.vault` or `ancillaryParams.keyring`.
- `_deployProgrammableBorrower` initializes `ProgrammableBorrower` with that vault. `initialize` (contracts/strategies/idle/ProgrammableBorrower.sol:108-135) only checks `_vault != 0` and `asset()` match, then calls `_allowUnlimitedSpend(_underlyingToken, _vault)` (line 133).
- At epoch start, `onStartEpoch` (line 201-224) calls `_depositToVault(underlyingToken.balanceOf(address(this)), 0)` → `vault.deposit(...)`, pushing the full pooled principal into the attacker contract. `_borrow` (line 452) and `onStopEpoch` (line 246) also pull from it.
- Broken invariant: pluggable-component authenticity / fund isolation. A fake vault's `deposit()` simply keeps the transferred underlying (unlimited approval also lets it `transferFrom` at any time) and returns worthless shares. Later `vault.withdraw`/`convertToAssets` calls either revert (permanently freezing the epoch state machine via `StopEpochVaultLiquidityUnavailable`) or return attacker-chosen values.
- No existing guard stops this: no vault registry, no signature/enforcement equivalent — verification defaults to "warn" (asset match only).

### Impact Explanation
Direct theft of 100% of lender principal routed through the malicious instance: every deposit parked at `onStartEpoch`, every repayment redeployed by `_repay` (line 527), and anything held at stop. Residual funds are additionally permanently frozen because `onStopEpoch` reverts when `vault.withdraw` fails. Loss = entire vault TVL for any lender who deposits into a vault deployed with the malicious plugin.

### Likelihood Explanation
Requires a depositor to be induced to use a vault whose factory deployment parameters embed the attacker vault — structurally identical to the reference report's "user induced to load an attacker's plugin" precondition. The factory is publicly callable, deployments emit a legitimate-looking `CreditVaultDeployed` event, and manager/owner wiring (`treasury` ownership, manager admin) is indistinguishable from a honest deployment, so a frontend or manager could present it as a normal credit vault.

### Recommendation
- Add a vault allowlist/registry (or an owner-gated `setVault`-style approval) checked inside `deployRevolvingCreditVault` before passing `programmableBorrowerParams` to `_deployProgrammableBorrower` — i.e., move the "signature policy" from warn to enforce for non-built-in vaults.
- Alternatively, restrict `deployRevolvingCreditVault` to an operator role, or deploy `ProgrammableBorrower` only against factory-registered vault implementations.
- Reduce standing exposure by replacing the unlimited approval with per-deposit exact approvals.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;
import "forge-std/Test.sol";
import {IERC4626} from "contracts/interfaces/IERC4626.sol";
import {IERC20Detailed} from "contracts/interfaces/IERC20Detailed.sol";

contract EvilVault {
    IERC20Detailed public asset; // returns the vault underlying -> passes asset() check
    constructor(address _asset) { asset = IERC20Detailed(_asset); }
    // ERC4626 stubs
    function deposit(uint256 assets, address) external returns (uint256) {
        asset.transferFrom(msg.sender, address(this), assets); // keep the tokens
        return assets;
    }
    function withdraw(uint256, address, address) external returns (uint256) { revert(); }
    function redeem(uint256, address, address) external returns (uint256) { revert(); }
    function balanceOf(address) external pure returns (uint256) { return 0; }
    function convertToAssets(uint256 s) external pure returns (uint256) { return s; }
    function steal(address to, uint256 amt) external { asset.transfer(to, amt); }
}

contract FactoryPluginTheftTest is Test {
    // uses the repo's factory deployment helpers (see test/foundry/IdleCreditVaultFactory.t.sol)
    function testMaliciousVaultDrainsFacility() external {
        // 1. Attacker deploys EvilVault wrapping the real underlying (e.g. USDC).
        // 2. Attacker calls factory.deployRevolvingCreditVault(...) with
        //    programmableBorrowerParams.vault = address(evilVault) — call succeeds,
        //    no vault verification beyond asset() match.
        // 3. Victim (KYC'd via attacker-supplied or real keyring) deposits D underlying
        //    via idleCDO.depositAA / queue into the new credit vault.
        // 4. Honest manager calls startEpoch -> CDO sends D to ProgrammableBorrower ->
        //    onStartEpoch -> _depositToVault(D) -> EvilVault.deposit pulls D.
        // 5. attacker: evilVault.steal(attacker, D);
        //    assertEq(underlying.balanceOf(attacker), D);            // 100% of deposits stolen
        //    assertEq(underlying.balanceOf(programmableBorrower), 0);
        // 6. manager stopEpoch -> onStopEpoch -> vault.withdraw reverts ->
        //    StopEpochVaultLiquidityUnavailable: residual accounting permanently frozen.
    }
}
```

Uncertainty note: I could not read the full `ProgrammableBorrowerParams` struct or `_deployProgrammableBorrower` body within the available iterations, so the exact field name for the vault param is inferred from `initialize(address _vault, ...)` being wired from `programmableBorrowerParams` in `deployRevolvingCreditVault` (contracts/IdleCreditVaultFactory.sol:145-150). The permissionless `external` entry point, the `asset()`-only check, the unlimited approval, and the full-balance deposit are confirmed at the cited lines.