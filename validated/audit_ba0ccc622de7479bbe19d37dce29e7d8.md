### Title
Unprivileged ERC4626 liquidity consumption can temporarily block epoch settlement - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
`IdleCDOEpochVariant.stopEpoch` delegates programmable-borrower liquidity recall to `ProgrammableBorrower.onStopEpoch`. If the underlying ERC4626 vault has insufficient redeemable liquidity, `vault.withdraw` reverts and `onStopEpoch` deliberately reverts `StopEpochVaultLiquidityUnavailable`, preventing epoch settlement.

An unprivileged user of the configured ERC4626 vault can consume its available liquidity immediately before the manager calls `stopEpoch`. While such liquidity remains unavailable, every retry reverts and withdrawals that depend on epoch settlement remain frozen.

### Finding Description
During `stopEpoch`, the CDO asks the programmable borrower to make `_amountToPullFromBorrower + _pendingWithdraws` available:

```solidity
IProgrammableBorrower(_borrower()).onStopEpoch(
  _amountToPullFromBorrower + _pendingWithdraws,
  _isRequestingAllFunds
)
```

In `ProgrammableBorrower.onStopEpoch`, a shortfall is withdrawn from the configured ERC4626 vault. If the vault’s share value covers the shortfall but its immediate withdrawal liquidity does not, the vault call reverts and the function propagates `StopEpochVaultLiquidityUnavailable` instead of returning `false`.

Because the revert occurs before `collectWithdrawFunds`, accounting finalization, `isEpochRunning = false`, and the epoch-number increment, the CDO remains in the running-epoch state. A vault participant can repeatedly consume replacement liquidity to extend the freeze across multiple settlement attempts.

### Impact Explanation
All funds needed by the stop operation can be temporarily frozen, including pending withdrawal principal and associated epoch interest. For example, if the pending-withdraw basis is 1,000,000 USDC and required interest is 10,000 USDC, rendering less than 1,010,000 USDC redeemable blocks settlement of the full 1,010,000 USDC liability.

The issue does not directly steal funds, but it creates a repeatable availability failure whose cost is borne by withdraw requesters and tranche holders.

### Likelihood Explanation
The attacker does not need any privileged role. Any user able to withdraw or otherwise consume liquidity from the configured ERC4626 vault can trigger the condition. The attack requires timing around the manager’s honest `stopEpoch` call and enough external-vault influence to make the requested withdrawal temporarily unavailable.

Existing checks do not prevent it: `stopEpoch` has already entered settlement, the CDO relies on `onStopEpoch`, and `onStopEpoch` bubbles the vault withdrawal failure as a revert. The borrower, owner, manager, and CDO can remain honest.

### Recommendation
Make transient ERC4626 liquidity failures non-blocking or explicitly bounded:

- Have `onStopEpoch` return `false` when withdrawal fails, and let the existing default/recovery path decide whether this represents a real borrower default.
- Alternatively, add a separate retryable settlement state that permits withdrawal claims/funding to proceed independently of borrower liquidity recall.
- Track the amount already recalled and avoid requiring a single atomic external-vault withdrawal for the entire stop amount.
- Consider capping external-vault liquidity exposure so reserved pending withdrawals remain immediately redeemable.

Returning `false` on actual insufficient vault backing and reserving revert only for unrecoverable integration errors would avoid conflating temporary liquidity exhaustion with a failed transaction.

### Proof of Concept
A Foundry fork test can reproduce the state transition against the deployed programmable-borrower configuration:

```solidity
function testVaultLiquidityConsumerBlocksStopEpoch() external {
    // Arrange: pool has an active epoch and pending withdrawals.
    uint256 required = pendingWithdrawBasis + expectedInterest;

    // Attacker is an unprivileged ERC4626 vault participant.
    // Consume vault liquidity so redeemable assets are below `required`,
    // while the borrower's share value still nominally covers it.
    vm.prank(attacker);
    underlyingVault.borrowOrWithdrawAvailableLiquidity();

    // Honest manager attempts settlement.
    vm.prank(manager);
    vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
    cdoEpoch.stopEpoch(nextApr, 0);

    assertTrue(cdoEpoch.isEpochRunning());
    assertEq(strategy.epochNumber(), epochBefore);

    // Repeat after replacement liquidity appears.
    waitForVaultLiquidity();
    vm.prank(attacker);
    underlyingVault.borrowOrWithdrawAvailableLiquidity();

    vm.prank(manager);
    vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
    cdoEpoch.stopEpoch(nextApr, 0);

    // Pending withdrawals remain unsettled.
    assertEq(strategy.pendingWithdraws(), pendingWithdrawBasis);
}
```

The decisive assertion is that `stopEpoch` reverts while `isEpochRunning` remains true, so the pending-withdrawal amount is frozen rather than settled.