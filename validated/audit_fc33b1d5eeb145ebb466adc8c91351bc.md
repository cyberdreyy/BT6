### Title
External ERC4626 liquidity withdrawal can indefinitely block `stopEpoch` - (File: `contracts/strategies/idle/ProgrammableBorrower.sol`)

### Summary
In programmable-borrower mode, an unprivileged holder or borrower of the configured ERC4626 vault can withdraw the vault’s available liquidity immediately before an honest `stopEpoch` call. `ProgrammableBorrower.onStopEpoch()` catches the failed withdrawal and reverts with `StopEpochVaultLiquidityUnavailable`, which propagates through `IdleCDOEpochVariant._stopEpoch()` instead of entering the default path. The epoch consequently remains running, the CDO remains paused, deposits and withdrawal requests remain disabled, and matured withdrawal receipts cannot be funded until third-party vault liquidity returns.

### Finding Description
During `stopEpoch`, `IdleCDOEpochVariant` calls `IProgrammableBorrower.onStopEpoch()` for the amount owed to the pool, including pending withdrawal requests. `onStopEpoch()` first checks whether the programmable borrower has enough idle underlying. If not, it attempts to withdraw the shortfall from the configured ERC4626 vault.

When the vault’s shares economically cover the shortfall but its currently withdrawable liquidity is insufficient, `vault.withdraw()` reverts. The catch block then reverts with `StopEpochVaultLiquidityUnavailable`. Because the `onStopEpoch()` call occurs outside `IdleCDOEpochVariant`’s `try this.getFundsFromBorrower(...)` block, the CDO-level default handler is not reached.

The relevant state remains stuck:

```solidity
isEpochRunning == true
paused() == true
allowAAWithdrawRequest == false
allowBBWithdrawRequest == false
```

This follows from `startEpoch()` pausing the CDO and disabling withdrawal requests, while `stopEpoch()` only reopens them after borrower funding succeeds.

### Impact Explanation
The broken lifecycle invariant is that a solvent epoch stop should either fund the owed amount, enter the borrower-default path, or provide another bounded recovery route. Instead, an external vault participant can force the epoch into an open-ended paused state without defaulting the borrower.

For example, assume:

- active pool NAV: `10_000_000` underlying
- matured pending withdrawals: `1_000_000` underlying
- programmable borrower cash: `0`
- programmable borrower vault share value: `10_000_000`
- ERC4626 free liquidity after the attacker withdrawal: `999_999`

The share position still economically covers the `1_000_000` required amount, but the withdrawal reverts due to unavailable vault liquidity. The full `10_000_000` active position and the `1_000_000` matured receipts remain inside the stopped lifecycle. The quantified temporary freezing impact is therefore `11_000_000` underlying worth of principal and pending obligations, plus forfeited epoch settlement and withdrawal availability for the duration of the liquidity shortage.

The attacker can repeat the drain whenever liquidity reappears, extending the freeze without making the borrower insolvent.

### Likelihood Explanation
Likelihood depends on the selected ERC4626 vault. It is high when the vault is shared with public depositors or borrowers and its idle liquidity can fall below the programmable borrower’s withdrawal requirement. A single large vault shareholder can consume liquidity before the manager’s `stopEpoch` transaction.

No privileged Idle role is required. The attacker only needs control over enough external vault claims to reduce available liquidity. The configured borrower, owner, manager, guardian, and fee receiver remain honest.

Likelihood is lower for isolated ERC4626 vaults that are exclusively funded by the programmable borrower or that implement enforced withdrawal liquidity. The code does not enforce either property during initialization; it only checks that the vault asset matches the CDO underlying.

### Recommendation
Do not allow ordinary `stopEpoch` settlement to depend on instantly withdrawable liquidity in a shared ERC4626 vault.

Prefer one of the following mitigations:

1. Use a dedicated ERC4626 vault or liquidity sleeve whose assets cannot be withdrawn by unrelated users.
2. Maintain a reserved idle-liquidity buffer that cannot be lent through the external vault.
3. Change `onStopEpoch()` to surface temporary liquidity failure as an explicit, bounded epoch state rather than reverting indefinitely.
4. If liquidity failure should be treated as a credit event, return `false` from the hook and route the shortfall through the existing default/recovery accounting instead of leaving the epoch permanently running.
5. Track and enforce a protected reserve such that `borrow()` and `executeBorrow()` cannot draw liquidity required for matured pending withdrawals or imminent epoch settlement.

### Proof of Concept
The following Foundry fork PoC assumes the deployed programmable-borrower pool exposes `cdo`, `programmableBorrower`, `vault`, and `underlying`. `externalVaultHolder` is an unprivileged holder of enough vault shares to drain the free liquidity required by the pool.

```solidity
function test_ExternalVaultLiquidityDrainFreezesStopEpoch() public {
    uint256 depositAmount = 10_000_000 * ONE_UNDERLYING;
    uint256 pendingAmount = 1_000_000 * ONE_UNDERLYING;

    // Honest KYC lender deposits and creates a matured withdrawal obligation.
    vm.startPrank(lender);
    underlying.approve(address(cdo), depositAmount);
    cdo.depositAA(depositAmount);
    uint256 trancheAmount = AAtranche.balanceOf(lender) * pendingAmount / depositAmount;
    cdo.requestWithdraw(trancheAmount, address(AAtranche));
    vm.stopPrank();

    // Honest manager starts the epoch. Idle funds are parked in the ERC4626 vault.
    vm.prank(manager);
    cdo.startEpoch();

    assertTrue(cdo.isEpochRunning());
    assertTrue(cdo.paused());

    // An unprivileged ERC4626 participant drains withdrawable liquidity.
    // The programmable borrower's shares remain economically backed, but the
    // vault cannot currently satisfy the shortfall.
    uint256 required = pendingAmount;
    uint256 idleLiquidity = underlying.balanceOf(address(vault));
    uint256 maxWithdrawable = vault.maxWithdraw(address(programmableBorrower));

    require(maxWithdrawable >= required, "position must be economically covered");

    uint256 sharesToDrain =
        vault.convertToShares(idleLiquidity - maxWithdrawable + required + 1);

    vm.prank(externalVaultHolder);
    vault.redeem(
        sharesToDrain,
        externalVaultHolder,
        externalVaultHolder
    );

    assertLt(
        vault.maxWithdraw(address(programmableBorrower)),
        required,
        "vault liquidity was not drained"
    );

    // The epoch has matured and the borrower is solvent. Settlement nevertheless
    // reverts inside onStopEpoch and never reaches IdleCDO's default handler.
    vm.warp(cdo.epochEndDate() + 1);

    vm.prank(manager);
    vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
    cdo.stopEpoch(0, 0);

    assertTrue(cdo.isEpochRunning(), "epoch should still be running");
    assertTrue(cdo.paused(), "CDO should remain paused");
    assertFalse(cdo.defaulted(), "borrower did not default");

    vm.prank(lender);
    vm.expectRevert(NotAllowed.selector);
    cdo.claimWithdrawRequest();
}
```