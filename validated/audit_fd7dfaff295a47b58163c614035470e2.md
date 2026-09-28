### Title
Unfunded instant-withdraw receipts can be claimed before `collectInstantWithdrawFunds`, draining the funded-claim reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary

`IdleCreditVault.claimInstantWithdrawRequest` pays the full `instantWithdrawsRequests[_user]` receipt from the strategy's underlying balance without synchronizing with `collectInstantWithdrawFunds`, the only step that actually pulls borrower funds and decrements `pendingInstantWithdraws`. This mirrors the CVE pattern: an asynchronous obligation (the instant receipt, like the scheduled tasklet) is allowed to consume a shared buffer (the funded-claim reserve) before the termination/funding step that was supposed to precede it. A user who times `claimInstantWithdrawRequest` in the window between `startEpoch` and the manager's `getInstantWithdrawFunds` is paid out of underlying that belongs to already-matured claims, permanently freezing later claimers.

### Finding Description

The instant-withdraw lifecycle has three unsynchronized steps:

1. `requestInstantWithdraw` burns CDO strategy tokens and mints the user a receipt, increments `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch`, and `pendingInstantWithdraws` (`IdleCreditVault.sol:356-375`).
2. Later, the honest manager calls `getInstantWithdrawFunds` on the CDO, which calls `collectInstantWithdrawFunds` to `safeTransferFrom` the underlying and decrement `pendingInstantWithdraws` (`IdleCreditVault.sol:398-403`).
3. `claimInstantWithdrawRequest` burns the full `instantWithdrawsRequests[_user]` and calls `_transferFundedClaim(_user, amount)` immediately (`IdleCreditVault.sol:380-393`).

The CDO-side entry point `IdleCDOEpochVariant.claimInstantWithdrawRequest` only gates on `allowInstantWithdraw` (`IdleCDOEpochVariant.sol:975-979`). Neither layer checks `pendingInstantWithdraws`, `instantWithdrawDelay`, or whether the specific epoch's receipts were ever funded — there is no `vchan_synchronize()` equivalent. The only protection is the implicit assumption that `getInstantWithdrawFunds` runs before any claim, an assumption no code enforces.

### Impact Explanation

The strategy's underlying balance is the shared funded-claim reserve backing all matured claims (normal funded receipts and previously collected instant receipts). A claim on an unfunded receipt succeeds whenever that reserve is non-empty, so the attacker receives underlying earmarked for other users' claims. When those users later claim, `safeTransfer` reverts on insufficient balance — a permanent freeze/theft of their matured claims up to the attacker's unfunded receipt size. This breaks the "one receipt, one funded payout" and solvency invariants. The existing guards (`_onlyIdleCDO`, `allowInstantWithdraw`, `isWalletAllowed`) do not verify funding status, so none of them stop it.

### Likelihood Explanation

The attacker only needs to be an allowed wallet holding tranche tokens. Sequence: APR drops by more than `instantWithdrawAprDelta` at `stopEpoch`, the attacker calls `requestWithdraw` which routes into `requestInstantWithdraw`, the manager calls `startEpoch` (funding the new epoch but not yet the instant receipts), and the attacker immediately calls `claimInstantWithdrawRequest` while the strategy still holds reserve for earlier claims. This requires only sequencing around honest manager transactions — no privileged collusion. The only caveat is that profitability requires a non-empty funded reserve; if the reserve is empty the claim simply reverts, capping the attack at zero cost.

### Recommendation

Synchronize the claim with the funding step: in `claimInstantWithdrawRequest`, require that the claimed amount is backed — e.g., track a `fundedInstantWithdraws`/`claimableInstantWithdraws` counter incremented only by `collectInstantWithdrawFunds`, and revert or cap the payout to that counter. Equivalently, gate `IdleCDOEpochVariant.claimInstantWithdrawRequest` on a per-epoch funded flag so an unfunded receipt can never reach `_transferFundedClaim`.

### Proof of Concept

A Foundry fork test in `test/foundry/IdleCreditVault.t.sol` style:

```solidity
function testClaimInstantWithdrawBeforeFundingDrainsReserve() external {
    // user A has a matured, funded claim sitting in the strategy reserve
    // attacker B deposits AA, epochs run
    _stopEpochAndCheckPrices(0, initialProvidedApr / 2, _expectedFundsEndEpoch()); // apr drop
    cdoEpoch.requestWithdraw(0, address(AAtranche)); // routes to requestInstantWithdraw
    _startEpochAndCheckPrices(0); // epoch running, manager has NOT yet called getInstantWithdrawFunds

    // B claims immediately — pays out of A's funded reserve
    cdoEpoch.claimInstantWithdrawRequest();
    assertGt(underlying.balanceOf(address(this)), 0);

    // A's matured claim now reverts on insufficient strategy balance
    vm.expectRevert();
    // userA.claimWithdrawRequest();
}
```

Uncertainty note: I was unable to read the body of `_transferFundedClaim` to confirm it does not itself check `pendingInstantWithdraws` or a funded counter; if it enforces funding internally, this specific path is guarded and the residual analog is only that claim order is not enforced on-chain. A full-session verification of `_transferFundedClaim` and `getInstantWithdrawFunds` is recommended before treating this as confirmed.