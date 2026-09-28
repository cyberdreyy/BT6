### Title
ERC4626 liquidity drain prevents programmable-borrower epoch settlement and freezes LP funds - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
`ProgrammableBorrower.onStopEpoch` treats an ERC4626 withdrawal failure as a hard revert. An unprivileged user of the configured external vault can temporarily remove its available underlying liquidity—for example, by borrowing all available assets from the Morpho markets backing a MetaMorpho vault—before the manager calls `IdleCDOEpochVariant.stopEpoch`. Because `IdleCDOEpochVariant` invokes the hook directly rather than inside the borrower-funding `try/catch`, the revert propagates and the epoch remains running. Repeating this liquidity drain each time liquidity returns keeps tranche funds frozen.

### Finding Description
During programmable-borrower settlement, `IdleCDOEpochVariant._stopEpoch` calls `IProgrammableBorrower.onStopEpoch` before pulling borrower funds. The hook computes a cash shortfall, compares it with `convertToAssets`, and then calls `vault.withdraw`. `convertToAssets` measures the programmable borrower's claim on the vault, not the vault's currently withdrawable liquidity. An ERC4626 lending vault can therefore report sufficient assets while having insufficient idle liquidity.

If `vault.withdraw` reverts, `onStopEpoch` reverts with `StopEpochVaultLiquidityUnavailable`. The surrounding CDO call is not wrapped in the `try this.getFundsFromBorrower(...)` block, so no default handling, epoch-state transition, or fallback path executes. `emergencyExitVault` has the same dependency on external vault liquidity and cannot reliably recover while the vault is drained.

### Impact Explanation
All tranche-holder principal represented by programmable-borrower vault shares can be temporarily frozen for as long as the attacker maintains low external-vault liquidity. Withdrawal requests cannot be funded because the epoch cannot stop. The impact scales to the full programmable-borrower vault position; in a deployment holding 10,000,000 USDC, essentially the entire invested balance is unavailable until vault liquidity is replenished or the vault position can otherwise be unwound.

This is not merely a gas or availability nuisance: the affected state transition is the sole epoch-settlement path for programmable-borrower funds, and normal LP withdrawals are epoch-gated.

### Likelihood Explanation
The attacker needs no privileged Idle role. Any external-vault user capable of reducing available underlying liquidity—such as a sufficiently collateralized borrower in the Morpho markets used by a MetaMorpho vault—can trigger the condition immediately before `stopEpoch`. Public mempool visibility makes the settlement transaction predictable. Maintaining the condition requires keeping the external liquidity borrowed or withdrawn, so the freeze is temporary rather than guaranteed permanent.

### Recommendation
Use the ERC4626 liquidity limit rather than economic share value when recalling funds:

- Cap the requested withdrawal with `vault.maxWithdraw(address(this))` or use `redeem` bounded by `maxRedeem`.
- Treat partial liquidity as a real shortfall and route it into an explicit settlement/default path rather than reverting indefinitely.
- Alternatively, allow `stopEpoch` to record the shortfall and enter the existing default/loss workflow when the vault cannot satisfy the required withdrawal.
- Ensure owner/manager emergency exits also use `maxWithdraw`/`maxRedeem` and cannot be blocked solely by temporary ERC4626 illiquidity.

### Proof of Concept
The fork PoC uses the existing programmable-borrower/MetaMorpho test setup and drains the backing Morpho liquidity before the manager calls `stopEpoch`.

```solidity
// test/foundry/ProgrammableBorrowerCreditVaultLiquidity.t.sol
// SPDX-License-Identifier: AGPL-3.0
pragma solidity 0.8.10;

import "./ProgrammableBorrowerCreditVault.t.sol";

contract ProgrammableBorrowerStopEpochLiquidityPoC is ProgrammableBorrowerCreditVaultTest {
    function testExternalVaultLiquidityDrainBlocksStopEpoch() external {
        uint256 depositAmount = 10_000 * oneScale;
        uint256 attackerCollateral = 20 ether;

        // Fund the programmable facility and start a normal APR=0 epoch.
        vm.prank(owner);
        cdoEpoch.setIsInterestMinted(true);
        idleCDO.depositAA(depositAmount);
        _startEpochAndCheckPrices(0);

        // The programmable borrower parks idle capital in the ERC4626 vault.
        uint256 vaultShares = morphoVault.balanceOf(address(programmableBorrower));
        uint256 vaultClaim = morphoVault.convertToAssets(vaultShares);
        assertGt(vaultClaim, 0);

        // Unprivileged external-market action: borrow/remove the underlying
        // liquidity backing the vault so maxWithdraw is below the required recall.
        _drainBackingMorphoLiquidity(attackerCollateral);
        assertLt(morphoVault.maxWithdraw(address(programmableBorrower)), vaultClaim);

        // Even though convertToAssets still reports a full economic claim,
        // the ERC4626 withdrawal cannot be satisfied and stopEpoch reverts.
        vm.warp(cdoEpoch.epochEndDate() + 1);
        vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
        vm.prank(manager);
        cdoEpoch.stopEpoch(0, 0);

        assertTrue(cdoEpoch.isEpochRunning(), "epoch remains stuck running");

        // A retry while the attacker maintains drained liquidity reverts again.
        vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
        vm.prank(manager);
        cdoEpoch.stopEpoch(0, 0);
    }
}
```

`_drainBackingMorphoLiquidity` should borrow or withdraw all currently idle USDC from the Morpho markets allocated to the configured MetaMorpho vault, using attacker-supplied collateral. The decisive assertion is `morphoVault.maxWithdraw(address(programmableBorrower)) < required`, while `convertToAssets(vaultShares)` remains sufficient: the accounting check in `onStopEpoch` passes, but the actual ERC4626 withdrawal reverts.