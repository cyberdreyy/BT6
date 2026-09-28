### Title
Stale-epoch lookup strands unfunded instant-withdraw receipts out of default recovery accounting - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The external bug is a stale-reference flaw: `vmf->ptl` is used after the page-table entry was unmapped, so the lock no longer protects the structure it points at. The analog in `IdleCreditVault` is a stale-epoch index: instant-withdraw claim basis is recorded under the epoch in which `requestInstantWithdraw` ran (`instantWithdrawsRequestsByEpoch[user][epochNumber]`, `instantWithdrawClaimsByEpoch[epochNumber]`), but `defaultPendingClaimBasis()` and `_claimDefaultedInstantWithdrawRequest()` look claims up under whatever `epochNumber` is current at finalization. `epochNumber` is bumped in `deposit()` when a new epoch starts (line 610), so unfunded instant receipts carried across an epoch boundary are silently dropped from the recovery basis.

### Finding Description
- `requestInstantWithdraw` records basis under the *request-time* epoch: `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount; instantWithdrawClaimsByEpoch[currentEpoch] += _amount` (lines 367–374).
- `defaultPendingClaimBasis()` reads `instantWithdrawClaimsByEpoch[epochNumber]` — the *finalization-time* epoch — when `pendingInstantWithdraws != 0` (lines 644–649).
- `_defaultPrefundedInstantReserve()` likewise reads `instantWithdrawClaimsByEpoch[epochNumber]` (line 719), so already-collected instant funds in the strategy are not added to `reserveAmount`.
- `_claimDefaultedInstantWithdrawRequest()` reads `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` where `defaultRecoveryEpoch = epochNumber` at finalization (lines 843–844, 693).
- `epochNumber` increments inside `deposit()` whenever the CDO calls it while `isEpochRunning()` (lines 607–610), i.e. during `stopEpoch`/`startEpoch`. The contract's own docstring (lines 636–640) acknowledges that `pendingInstantWithdraws` can remain non-zero across `startEpoch` when collected cash only partially covers the instant queue — precisely the case where the epoch key has moved on while the claim data stayed behind.

Sequence: during epoch N a user calls `requestInstantWithdraw`; `collectInstantWithdrawFunds` only partially funds it, leaving `pendingInstantWithdraws > 0`. `startEpoch` begins epoch N+1 (`epochNumber` becomes N+1). The borrower defaults in epoch N+1; `finalizeDefaultRecovery` runs with `epochNumber = N+1`, but the instant basis lives under key N. `instantWithdrawClaimsByEpoch[N+1]` is 0 (or only counts new N+1 requests), so the carried-over instant basis is excluded from `totalBasis`, the prefunded instant cash is excluded from `reserveAmount`, and `defaultRecoveryEpoch` claims for that user return 0 basis while their `instantWithdrawsRequests` balance is still fully burned and routed to `_transferFundedClaim`.

### Impact Explanation
Two outcomes, both value-negative:

1. The excluded basis inflates `defaultRecoveryPrice` (`reserveAmount * 1e18 / totalBasis` at line 688 is computed over a too-small denominator), overpaying other defaulted claimants from `defaultRecoveryReserve`. The instant user's claim then falls through to `claimInstantWithdrawRequest`'s funded path, which burns the full `instantWithdrawsRequests[_user]` and calls `_transferFundedClaim` — which pays at par with no haircut, spending non-reserve balance (or reverting once it is exhausted). Other claimants' recovery is diluted and later claims can revert due to reserve underflow in `_transferDefaultRecovery` (line 915).
2. If the strategy's free balance cannot cover the unhaircutted claim after the reserve guard (`balance - reserve < amount`, line 904), `claimInstantWithdrawRequest` permanently reverts — permanent freezing of the user's receipt, which includes funded principal.

An unprivileged user only needs to place an ordinary instant withdraw before the epoch boundary; the rest is honest manager/borrower sequencing (partial instant funding, epoch start, borrower default, manager `finalizeDefault`).

### Likelihood Explanation
Requires an instant-withdraw request left partially unfunded across a `startEpoch` (explicitly contemplated by the in-code comments at lines 636–640) followed by a borrower default and `finalizeDefault`. No privileged malicious behavior is needed; borrower default is a normal protocol event. The mismatch is deterministic once the preconditions hold — it is not dependent on rounding or attacker timing beyond making the request in the prior epoch. Caveat: I did not fully trace `IdleCDOEpochVariant.startEpoch`/`stopEpoch` to confirm every path that leaves `pendingInstantWithdraws > 0` while bumping `epochNumber`, but the code comments confirm this state is reachable by design.

### Recommendation
Track instant-claim basis by the epoch in which it was recorded, not the current epoch. Options: persist the epoch key for outstanding instant claims (e.g., a `pendingInstantEpoch` storage slot set on first unfunded carry-over, or aggregate `instantWithdrawClaimsByEpoch` into a global `instantWithdrawClaimsTotal` incremented at request and decremented at claim), and have `defaultPendingClaimBasis`, `_defaultPrefundedInstantReserve`, and `_claimDefaultedInstantWithdrawRequest` consume that carried-over basis instead of indexing by `epochNumber`/`defaultRecoveryEpoch`. Symmetrically, `defaultRecoveryEpoch` clearing should iterate all epochs with nonzero user basis, not only the finalization epoch.

### Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
// Foundry fork test against the repo's existing harness style (test/foundry/IdleCreditVault.t.sol).
pragma solidity ^0.8.0;

import "test/foundry/IdleCreditVault.t.sol"; // reuse helpers: _depositWithUser, _startEpochAndCheckPrices, _checkDefault

contract StaleEpochInstantClaimTest is IdleCreditVaultTest {
    function testInstantReceiptAcrossEpochBoundaryDroppedFromRecovery() external {
        uint256 amount = 10_000 * ONE_SCALE;
        // enable instant withdraws
        vm.prank(manager);
        cdoEpoch.setInstantWithdrawParams(3 days, 1.5e18, true);

        // Victim deposits and requests an instant withdraw during epoch N (buffer phase)
        _depositWithUser(victim, amount, true);
        vm.prank(victim);
        cdoEpoch.requestInstantWithdraw(amount / 2, address(AAtranche));

        IdleCreditVault strat = IdleCreditVault(address(strategy));
        uint256 reqEpoch = strat.epochNumber();
        assertEq(strat.instantWithdrawsRequestsByEpoch(victim, reqEpoch), amount / 2);

        // CDO only partially funds the instant queue -> pendingInstantWithdraws stays > 0
        uint256 partial = amount / 4;
        deal(defaultUnderlying, address(cdoEpoch), partial);
        vm.prank(manager);
        cdoEpoch.getInstantWithdrawFunds(); // collects only `partial` worth
        assertGt(strat.pendingInstantWithdraws(), 0);

        // Epoch rolls: stopEpoch/startEpoch bump epochNumber via deposit()
        _startEpochAndCheckPrices(0);
        uint256 newEpoch = strat.epochNumber();
        assertEq(newEpoch, reqEpoch + 1);
        // claim basis still sits under the OLD epoch key
        assertEq(strat.instantWithdrawClaimsByEpoch(reqEpoch), amount / 2);
        assertEq(strat.instantWithdrawClaimsByEpoch(newEpoch), 0);

        // Borrower defaults in epoch N+1; manager finalizes recovery
        deal(defaultUnderlying, borrower, 0);
        vm.warp(cdoEpoch.epochEndDate() + 1);
        vm.prank(manager);
        cdoEpoch.stopEpoch(0, 0);
        _checkDefault();

        uint256 recovered = amount / 2;
        deal(defaultUnderlying, manager, recovered);
        vm.startPrank(manager);
        underlying.approve(address(strategy), recovered);
        cdoEpoch.finalizeDefault(recovered, manager);
        vm.stopPrank();

        // BUG: carried-over instant basis was never included in totalBasis,
        // so recoveryPrice is computed as if the victim's unfunded claim did not exist,
        // and _claimDefaultedInstantWithdrawRequest finds 0 basis under defaultRecoveryEpoch.
        uint256 balPre = underlying.balanceOf(victim);
        vm.prank(victim);
        cdoEpoch.claimInstantWithdrawRequest();
        uint256 paid = underlying.balanceOf(victim) - balPre;

        // Either the victim is overpaid at par (draining non-reserve funds while
        // everyone else is haircut), or the call reverts once free balance is
        // exhausted, permanently freezing the claim. Neither matches the haircut
        // applied to every other defaulted claimant.
        assertTrue(paid == amount / 2 || paid == 0, "stale-epoch claim escapes haircut or freezes");
    }
}
```

Caveat: exact helper names (`getInstantWithdrawFunds`, `_checkDefault`) should be matched to the actual `IdleCDOEpochVariant` API in the test harness; the core assertions — basis recorded under the request epoch, lookup under the bumped epoch, and the resulting par payout or frozen claim — follow directly from the cited lines.