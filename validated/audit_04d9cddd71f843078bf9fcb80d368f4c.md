### Title
Stale `instantWithdrawsRequestsByEpoch` / `instantWithdrawClaimsByEpoch` entries are never cleared by `claimInstantWithdrawRequest`, corrupting default-recovery accounting and freezing/diluting the recovery reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Analogous to CVE-2016-9106 (an allocated IO vector that is never freed), `requestInstantWithdraw` allocates per-epoch receipt accounting (`instantWithdrawsRequestsByEpoch[user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]`), but the normal funded claim path `claimInstantWithdrawRequest` only clears the aggregate `instantWithdrawsRequests[user]` and burns the receipt — it never releases the per-epoch records. The unreleased per-epoch basis is later re-read by `defaultPendingClaimBasis()` and `_defaultPrefundedInstantReserve()` during `finalizeDefaultRecovery`, inflating both the claim basis and the assumed prefunded cash reserve by the full amount of already-claimed receipts.

### Finding Description
At request time, `requestInstantWithdraw` records the receipt twice: `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount` and `instantWithdrawClaimsByEpoch[currentEpoch] += _amount` (lines 371-372). The funded claim path then does:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

(lines 387-392) — no write to `instantWithdrawsRequestsByEpoch` or `instantWithdrawClaimsByEpoch`. Only the defaulted-claim path `_claimDefaultedInstantWithdrawRequest` clears them (lines 847-853). So a receipt that is funded and claimed normally inside the same strategy epoch leaves its basis in `instantWithdrawClaimsByEpoch[epochNumber]` forever.

`epochNumber` only advances in `deposit()` when the epoch is running (lines 607-610), so it stays constant through the whole running epoch — including the window where instant withdrawals are funded via `collectInstantWithdrawFunds` (which decrements `pendingInstantWithdraws` but not the per-epoch basis, line 401) and claimed, and the subsequent default triggered by a failed `getInstantWithdrawFunds`/`stopEpoch`.

When `finalizeDefaultRecovery` later runs with `pendingInstantWithdraws != 0` (i.e., some other requester was only partially funded):

- `defaultPendingClaimBasis()` adds `instantWithdrawClaimsByEpoch[epochNumber]` — still containing the already-claimed amount `A` — to the default basis (lines 644-649).
- `_defaultPrefundedInstantReserve()` computes `prefundedReserve = instantBasis - pendingInstant`, which counts `A` as underlying "already held" by the strategy even though that cash was already paid out to the claimer (lines 716-723).
- `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` is therefore computed with both a phantom +`A` in the reserve numerator and a phantom +`A` in the basis denominator (lines 685-688). For any partial recovery (`realReserve < totalBasis - A`) this pushes `defaultRecoveryPrice` above the fair ratio.

### Impact Explanation
The phantom `A` in `reserveAmount` does not correspond to real cash: `defaultRecoveryReserve` is credited with underlying that was already transferred out. Recovery claimants (both defaulted receipts via `_transferDefaultRecovery` and post-default requests) are then paid `basis × recoveryPrice` from a reserve that is short by up to `A × recoveryPrice`. The invariant `payouts ≤ reserve` breaks: early claimants are overpaid at the expense of later ones, and the last claimants' `underlyingToken.safeTransfer` / `defaultRecoveryReserve -= _amount` revert — permanently freezing their recovery in the strategy (`defaultRecoveryReserve` has no sweep path other than claims). Quantified loss: up to the total same-epoch instant-withdraw amounts that were funded and claimed before finalization, i.e. the full "leaked" basis that was never freed.

### Likelihood Explanation
The trigger sequence uses only honest privileged actions plus unprivileged ones:

1. During buffer/running epoch N (instant withdrawals enabled), attacker A and honest user B call `requestWithdraw` routed to `requestInstantWithdraw` via the CDO.
2. Manager calls `getInstantWithdrawFunds`; borrower returns only partial liquidity, so `collectInstantWithdrawFunds` funds A's share but leaves `pendingInstantWithdraws == B > 0`.
3. After `instantWithdrawDeadline`, A claims via `claimInstantWithdrawRequest` — receives full par payout; the stale per-epoch basis remains.
4. Borrower fails to repay at `stopEpoch` → `defaulted` (a market event, not attacker-controlled); owner/manager calls `finalizeDefault` with honest recovery funds.

Every step except the borrower's default is a normal, permissionless or routine operation; the miscalculation is then forced on all recovery claimants deterministically. Likelihood is bounded by needing instant withdrawals enabled and a partial-funding-then-default sequence in one epoch, but requires no attacker privilege beyond holding tranche tokens.

### Recommendation
In `claimInstantWithdrawRequest`, release the per-epoch allocation symmetrically with `_claimDefaultedInstantWithdrawRequest`: track the request epoch (or iterate `instantWithdrawsRequestsByEpoch[_user][epochNumber]`), zero the per-user-per-epoch entry, and subtract the claimed basis from `instantWithdrawClaimsByEpoch[requestEpoch]`. Alternatively, have `collectInstantWithdrawFunds` decrement `instantWithdrawClaimsByEpoch` as it decrements `pendingInstantWithdraws`, so the per-epoch claim basis only ever reflects unfunded receipts. A reserve-conservation invariant test (`sum of recovery payouts + residual reserve == recovered amount`) across a claim-then-default-in-same-epoch sequence would catch this.

### Proof of Concept
A Foundry fork PoC (build on `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function testStaleInstantBasisCorruptsRecovery() external {
    uint256 instantDelay = cdoEpoch.instantWithdrawDelay();
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(instantDelay, 1000, false);

    address A = makeAddr('attacker');
    address B = makeAddr('honest-instant');
    uint256 amount = 10_000 * ONE_SCALE;
    _depositWithUser(A, amount, true);
    _depositWithUser(B, amount, true);
    _depositWithUser(makeAddr('active'), amount, true);

    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr / 2, _expectedFundsEndEpoch());

    // epoch N buffer: A and B request instant withdraws
    vm.prank(A); cdoEpoch.requestWithdraw(0, address(AAtranche));
    vm.prank(B); cdoEpoch.requestWithdraw(0, address(AAtranche));

    _startEpochAndCheckPrices(1);
    IdleCreditVault sv = IdleCreditVault(address(strategy));

    // borrower returns only enough to fund A: pendingInstantWithdraws stays > 0 for B
    // (fund borrower with exactly A's share, call getInstantWithdrawFunds)
    ...
    vm.warp(block.timestamp + instantDelay + 1);
    vm.prank(manager);
    cdoEpoch.getInstantWithdrawFunds(); // partial collect -> pendingInstantWithdraws != 0

    // A claims at par; stale basis remains in instantWithdrawClaimsByEpoch[epochNumber]
    vm.prank(A);
    cdoEpoch.claimInstantWithdrawRequest();
    assertGt(sv.instantWithdrawClaimsByEpoch(sv.epochNumber()), sv.pendingInstantWithdraws(),
        'stale basis not released');

    // borrower defaults (returns nothing further)
    deal(defaultUnderlying, borrower, 0);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);
    assertTrue(cdoEpoch.defaulted());

    // finalize with a fair partial recovery
    uint256 totalBasis = cdoEpoch.getContractValue() + cdoEpoch.expectedEpochInterest()
        - cdoEpoch.pendingWithdrawFees() + sv.defaultPendingClaimBasis();
    uint256 recovered = totalBasis * 7e17 / 1e18;
    deal(defaultUnderlying, manager, recovered);
    vm.startPrank(manager);
    underlying.approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();

    // B claims recovery, then an active LP's post-default claim reverts:
    // reserve is short by A's already-paid amount.
    vm.prank(B); cdoEpoch.claimWithdrawRequest();
    vm.expectRevert(); // insufficient reserve / underflow -> permanently frozen
    cdoEpoch.claimWithdrawRequest();
}
```

Note: I verified the missing cleanup in `claimInstantWithdrawRequest` (lines 387-392) against the cleanup present in `_claimDefaultedInstantWithdrawRequest` (lines 847-853), but could not exhaustively confirm every other write site for `instantWithdrawClaimsByEpoch` in the remaining ~30 matches in the file; if another path clears it before finalization the finding weakens to reserved-accounting drift only.