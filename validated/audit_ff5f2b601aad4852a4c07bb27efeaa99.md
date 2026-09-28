### Title
Unprotected ERC4626 deposit lets vault share-price inflation steal programmable-borrower principal - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
`ProgrammableBorrower._depositToVault` calls `vault.deposit(_assetAmount, address(this))` without a minimum-share bound or empty/first-depositor protection. When a programmable epoch starts, `onStartEpoch` deposits the full idle underlying balance through that unguarded path. A public ERC4626 vault user can first create a tiny share position, donate underlying to inflate the vault’s assets per share, and let the epoch-start deposit receive a rounded-down number of shares. The attacker then redeems their shares and extracts part of the borrower facility’s principal. This maps the injected `expiry_date` bug class to a caller-controlled numeric input—the external vault share price—being consumed directly by privileged pool accounting.

### Finding Description
During the buffer-to-running transition, `IdleCDOEpochVariant.startEpoch` transfers surplus pool funds to the configured programmable borrower and invokes `onStartEpoch`. `ProgrammableBorrower.onStartEpoch` snapshots the vault valuation, then deposits every idle underlying token through `_depositToVault` before recording the pre-deposit balance as the epoch principal baseline. The deposit path has no `minSharesOut`, solvency check, or requirement that the vault have a safe minimum share supply.

The vulnerable sequence is:

1. The borrower facility is in the buffer phase and holds `A` underlying that will be parked at epoch start.
2. The attacker, an ordinary user of the configured ERC4626 vault, deposits a dust amount to obtain shares.
3. The attacker donates `D` underlying directly to the vault, raising `convertToAssets(1 share)` from the normal rate to approximately `D`.
4. The honest manager calls `startEpoch`; `ProgrammableBorrower` calls `vault.deposit(A, address(this))` without a minimum-share check.
5. Integer division gives the facility at most `floor(A * S / (V + D))` shares, which can be only one share in a near-empty vault.
6. The attacker redeems their vault shares. The rounding loss stays inside the ERC4626 vault and is paid to the attacker’s redemption.

`ProgrammableBorrower` later measures the vault sleeve with `convertToAssets`; the missing assets become `vaultLoss()` and reduce `totalInterestDueNow()` at `stopEpoch`. Thus, this is not merely an external-vault issue: the protocol intentionally trusts the vault conversion result and has no boundary against user-controlled share-price inflation.

### Impact Explanation
The attacker can permanently steal a quantifiable portion of the facility’s principal. For a near-empty vault with one attacker share backed by `D + 1` assets, choosing `D + 1` slightly below `A / 2` makes a deposit of `A` mint one share. The vault then holds approximately `A + A / 2`, while attacker and borrower each own one share, letting the attacker recover roughly `A / 2` from an initial outlay of approximately `A / 2` and capture rounding value up to almost 25% of `A`. Larger configured vaults can still lose dust-sized or bounded rounding value, but the near-empty-vault configuration permits material theft.

The loss is ultimately borne by pool depositors because the programmable borrower reports the reduced vault position as epoch loss, while borrower principal accounting remains contractual and does not recreate the stolen assets.

### Likelihood Explanation
Likelihood is configuration-dependent rather than universal. The attack requires the configured ERC4626 vault to accept public deposits and donations and to have a sufficiently small or drainable share supply when `startEpoch` calls `deposit`. It does not require control of the owner, manager, borrower, IdleCDO, or queue; the malicious actor only needs to be an ordinary vault user and to act before the honest manager’s epoch-start transaction. Established high-liquidity vaults greatly reduce the profitable amount, but newly configured, drained, or low-TVL vaults are realistically exposed.

### Recommendation
Do not call ERC4626 `deposit` without an output bound. Compute the expected shares from `previewDeposit`, require a minimum configured share supply/TVL before using a vault, and pass a `minSharesOut` equivalent—or use a vault/router interface that supports slippage protection. If the interface remains plain ERC4626, calculate `minShares` locally, call `deposit`, compare the returned shares, and revert on under-minting. The vault should also be validated for donation resistance, such as virtual shares/assets, a minimum locked liquidity requirement, or a protocol-approved vault allowlist. Add regression tests covering first-depositor inflation and donation immediately before `onStartEpoch`.

### Proof of Concept
A Foundry PoC should fork the target deployment, use the configured `ProgrammableBorrower.vault()`, and execute the sequence as an ordinary vault user while keeping all privileged calls honest:

```solidity
function testStartEpochShareInflationTheft() external {
    // Fork fixture assumptions:
    // - cdoEpoch/programmableBorrower/vault/underlying come from the live deployment.
    // - pool is in buffer phase.
    // - vault accepts public deposits and has negligible totalSupply.
    // - attacker has no privileged role in Idle contracts.

    uint256 poolIdle = underlying.balanceOf(address(programmableBorrower));
    assertGt(poolIdle, 0);

    // Attacker owns one near-empty-vault share.
    vm.startPrank(attacker);
    underlying.approve(address(vault), type(uint256).max);
    vault.deposit(1, attacker);

    // Donate approximately half the pending pool deposit. This inflates assets/share
    // while still causing the pool deposit to mint only one share in a dust vault.
    uint256 donation = poolIdle / 2 - 2;
    underlying.transfer(address(vault), donation);
    vm.stopPrank();

    uint256 attackerCost = 1 + donation;
    uint256 attackerShares = vault.balanceOf(attacker);
    uint256 borrowerSharesBefore = vault.balanceOf(address(programmableBorrower));

    // Honest manager performs the normal buffer -> running transition.
    vm.prank(manager);
    cdoEpoch.startEpoch();

    uint256 borrowerShares = vault.balanceOf(address(programmableBorrower)) - borrowerSharesBefore;
    assertEq(borrowerShares, 1, "unguarded deposit was rounded down");

    // Attacker exits the public ERC4626 position.
    vm.prank(attacker);
    uint256 attackerAssets = vault.redeem(attackerShares, attacker, attacker);

    assertGt(attackerAssets, attackerCost, "attacker extracted pool principal");
    assertGt(programmableBorrower.vaultLoss(), 0, "pool records stolen assets as vault loss");
}
```

The exact rounding constants depend on the deployed vault’s decimals and virtual-share implementation; for a vault with nontrivial supply, the test should first reduce or select a configuration where `A * totalSupply / totalAssets` rounds materially below the economically correct share count.