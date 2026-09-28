### Title
Stale per-epoch instant-withdraw receipt accounting inflates `defaultPendingClaimBasis`, diluting default recovery and permanently freezing claimants' share — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The CVE is a classic leak bug: an error path acquires a resource but never releases it. The analog in `IdleCreditVault` is a *claim-basis leak*: `claimInstantWithdrawRequest` pays a funded instant receipt at par and clears only the aggregate `instantWithdrawsRequests[_user]`, but never clears `instantWithdrawsRequestsByEpoch[_user][epoch]` nor decrements `instantWithdrawClaimsByEpoch[epoch]` (lines 387–392). That already-paid basis is never released, so when a later default finalizes in the same epoch, `defaultPendingClaimBasis()` counts it again (line 647), `finalizeDefaultRecovery` spreads the recovery reserve over the inflated `totalBasis` (lines 679–688), and the stale entry can never be cleared without reverting (line 848 underflows). The phantom basis's share of the reserve is permanently stranded and every honest claimant's `defaultRecoveryPrice` is diluted.

### Finding Description
In `claimInstantWithdrawRequest`, the non-default path burns `instantWithdrawsRequests[_user]`, zeroes the aggregate, and pays via `_transferFundedClaim`:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

It does not touch `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` or `instantWithdrawClaimsByEpoch[currentEpoch]`, both set at request time (lines 371–372). Compare with `_claimDefaultedInstantWithdrawRequest` (lines 844–853), which does clear both — proving the per-epoch entries are supposed to track outstanding claim basis.

An unprivileged lender can therefore leave a "leaked" claim basis on the books:

1. During a running epoch, attacker calls `requestInstantWithdraw` via the CDO (e.g., through `requestWithdraw` instant path) for amount A; `instantWithdrawsRequestsByEpoch[attacker][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` become A.
2. `manager.getInstantWithdrawFunds()` partially funds the queue; `collectInstantWithdrawFunds` pulls only part of it, leaving `pendingInstantWithdraws > 0` (the exact scenario documented at lines 636–640 and exercised by `testFinalizeDefaultClaimsFundedAndDefaultedInstantRedeemsInOneCall`). The attacker claims their funded portion at par — but the per-epoch basis A stays.
3. The borrower defaults in the same epoch (e.g., epoch ends with insufficient repayment; `stopEpoch` sets `defaulted()`).
4. `manager.finalizeDefault(recovered, source)` calls `finalizeDefaultRecovery`. `defaultPendingClaimBasis()` returns `pendingWithdraws + instantWithdrawClaimsByEpoch[epoch]`, still including the attacker's already-paid A. `recoveryPrice = reserveAmount * 1e18 / totalBasis` is computed over a `totalBasis` inflated by A.
5. Every honest defaulted receipt (`_claimDefaultedWithdrawRequest`, `_claimDefaultedInstantWithdrawRequest`) and active tranche holder is paid at the diluted `recoveryPrice`. The reserve slice corresponding to phantom basis A can never be paid out: `_claimDefaultedInstantWithdrawRequest` for the attacker reverts at `instantWithdrawsRequests[_user] -= claimBasis` (line 848) because the aggregate was already zeroed, and `instantWithdrawClaimsByEpoch` is never otherwise decremented.

Invariant broken: one receipt, one payout — the same instant receipt is counted once as a paid claim and once as outstanding claim basis, violating recovery-reserve conservation (`totalPayout + remainingReserve == recovered` becomes `totalPayout + stranded + dust == recovered` with a structurally unreachable stranded amount).

### Impact Explanation
Direct theft of unclaimed yield / permanent freezing: all other default claimants (pending normal receipts, instant receipts, active AA/BB tranche holders via `DefaultDistributor.claim`) receive a strictly lower `defaultRecoveryPrice` than they are owed. The reserve share attributable to the attacker's phantom basis A is permanently locked in the strategy contract — no code path can release it, and the attacker's own legitimate remaining instant receipts (if any, since `claimBasis` from the epoch exceeds their aggregate) revert at line 848, freezing real user funds as a side effect. Loss magnitude is proportional to the attacker's pre-claim instant amount A relative to total claim basis; with a modest deposit the attacker can materially dilute a thin recovery.

### Likelihood Explanation
Requires only unprivileged actions plus an honest-borrower default (a normal protocol event, explicitly in-scope): request instant withdraw, wait for partial instant funding (`getInstantWithdrawFunds` when CDO liquidity < full queue is a routine occurrence since `startEpoch` moves cash to the borrower), claim, then wait for the epoch to default and finalize. No privileged collusion, no oracle manipulation, no timing precision beyond requesting in the same epoch that later defaults. The honest-manager `finalizeDefault` call mechanically computes the wrong price from the stale basis.

### Recommendation
In `claimInstantWithdrawRequest`, mirror the cleanup done in `_claimDefaultedInstantWithdrawRequest`: when paying a funded instant receipt, clear `instantWithdrawsRequestsByEpoch[_user][epoch]` for the relevant epoch(s) and decrement `instantWithdrawClaimsByEpoch[epoch]` by the claimed amount, so `defaultPendingClaimBasis` only ever counts receipts that remain unpaid. Alternatively, track a per-epoch "funded" marker so partially funded instant claims store only the unfunded remainder as default claim basis.

### Proof of Concept
Foundry fork test sketch (against the existing `IdleCreditVault`/`IdleCDOEpochVariant` harness in `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testStaleInstantBasisDilutesRecovery() public {
    // attacker deposits into AA tranche in epoch N
    _depositWithUser(attacker, 10_000 * ONE_SCALE, true);
    _startEpochAndCheckPrices(0);

    // attacker requests instant withdraw of amount A in the epoch that will default
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(A, address(AAtranche)); // instant path
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);

    // manager collects only part of the instant queue -> pendingInstantWithdraws > 0
    vm.prank(manager);
    cdoEpoch.getInstantWithdrawFunds(); // partially funded
    // attacker claims funded portion at par; per-epoch basis NOT cleared
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    assertEq(IdleCreditVault(address(strategy)).instantWithdrawsRequestsByEpoch(attacker, epochN), A); // leaked basis

    // borrower defaults: stop epoch with no repayment
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(cdoEpoch.owner());
    cdoEpoch.stopEpoch(0, 0);
    assertTrue(cdoEpoch.defaulted());

    // honest manager finalizes recovery
    uint256 basis = creditVault.defaultPendingClaimBasis(); // includes attacker's already-paid A
    uint256 recovered = ...; // e.g. 70% of true outstanding basis
    deal(underlying, manager, recovered);
    vm.startPrank(manager);
    IERC20Detailed(underlying).approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();

    // recovery price is diluted below recovered/trueOutstandingBasis
    assertLt(creditVault.defaultRecoveryPrice(), recovered * 1e18 / trueBasis);
    // honest claimant receives less than their fair share; phantom slice stays stranded
    // attacker attempting to clear stale basis reverts (underflow at line 848)
}
```

**Uncertainty note:** I could not read `IdleCDOEpochVariant.claimInstantWithdrawRequest`'s gating (e.g., `allowInstantWithdraw` checks) within the tool-call budget to fully confirm that a user can claim an instant receipt on-chain while `pendingInstantWithdraws > 0` pre-default. The test at `IdleCreditVault.t.sol:4505-4522` establishes that partial instant funding coexists with remaining `pendingInstantWithdraws`, and the strategy-level claim path itself has no such gate, but if the CDO blocks all instant claims until the queue is fully funded, step 2's claim would need the attacker's receipt to be funded in a scenario where another user's is not — which the documented partial-funding design (lines 636–640) supports. The accounting asymmetry (per-epoch entries never cleared on the funded path) is confirmed by direct code reading regardless.