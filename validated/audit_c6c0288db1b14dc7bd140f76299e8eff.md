### Title
Unfunded instant-withdraw receipts can claim underlying reserved for other users' funded withdraw claims - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug is a race where `get_old_root` uses an extent buffer without locking it before a cloning operation — an operation consumes shared state before the prerequisite ordering step completed. The analog in `IdleCreditVault` is `claimInstantWithdrawRequest`: it pays out `instantWithdrawsRequests[_user]` from the strategy's underlying balance without verifying that the instant-withdraw bucket has actually been funded (`pendingInstantWithdraws` is never consulted), while `requestInstantWithdraw` marks every new request as unfunded. The only balance guard in `_transferFundedClaim` protects `defaultRecoveryReserve`, not the underlying already collected for other users' funded normal withdraw claims.

### Finding Description
In `IdleCreditVault.sol`:

- `requestInstantWithdraw` (lines 356-375) burns the CDO's strategy tokens, mints a receipt to the user, increments `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch`, and `pendingInstantWithdraws`. Funding arrives only later, when the CDO calls `collectInstantWithdrawFunds` (lines 398-403), which decrements `pendingInstantWithdraws` and pulls underlying from the CDO.
- `claimInstantWithdrawRequest` (lines 380-393) then burns the receipt and calls `_transferFundedClaim(_user, amount)` for the *entire* `instantWithdrawsRequests[_user]` balance — including the unfunded portion still counted in `pendingInstantWithdraws`.
- `_transferFundedClaim` (lines 897-907) only ensures `balance - defaultRecoveryReserve >= _amount`. It does not check `pendingInstantWithdraws`, so it happily spends underlying that was transferred in by `collectWithdrawFunds` (lines 411-430) to back *other users'* funded normal withdraw claims.

Sequence (running epoch, instant withdraws enabled via `allowInstantWithdraw`):

1. Alice has a normal withdraw request funded at `stopEpoch` via `collectWithdrawFunds` — the strategy now holds `A` underlying reserved for her claim, but she has not claimed yet.
2. Attacker (any KYC'd EOA) calls `IdleCDOEpochVariant.requestInstantWithdraw` → `IdleCreditVault.requestInstantWithdraw`. The receipt is minted; `pendingInstantWithdraws += amount`. No underlying has been collected.
3. Attacker immediately calls `IdleCDOEpochVariant.claimInstantWithdrawRequest` → `IdleCreditVault.claimInstantWithdrawRequest`. `instantWithdrawsRequests[attacker]` is burned and `_transferFundedClaim` transfers `amount` underlying out of the strategy — drawn from Alice's funded claim bucket, since `defaultRecoveryReserve == 0` disables the only guard.
4. When Alice later calls `claimWithdrawRequest` → `_claimFundedWithdrawRequest` → `_transferFundedClaim`, the strategy's balance is short and the `safeTransfer` reverts. Alice's already-funded, already-accounted claim is permanently unpayable unless someone re-funds the strategy.

The broken invariant is "one receipt, one *funded* payout": a receipt representing unfunded debt is redeemed against assets earmarked for a different claimant, producing direct insolvency — the exact same shape as the kernel bug (consuming shared state without waiting for the operation that makes it valid).

### Impact Explanation
Direct theft and insolvency: the attacker receives underlying equal to their instant-withdraw request (up to the full funded-but-unclaimed withdraw balance held by the strategy), and the legitimate claimants' payouts revert permanently. Loss is quantified as `min(attackerRequest, fundedUnclaimedBalance)` — i.e., the entire pending funded claim bucket can be drained. No privileged role is required; only `allowInstantWithdraw` must be true and the attacker must pass `isWalletAllowed`/Keyring, which any KYC-passing lender does.

### Likelihood Explanation
Requirements: `allowInstantWithdraw` enabled on the CDO, at least one funded-but-unclaimed withdraw (or any stray underlying) in the strategy, and the instant funding leg (`collectInstantWithdrawFunds`) not yet executed for the attacker's request. The window exists whenever funded normal claims sit idle in the strategy while a new instant request is made before the next `startEpoch` funding step. Cost to the attacker is only a deposit plus the request transaction; the receipt mint is 1:1 so no capital is lost if the attempt reverts. The gap is structural: nothing in `claimInstantWithdrawRequest` or `_transferFundedClaim` ties payout to `pendingInstantWithdraws` or to per-epoch funding status.

Caveat: I could not fully trace `IdleCDOEpochVariant`'s instant-withdraw orchestration (`getInstantWithdrawFunds` / `collectInstantWithdrawFunds` call sites) within the available iterations; if the CDO guarantees collection in the same transaction as every `requestInstantWithdraw`, the window narrows. However the code comments ("funds will get transferred from borrower when epoch starts") and the existence of the `pendingInstantWithdraws` unfunded-remainder tracking indicate collection is asynchronous, so the unfunded-claim path is reachable.

### Recommendation
In `claimInstantWithdrawRequest`, cap the payable amount to the funded portion, e.g. `amount = min(instantWithdrawsRequests[_user], instantBasis - pendingInstantWithdraws)` or track a per-user funded instant balance populated by `collectInstantWithdrawFunds`. Alternatively revert when `pendingInstantWithdraws != 0` covers the claim, and/or extend `_transferFundedClaim` to also subtract `defaultRecoveryReserve`-like buckets for all earmarked liabilities. The funding step and the claim step must be ordered on-chain, not just operationally.

### Proof of Concept
Foundry fork PoC sketch (based on `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testUnfundedInstantClaimStealsFundedWithdraw() external {
    // 1. Alice deposits, requests normal withdraw, epoch stops and funds it
    _depositWithUser(alice, 10_000 * ONE_SCALE, true);
    vm.prank(alice);
    uint256 aliceReq = cdoEpoch.requestWithdraw(0, address(AAtranche));
    _startEpochAndCheckPrices(0);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.prank(manager);
    cdoEpoch.stopEpoch(initialProvidedApr, 0); // collectWithdrawFunds pulls aliceReq into strategy

    IdleCreditVault vault = IdleCreditVault(address(strategy));
    assertGt(underlying.balanceOf(address(vault)), 0); // funded, unclaimed

    // 2. Attacker deposits, requests instant withdraw (unfunded), then claims
    _depositWithUser(attacker, aliceReq, true);
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(IERC20(AAtranche).balanceOf(attacker), address(AAtranche));
    assertEq(vault.pendingInstantWithdraws(), vault.instantWithdrawsRequests(attacker)); // fully unfunded

    uint256 pre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest(); // pays out of Alice's funded bucket
    assertGt(underlying.balanceOf(attacker) - pre, 0);

    // 3. Alice's funded claim now reverts / strategy is insolvent
    vm.expectRevert();
    vm.prank(alice);
    cdoEpoch.claimWithdrawRequest();
}
```