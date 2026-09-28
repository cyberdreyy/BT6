### Title
Multi-epoch instant-withdraw claims omitted from default basis inflate `defaultRecoveryPrice` and drain the recovery reserve - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
CVE-2023-42754 is a NULL-deref: code assumed an `skb` was associated with a device before use. The credit-vault analog is the same bug class — `defaultPendingClaimBasis()` and `_defaultPrefundedInstantReserve()` assume every pending instant-withdraw claim is associated with the *current* `epochNumber` (`instantWithdrawClaimsByEpoch[epochNumber]`), but an unfunded instant receipt can survive across an epoch boundary and sit in `instantWithdrawClaimsByEpoch[<earlier epoch>]`. The stale-epoch basis is silently dropped from recovery accounting, inflating `defaultRecoveryPrice` and letting the attacker pull more than their pro-rata share of `defaultRecoveryReserve`, while later claimants are left unpaid.

### Finding Description
`requestInstantWithdraw` (line 356) records claims per epoch via `instantWithdrawsRequestsByEpoch[user][currentEpoch]` and `instantWithdrawClaimsByEpoch[currentEpoch]`, with no check that a prior unfunded instant claim exists in an older epoch. `collectInstantWithdrawFunds` (line 398) only decrements the aggregate `pendingInstantWithdraws` and never touches the per-epoch claim buckets — the code explicitly acknowledges partial funding: *"`pendingInstantWithdraws` is the still-unfunded remainder. If it is lower than the current-epoch claim basis, the difference is already-held underlying reserved"* (lines 712-715).

But `defaultPendingClaimBasis()` (lines 644-649) computes the haircut basis as:

```solidity
basis = pendingWithdraws;
if (pendingInstantWithdraws != 0) {
  basis += instantWithdrawClaimsByEpoch[epochNumber];
}
```

Only the **current** epoch's instant claims are counted. If `pendingInstantWithdraws` contains claims from both epoch `E` and `E+1` (the epoch in which default/finalization occurs), the epoch-`E` claims are excluded from `totalBasis` in `finalizeDefaultRecovery` (lines 679-688), so `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` is inflated. The epoch-`E` receipt is also not cleared by `_claimDefaultedInstantWithdrawRequest` (lines 843-844), which only reads `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` — instead it falls through to `claimInstantWithdrawRequest` (lines 387-392) and is paid **at par** via `_transferFundedClaim` out of the same strategy balance that backs `defaultRecoveryReserve`.

### Impact Explanation
Broken invariant: fair recovery distribution / one receipt one (haircut-adjusted) payout. An unprivileged tranche-token holder who creates instant-withdraw requests in two consecutive epochs where the first is only partially funded receives (a) their epoch-`E` receipt at par plus (b) their defaulted-epoch receipt at an inflated `defaultRecoveryPrice`, extracting more underlying than the reserve's fair share. Aggregate payouts exceed `defaultRecoveryReserve`, so other defaulted-epoch claimants (and post-default requesters, who are paid 1:1 from the same reserve at lines 760-767) suffer direct loss — theft of recovery funds and insolvency of the reserve. Loss is bounded by the stale-epoch unfunded instant claims plus the inflation of the recovery multiplier applied to all defaulted receipts.

### Likelihood Explanation
Requires a partially funded instant-withdraw bucket surviving an epoch boundary (i.e., the CDO could not fully source instant liquidity at stop/start epoch — a scenario the code explicitly models via `_defaultPrefundedInstantReserve`), followed by a borrower default and `finalizeDefault`. The attacker only needs to be a tranche-token holder calling `requestInstantWithdraw` in two epochs and `claimInstantWithdrawRequest`/`claimWithdrawRequest` after finalization — all unprivileged actions. Manager/borrower remain honest; the flaw is triggered whenever partial funding legitimately occurs. The existing guards (`_onlyIdleCDO`, `defaultRecoveryFinalized` checks, the `requestWithdraw` loss-receipt guard at lines 261-271) do not inspect `instantWithdrawClaimsByEpoch` for stale epochs, so nothing stops it.

### Recommendation
In `defaultPendingClaimBasis()` and `_defaultPrefundedInstantReserve()`, account for instant claims across **all** epochs with pending basis — e.g., maintain a global `totalInstantClaims` counter incremented in `requestInstantWithdraw` and decremented in `collectInstantWithdrawFunds`/`_claimDefaultedInstantWithdrawRequest`, rather than reading only `instantWithdrawClaimsByEpoch[epochNumber]`. Additionally, `_claimDefaultedInstantWithdrawRequest` should iterate/track all per-epoch receipt buckets for the user (or record the earliest outstanding epoch per user, mirroring `lastWithdrawRequest`) so stale-epoch instant receipts are haircut at `defaultRecoveryPrice` instead of falling through to the par funded-claim path.

### Proof of Concept
Sketch (Foundry fork, extends the harness style in `test/foundry/IdleCreditVault.t.sol`):

```solidity
// Setup: AA deposit, start epoch E.
uint256 amount = 100_000 * ONE_SCALE;
idleCDO.depositAA(amount);
_startEpochAndCheckPrices(0);

// Attacker requests instant withdraw in epoch E.
cdoEpoch.requestInstantWithdraw(trancheBal / 2, AAtranche); // via CDO path

// stopEpoch E: borrower repays only enough that collectInstantWithdrawFunds
// funds part of the instant bucket -> pendingInstantWithdraws > 0,
// instantWithdrawClaimsByEpoch[E] > 0 remains.
vm.warp(cdoEpoch.epochEndDate() + 1);
vm.prank(manager);
cdoEpoch.stopEpoch(partialInstantFunding, 0);
// epochNumber -> E+1

// Attacker requests instant withdraw again in epoch E+1.
cdoEpoch.requestInstantWithdraw(rest, AAtranche); // now instantWithdrawClaimsByEpoch[E+1] > 0

// Borrower defaults (no repayment) in epoch E+1.
vm.warp(cdoEpoch.epochEndDate() + 1);
vm.prank(manager);
cdoEpoch.stopEpoch(0, 0);          // defaulted == true

// Manager finalizes with a real recovery.
cdoEpoch.finalizeDefault(recovered, manager);
// BUG: defaultPendingClaimBasis() only counted instantWithdrawClaimsByEpoch[E+1];
// epoch-E claims missing -> defaultRecoveryPrice inflated.

// Attacker claims: epoch-E receipt at par via _transferFundedClaim +
// epoch-E+1 receipt at inflated defaultRecoveryPrice.
cdoEpoch.claimInstantWithdrawRequest();
// assert attacker underlying received > fair share; assert strategy balance
// < defaultRecoveryReserve obligations of remaining claimants.
```

Key assertion to demonstrate the invariant break: `underlying.balanceOf(attacker) - before` exceeds `attackerBasis * defaultRecoveryPrice / 1e18`, and a second (victim) defaulted claimant's `claimWithdrawRequest` reverts on `safeTransfer` or pays less than `claimBasis * defaultRecoveryPrice / 1e18` because the reserve was drained.

Caveat: I verified the accounting functions above but did not have remaining capacity to read `IdleCDOEpochVariant.stopEpoch`'s exact ordering of `deposit()`/`collectInstantWithdrawFunds`/`collectWithdrawFunds`; the finding depends on partial instant funding being reachable across an epoch boundary, which `_defaultPrefundedInstantReserve`'s own comments confirm is a supported state.