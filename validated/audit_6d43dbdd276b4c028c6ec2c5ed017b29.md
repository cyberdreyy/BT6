### Title
Unprivileged ERC4626 vault user can block `onStopEpoch`/`stopEpoch` and the stuck vault cannot be removed while shares are held - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
Analogous to the Derby issue (a trusted, irremovable "Vault" whose responses are blindly trusted), `ProgrammableBorrower` treats its configured ERC4626 `vault` as a fully trusted accounting and liquidity source. `setVault` cannot detach a malfunctioning vault while `epochAccountingActive || vault.balanceOf(address(this)) != 0` (`ProgrammableBorrower.sol:158-165`), and `onStopEpoch` reverts (`StopEpochVaultLiquidityUnavailable`, `ProgrammableBorrower.sol:246-253`) whenever `vault.withdraw(shortfall)` reverts for the amount IdleCDO needs to pull. Any unprivileged user of the shared ERC4626 vault can deplete its liquid underlying (e.g., withdraw/borrow the vault's idle liquidity via a flash-funded position) right before the manager calls `stopEpoch`, making `vault.withdraw` revert while `shortfall <= _currentVaultAssets()` still holds (assets exist as shares but are illiquid). `stopEpoch` then always reverts, the epoch cannot be stopped or defaulted cleanly, and all queued lender withdrawals for that credit vault are frozen until vault liquidity returns — while the admin has no way to swap/remove the vault mid-position.

### Finding Description
- `ProgrammableBorrower.onStopEpoch` (`ProgrammableBorrower.sol:231-268`) computes `shortfall = _amountRequired - onHand`. If `shortfall > _currentVaultAssets()` it returns `true` and lets IdleCDO's `transferFrom` fail into the default path; but when `shortfall <= _currentVaultAssets()` (shares nominally cover it), it calls `vault.withdraw(shortfall, ...)` inside a try/catch that reverts with `StopEpochVaultLiquidityUnavailable` on failure (`ProgrammableBorrower.sol:245-253`). ERC4626 `withdraw` reverts when the vault's instantly redeemable liquidity is below the requested amount even though `convertToAssets(shares)` still reports full coverage — exactly the state a third-party vault user can create.
- The trust is non-revocable in this state: `setVault` reverts with `NotAllowed` while `epochAccountingActive` is true or `vault.balanceOf(address(this)) != 0` (`ProgrammableBorrower.sol:163-165`), mirroring Derby's "no `removeVault`" — the protocol cannot eject a malfunctioning/illiquid vault once funds are deposited.
- `epochAccountingActive` stays true because `onStopEpoch` reverts before reaching `epochAccountingActive = false` (`ProgrammableBorrower.sol:265`), so every retry of `stopEpoch` hits the same revert; `borrow`/`repay` also require `epochAccountingActive` and interact with the same vault.

### Impact Explanation
Temporary freezing of all lender funds in the affected credit vault. While `stopEpoch` cannot execute, no withdraw requests can be processed/claimed for that epoch (withdrawals are settled only at epoch stop), the vault position cannot be unwound through the normal path, and `setVault`/`emergencyExitVault` cannot detach the vault while shares exist. The attacker's cost is limited to the funding needed to drain the external vault's free liquidity for the duration of the freeze (e.g., Morpho borrow interest or opportunity cost on a large share position), versus freezing the entire pool's TVL. Repeatable each time liquidity returns, extending the freeze.

### Likelihood Explanation
Medium. It requires (a) a credit vault configured in programmable-borrower mode against a shared ERC4626 vault, and (b) the attacker being able to reduce that vault's immediately withdrawable liquidity below `shortfall` at `stopEpoch` time — feasible for any Morpho-style vault where liquidity is publicly borrowable/redeemable, and the attacker can time it by watching the mempool for `stopEpoch`. No privileged role needed. The condition `shortfall <= _currentVaultAssets()` is the common case (funds are in the vault, just illiquid). Caveat I could not fully verify within the iteration budget: the exact behavior of `IdleCDOEpochVariant.stopEpoch` when `onStopEpoch` reverts (whether it has a fallback path that bypasses the hook); from the code read, the revert propagates and aborts the stop, which is the basis of this finding.

### Recommendation
- In `onStopEpoch`, do not revert when `vault.withdraw` fails for a liquidity (not solvency) reason: catch the failure, set `epochAccountingActive = false`, cap the reported pull amount to `onHand`, and return `true` so IdleCDO can settle with what is available and route the shortfall through the existing loss/default accounting instead of freezing.
- Alternatively/additionally, add an owner/guardian escape that forcibly clears `epochAccountingActive` and allows `setVault` (or `emergencyExitVault` of last resort) when `stopEpoch` has been blocked by a vault withdrawal failure — i.e., a "remove malfunctioning vault" mechanism, the same fix recommended in the external report.

### Proof of Concept
Foundry fork test (mainnet, programmatic-borrower pool over a shared ERC4626 vault such as a Morpho vault):

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCDOEpochVariant} from "../contracts/IdleCDOEpochVariant.sol";
import {ProgrammableBorrower} from "../contracts/strategies/idle/ProgrammableBorrower.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";
import {IERC4626} from "../contracts/interfaces/IERC4626.sol";

contract VaultFreezePoC is Test {
    IdleCDOEpochVariant cdo;
    ProgrammableBorrower pb;
    IERC4626 extVault;      // shared external ERC4626 (e.g. Morpho vault)
    IERC20Detailed usdc;
    address manager = /* manager */;
    address attacker = makeAddr("attacker");

    function test_attackerFreezesStopEpochByDrainingVaultLiquidity() external {
        // setup: AA deposit, startEpoch -> pb deposits pool assets into extVault
        // ...

        uint256 shortfallNeeded = /* amountRequired - onHand at stop */;
        // Ensure shares nominally cover the shortfall
        assertLe(shortfallNeeded, extVault.convertToAssets(extVault.balanceOf(address(pb))));

        // Attacker (plain vault user) drains extVault liquid underlying below `shortfallNeeded`:
        // e.g. borrow/redeem so that extVault liquidity < shortfallNeeded.
        vm.startPrank(attacker);
        // usdc.flashLoan/deposit collateral -> borrow idle liquidity from extVault markets
        vm.stopPrank();
        assertLt(usdc.balanceOf(address(extVault)), shortfallNeeded);

        // Manager attempts to stop the epoch: onStopEpoch -> vault.withdraw reverts
        // -> StopEpochVaultLiquidityUnavailable -> stopEpoch reverts
        vm.prank(manager);
        vm.expectRevert(); // StopEpochVaultLiquidityUnavailable bubbled through stopEpoch
        cdo.stopEpoch(0, 0);

        // Admin cannot remove the malfunctioning vault mid-epoch:
        vm.prank(/* owner */);
        vm.expectRevert(); // NotAllowed: vault.balanceOf(pb) != 0 && epochAccountingActive
        pb.setVault(address(0xBEEF));

        // Epoch still running: lender withdraw requests remain unprocessable -> funds frozen
        assertTrue(cdo.isEpochRunning());
    }
}
```

Key assertion chain: `convertToAssets(shares) >= shortfall` (solvency OK) while `underlying.balanceOf(vault) < shortfall` (liquidity exhausted) ⇒ `vault.withdraw` reverts ⇒ `onStopEpoch` reverts ⇒ `stopEpoch`/`setVault` unusable ⇒ lender withdrawals frozen until the attacker restores liquidity.