### Title
Post-default instant withdraws bypass default-recovery haircut and drain prefunded claims — ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`requestWithdraw` was hardened for the post-default regime: once `defaultRecoveryFinalized` is set it requires the user to have fully claimed all prior receipts and routes the new request through the pre-haircut `postDefaultRequests` bucket (lines 247–257). The parallel entry point `requestInstantWithdraw` — the "`format_map` to `requestWithdraw`'s `format`" — received none of that handling. A post-default instant request is minted at par into `instantWithdrawsRequests`/`instantWithdrawsRequestsByEpoch` under the *current* epoch, is never haircutted by `_claimDefaultedInstantWithdrawRequest` (which only clears `defaultRecoveryEpoch` basis), and is then paid 1:1 via `_transferFundedClaim`, which only protects `defaultRecoveryReserve` and nothing else.

### Finding Description
When the CDO finalizes a borrower default via `finalizeDefaultRecovery` (lines 661–710), two disjoint pools of underlying can exist in the strategy:

1. `defaultRecoveryReserve` — pays haircutted defaulted receipts and `postDefaultRequests`.
2. Funded-but-unclaimed instant-withdraw cash — underlying pulled earlier by `collectInstantWithdrawFunds` (lines 398–403). Once `pendingInstantWithdraws` hits 0, `_defaultPrefundedInstantReserve` returns 0 (lines 716–723) so this cash is **not** folded into the reserve, yet it is still owed at par to the users in `instantWithdrawsRequests`/`instantWithdrawClaimsByEpoch`.

`claimInstantWithdrawRequest` (lines 380–393) pays whatever remains in `instantWithdrawsRequests[_user]` through `_transferFundedClaim` (lines 897–907), whose only guard is `balance - defaultRecoveryReserve >= amount`. Any wallet in pool (2) is therefore first-come-first-served spendable by any account holding an instant receipt — including receipts created *after* finalization.

`requestInstantWithdraw` (lines 356–375):
- has no `defaultRecoveryFinalized` branch,
- does not require the caller to first claim old funded/defaulted receipts (unlike `requestWithdraw` line 249),
- mints the receipt at par (no haircut, unlike the post-default `requestWithdraw` path which relies on the CDO passing a pre-haircut amount into `postDefaultRequests`),
- still increments `instantWithdrawsRequestsByEpoch[user][epochNumber]` — an epoch different from `defaultRecoveryEpoch`, so `_claimDefaultedInstantWithdrawRequest` (lines 842–856) skips it entirely.

Result: a post-default instant receipt is treated as a fully-funded, par-value claim against cash that belongs to earlier funded claimants.

### Impact Explanation
After default finalization, any lender (unprivileged; the request flows through the honest CDO's instant-withdraw path, which the protocol explicitly keeps open post-default — `requestWithdraw` has a dedicated post-default mode) can:

1. Call `requestInstantWithdraw(amount, attacker)` via the CDO, burning `amount` of the CDO's (now haircut-valued) strategy tokens and receiving a par `instantWithdrawsRequests` receipt.
2. Call `claimInstantWithdrawRequest(attacker)` → `_claimDefaultedInstantWithdrawRequest` clears only default-epoch basis → `amount = instantWithdrawsRequests[attacker]` → `_transferFundedClaim` succeeds as long as `balance - reserve >= amount`.
3. The strategy's unreserved cash — underlying collected via `collectInstantWithdrawFunds` for still-unclaimed funded instant receipts — is transferred to the attacker at par.

Subsequent `claimInstantWithdrawRequest` calls by the legitimate funded claimants revert in `_transferFundedClaim` (`balance - reserve < amount`), permanently freezing their claims. This is direct theft of funded withdrawal proceeds, bounded by the unclaimed funded instant balance; symmetrically, any post-default cash legitimately routed to the strategy (e.g., a later `collectInstantWithdrawFunds` funding the queue) is also capturable at par while active tranche holders only recovered `defaultRecoveryPrice`. Fair-mint/burn and one-receipt-one-payout invariants are broken.

### Likelihood Explanation
Requires: (a) a borrower default that is finalized with recovery (in-scope, ordinary flow), and (b) residual unreserved underlying in the strategy — funded-but-unclaimed instant withdrawals — or any later CDO-driven funding of the instant queue. Condition (b) is common because `collectInstantWithdrawFunds` deliberately leaves collected cash in the strategy until users claim, and `_defaultPrefundedInstantReserve` explicitly excludes it from the reserve once `pendingInstantWithdraws == 0`. No privileged misbehavior is needed; the only assumption is that the CDO still routes `requestInstantWithdraw` after finalization, which the design supports (the symmetric `requestWithdraw` path is intentionally kept functional post-default).

### Recommendation
Mirror the `requestWithdraw` post-default logic in `requestInstantWithdraw`: when `defaultRecoveryFinalized`, require the user to have cleared all prior normal, APR0, instant, and post-default receipts (`_hasWithdrawRequest`, `instantWithdrawsRequests`, `postDefaultRequests`), and either route the request into `postDefaultRequests`-style accounting or revert. Additionally, consider sweeping funded-but-unclaimed instant cash into `defaultRecoveryReserve`-equivalent isolation at finalization so no unreserved par-claimable balance remains.

### Proof of Concept
Foundry fork sketch (extend `test/foundry` credit-vault default harness):

```solidity
function test_PostDefaultInstantWithdrawStealsFundedClaims() public {
    // --- epoch running, both users hold strategy tokens via CDO deposits ---
    uint256 amt = 100e6;
    _deposit(userA, amt);
    _deposit(attacker, amt);

    // userA requests instant withdraw; CDO collects funds, pendingInstantWithdraws -> 0
    vm.prank(cdo);
    vault.requestInstantWithdraw(amt, userA);
    vm.prank(cdo);
    vault.collectInstantWithdrawFunds(amt); // cash now sits unreserved in vault
    // userA has not yet claimed.

    // --- borrower defaults; finalizeDefaultRecovery runs ---
    _defaultAndFinalize(); // pendingInstantWithdraws == 0 -> no instant basis added to reserve

    // --- attacker opens a post-default instant request ---
    uint256 attackerShares = vault.balanceOf(cdo) / 2;
    vm.prank(cdo);
    vault.requestInstantWithdraw(attackerShares, attacker);

    // claim: default-epoch clear is a no-op for attacker's new receipt
    vm.prank(cdo);
    vault.claimInstantWithdrawRequest(attacker);

    // attacker received par underlying that was funded for userA
    assertGt(underlying.balanceOf(attacker), 0);

    // userA's already-funded claim is now frozen: _transferFundedClaim reverts
    vm.prank(cdo);
    vm.expectRevert(NotAllowed.selector);
    vault.claimInstantWithdrawRequest(userA);
}
```

Note: the exact trigger surface (`requestInstantWithdraw` reachability post-default through `IdleCDOEpochVariant`) should be confirmed in a full fork PoC; the accounting asymmetry in `IdleCreditVault` itself is present in code as cited.