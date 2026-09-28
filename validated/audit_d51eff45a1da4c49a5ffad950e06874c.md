### Title
Post-default instant withdraw receipts are routed into the defaulted epoch's claim bucket and paid out of the isolated recovery reserve - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
Path traversal lets a request escape its intended scope and resolve against a protected resource. The vault analog is epoch-scoped claim routing: `requestInstantWithdraw` always writes the new receipt into `instantWithdrawsRequestsByEpoch[_user][epochNumber]`, but `epochNumber` never advances after a default. Post-default instant requests therefore land in `defaultRecoveryEpoch`'s bucket and are paid at `defaultRecoveryPrice` out of `defaultRecoveryReserve` — reserve that was sized at finalization exclusively for pre-default claimants. `requestInstantWithdraw` has no post-default branch and none of the `defaultRecoveryFinalized` guards that `requestWithdraw` has (`IdleCreditVault.sol:247-258` vs `356-375`).

### Finding Description
- `finalizeDefaultRecovery` snapshots `defaultRecoveryEpoch = epochNumber`, sets `defaultRecoveryFinalized`, and isolates `defaultRecoveryReserve = reserveAmount` sized for `defaultPendingClaimBasis()` (pre-default receipts only) (`IdleCreditVault.sol:679-696`).
- After finalization, `epochNumber` stays equal to `defaultRecoveryEpoch` because only `deposit()` during `stopEpoch` increments it (`:607-611`), and a defaulted pool never stops an epoch again.
- `requestInstantWithdraw` performs no `defaultRecoveryFinalized` check and records `instantWithdrawsRequestsByEpoch[_user][epochNumber] += _amount` and `instantWithdrawClaimsByEpoch[epochNumber] += _amount` (`:367-374`). A fresh post-default request is thus indistinguishable from a defaulted-epoch receipt — the "traversal" into an epoch scope the receipt does not belong to.
- `claimInstantWithdrawRequest` then calls `_claimDefaultedInstantWithdrawRequest`, which clears `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` — including the attacker's brand-new receipt — and pays `(claimBasis * defaultRecoveryPrice) / 1e18` via `_transferDefaultRecovery`, which decrements `defaultRecoveryReserve` (`:382-386`, `:842-856`, `:912-917`).
- The new receipt was never funded: the request only burns CDO strategy tokens and mints a user receipt; no underlying enters the reserve (`:360-374`, reserve only grows in `reserveDefaultRecovery`/`finalizeDefaultRecovery`, both finalized-gated).

Broken invariant: one receipt, one payout + recovery-reserve isolation. Existing guards don't stop it: `defaultRecoveryFinalized` reverts only inside `reserveDefaultRecovery`/`finalizeDefaultRecovery`; `requestInstantWithdraw` and `claimInstantWithdrawRequest` intentionally remain callable post-default (the `_onlyIdleCDO` path is the only gate, and instant withdrawals are a supported user flow).

### Impact Explanation
An attacker holding tranche tokens requests an instant withdraw of size X after finalization and immediately claims `X * defaultRecoveryPrice` underlying from `defaultRecoveryReserve`. This directly steals recovery funds earmarked for legitimate defaulted-epoch claimants (normal withdrawers and unfunded instant withdrawers). Worse, each spurious claim also decrements `instantWithdrawClaimsByEpoch[defaultRecoveryEpoch]` and `pendingInstantWithdraws` (`:847-853`); once the attacker drains `X > 0`, the last legitimate claimant's `_claimDefaultedInstantWithdrawRequest` underflows on `instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis` or `defaultRecoveryReserve -= _amount`, permanently freezing their unclaimed recovery. Quantified loss: up to `min(attackerReceipt, remainingReserve) * defaultRecoveryPrice` stolen, plus permanent freeze of the residual legitimate claims.

### Likelihood Explanation
Requires a defaulted vault that reached `finalizeDefaultRecovery` with `pendingInstantWithdraws != 0` (i.e., `defaultInstantWithdrawsFinalized = true`) — a normal consequence of an unfunded instant request at epoch end. The attacker only needs to be a KYC-passing tranche holder (`isWalletAllowed`-gated EOA) calling `requestInstantWithdraw`/`claimInstantWithdrawRequest` through the CDO, both unprivileged user flows with no post-default block. One prerequisite worth noting: reachability depends on the CDO's `requestInstantWithdraw` not reverting while `defaulted()`; the strategy side places no such restriction and the parallel normal-request path explicitly supports post-default requests (`:247-258`), so asymmetric treatment of instant requests appears to be an oversight rather than a deliberate gate.

### Recommendation
In `requestInstantWithdraw`, when `defaultRecoveryFinalized` is true either revert or route the request into a post-default bucket keyed by a fresh epoch marker (or `postDefaultRequests`-style accounting like `requestWithdraw` uses), so it can never be cleared by `_claimDefaultedInstantWithdrawRequest`/`_transferDefaultRecovery`. At minimum, in `_claimDefaultedInstantWithdrawRequest` cap `claimBasis` to the pre-finalization `instantWithdrawClaimsByEpoch[defaultRecoveryEpoch]` snapshot rather than trusting the per-user per-epoch entry that post-default requests can still inflate.

### Proof of Concept
```solidity
// test/foundry/IdleCreditVaultPostDefaultInstant.t.sol
// Fork test on the existing IdleCreditVault.t.sol harness.
function testPostDefaultInstantRequestDrainsRecoveryReserve() external {
    uint256 amountWei = 10_000 * ONE_SCALE;
    address victim = makeAddr('victimInstant');
    address attacker = makeAddr('attacker');

    _depositWithUser(victim, amountWei, true);
    _depositWithUser(attacker, amountWei, true);
    _depositWithUser(makeAddr('filler'), amountWei, true);

    // victim queues an instant withdraw that stays UNFUNDED at epoch end
    vm.prank(victim);
    cdoEpoch.requestInstantWithdraw(trancheBal(victim, AAtranche) / 4, address(AAtranche));
    // do NOT call getInstantWithdrawFunds -> pendingInstantWithdraws > 0

    _startEpochAndCheckPrices(0);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    // borrower repays nothing -> default
    vm.prank(manager);
    cdoEpoch.stopEpoch(initialProvidedApr, 0);
    vm.prank(manager);
    cdoEpoch.finalizeDefaultRecovery(partialRecovery, recoverySource); // price p < 1

    IdleCreditVault strat = IdleCreditVault(address(strategy));
    assertTrue(strat.defaultInstantWithdrawsFinalized());
    uint256 reserveBefore = strat.defaultRecoveryReserve();

    // ATTACK: post-default instant request lands in defaultRecoveryEpoch bucket
    uint256 atkAmt = trancheBal(attacker, AAtranche) / 2;
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(atkAmt, address(AAtranche));
    assertEq(strat.instantWithdrawsRequestsByEpoch(attacker, strat.defaultRecoveryEpoch()), atkAmt);

    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    uint256 stolen = underlying.balanceOf(attacker) - balPre;

    // attacker was paid haircut price from the victims' reserve despite zero funding
    assertEq(stolen, atkAmt * strat.defaultRecoveryPrice() / 1e18);
    assertEq(strat.defaultRecoveryReserve(), reserveBefore - stolen);

    // victim's legitimate claim now underflows -> permanently frozen
    vm.prank(victim);
    vm.expectRevert();
    cdoEpoch.claimInstantWithdrawRequest();
}
```
Sequence: running epoch → default → finalized recovery (mixed phase). Attack cost: tranche tokens the attacker already owns; payout comes from `defaultRecoveryReserve`, not from the attacker's own backing, so the receipt is pure theft plus a freezing side-effect on remaining claimants.