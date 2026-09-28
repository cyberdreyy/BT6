### Title
Claimed instant-withdraw receipts are never cleared from per-epoch accounting, inflating the default claim basis, diluting `defaultRecoveryPrice`, and permanently freezing part of the recovery reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` zeroes only the aggregate `instantWithdrawsRequests[_user]` after paying out (IdleCreditVault.sol:391), but never clears `instantWithdrawsRequestsByEpoch[_user][epoch]` or decrements `instantWithdrawClaimsByEpoch[epoch]`. These per-epoch entries are exactly the "freed pointer left dangling" pattern from CVE-2024-44997: storage that logically represents a consumed claim remains populated and is later re-read by `_defaultPrefundedInstantReserve`/`defaultPendingClaimBasis` during `finalizeDefaultRecovery` and by `_claimDefaultedInstantWithdrawRequest` during claims. The result is an inflated default claim basis (lower recovery price for every honest claimant) and a permanently unclaimable slice of `defaultRecoveryReserve`.

### Finding Description
- `requestInstantWithdraw` records both `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount` and `instantWithdrawClaimsByEpoch[currentEpoch] += _amount` (IdleCreditVault.sol:371-372).
- `claimInstantWithdrawRequest` pays the user and sets `instantWithdrawsRequests[_user] = 0`, but leaves both per-epoch mappings untouched (IdleCreditVault.sol:387-392).
- On default finalization, `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` when `pendingInstantWithdraws != 0` (IdleCreditVault.sol:644-649), and `_defaultPrefundedInstantReserve` computes `instantBasis - pendingInstant` from the same stale global (IdleCreditVault.sol:716-722). Already-claimed receipts are therefore counted again as defaulted claim basis, inflating `totalBasis` in `finalizeDefaultRecovery` (IdleCreditVault.sol:679-688) and depressing `defaultRecoveryPrice` for all active and pending claimants.
- After finalization, `_claimDefaultedInstantWithdrawRequest` re-reads the stale `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]` and executes `instantWithdrawsRequests[_user] -= claimBasis` (IdleCreditVault.sol:844-848). Since the aggregate was already zeroed, this underflows and reverts, so the stale claim can never be paid — its share of `defaultRecoveryReserve` is locked permanently.

### Impact Explanation
Every unit of instant withdrawal that was legitimately claimed in the default epoch is double-counted as recovery basis. Honest AA/BB holders and pending redeemers each receive `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` with a strictly lower `defaultRecoveryPrice`, and the reserve portion priced for stale claims can never leave the contract. The loss equals `staleInstantBasis * defaultRecoveryPrice / RECOVERY_FULL` in diluted payouts plus permanently locked reserve — a direct, quantified fund loss to honest users and permanent freezing of unclaimed recovery funds.

### Likelihood Explanation
The trigger requires that within the same `epochNumber` an instant request is funded (manager calls `getInstantWithdrawFunds`), claimed, and the borrower then defaults with `pendingInstantWithdraws != 0` at finalization (e.g. a partially unfunded instant queue, exactly the scenario exercised in `testFinalizeDefaultClaimsFundedAndDefaultedInstantReceipts`). The attacker is any unprivileged user whose earlier claim left the stale entries; no privileged misbehavior is needed. Any epoch in which instant withdrawals were serviced and a subsequent default occurs in the same epoch number is exposed.

### Recommendation
In `claimInstantWithdrawRequest` (and symmetrically wherever funded instant claims are paid), clear the per-epoch records when paying: zero `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and subtract the paid amount from `instantWithdrawClaimsByEpoch[epochNumber]`, mirroring how `_clearWithdrawClaimForEpoch` clears `withdrawsRequestsByEpoch` and resets `lastWithdrawRequest`. Alternatively, maintain a per-user "claimed" marker per epoch so `_claimDefaultedInstantWithdrawRequest` only sees receipts that were still outstanding at finalization.

### Proof of Concept
```solidity
// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.10;

// Foundry fork test against IdleCDOEpochVariant + IdleCreditVault.
// Setup follows test/foundry/IdleCreditVault.t.sol helpers.
function testStaleInstantBasisDilutesRecovery() external {
    uint256 amount = 100_000 * ONE_SCALE;
    address staleUser = makeAddr('stale-instant-user');
    address victim    = makeAddr('honest-pending-user');
    uint256 recoveryRatio = 7e17;

    _depositWithUser(staleUser, amount, true);
    _depositWithUser(victim,    amount, true);

    // epoch N running; both users request instant + normal withdraws
    _startEpochAndCheckPrices(0);
    vm.prank(victim);
    cdoEpoch.requestWithdraw(0, address(AAtranche)); // normal pending receipt
    vm.prank(staleUser);
    cdoEpoch.requestInstantWithdraw(0, address(AAtranche));

    // manager funds instant queue; staleUser claims and is paid at par
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    _getInstantFunds(); // collects only part -> pendingInstantWithdraws stays > 0
    vm.prank(staleUser);
    cdoEpoch.claimInstantWithdrawRequest();
    // BUG: instantWithdrawsRequestsByEpoch[staleUser][N] and
    // instantWithdrawClaimsByEpoch[N] still hold the claimed amount.

    IdleCreditVault cv = IdleCreditVault(address(strategy));
    uint256 honestBasis =
        cdoEpoch.getContractValue() + cdoEpoch.expectedEpochInterest()
        - cdoEpoch.pendingWithdrawFees() + cv.pendingWithdraws();
    uint256 staleBasis = cv.instantWithdrawsRequestsByEpoch(staleUser, cv.epochNumber());
    assertGt(staleBasis, 0, 'stale per-epoch entry remains after funded claim');

    // borrower defaults in the SAME epochNumber
    _stopEpochAndCheckPrices(0, initialProvidedApr, 0);
    _checkDefault();

    uint256 recovered = honestBasis * recoveryRatio / ONE_TRANCHE;
    deal(defaultUnderlying, manager, recovered);
    vm.startPrank(manager);
    IERC20Detailed(defaultUnderlying).approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();

    // recovery price is computed on honestBasis + staleBasis -> honest users underpaid
    assertLt(cv.defaultRecoveryPrice(), recoveryRatio, 'recovery price diluted by stale claim');

    // stale user's share of the reserve can never be paid: their claim reverts
    vm.expectRevert(); // underflow: instantWithdrawsRequests[staleUser] - staleBasis
    vm.prank(staleUser);
    cdoEpoch.claimInstantWithdrawRequest();

    // victim claims less than the intended haircut
    uint256 balPre = IERC20Detailed(defaultUnderlying).balanceOf(victim);
    vm.prank(victim);
    cdoEpoch.claimWithdrawRequest();
    assertLt(
        IERC20Detailed(defaultUnderlying).balanceOf(victim) - balPre,
        cv.withdrawsRequestsByEpoch(victim, cv.defaultRecoveryEpoch()) * recoveryRatio / ONE_TRANCHE,
        'honest redeemer diluted'
    );
}
```