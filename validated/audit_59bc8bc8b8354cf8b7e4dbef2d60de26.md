### Title
Unfunded instant-withdraw receipts from earlier epochs are dropped from default-recovery basis — later claimants' recovery reserve is drained and old receipt claims freeze permanently - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` tracks instant-withdraw receipts per epoch (`instantWithdrawsRequestsByEpoch`, `instantWithdrawClaimsByEpoch`) but keeps `pendingInstantWithdraws` as a single cross-epoch aggregate. During `finalizeDefaultRecovery`, only the *current* epoch's instant basis (`instantWithdrawClaimsByEpoch[epochNumber]`) is added to the recovery basis and only the current epoch is treated as prefunded reserve. An unfunded instant receipt created in an earlier epoch — a "child" claim that outlives its parent epoch — is counted in `pendingInstantWithdraws` (so `defaultInstantWithdrawsFinalized` is set and recovery logic engages) but is silently excluded from both `totalBasis` and the per-epoch claim-clearing path. This mirrors the kernel bug: a child object (the old-epoch receipt) still referenced by the parent's aggregate counter is never refcounted into the new session's accounting.

### Finding Description
Relevant code:

- `requestInstantWithdraw` adds to the aggregate `pendingInstantWithdraws` *and* to `instantWithdrawsRequestsByEpoch[user][epochNumber]` / `instantWithdrawClaimsByEpoch[epochNumber]` (lines 366–374). Nothing forces an old-epoch receipt to be claimed or funded before a new epoch starts.
- `defaultPendingClaimBasis` (lines 644–649) adds only `instantWithdrawClaimsByEpoch[epochNumber]` — the default epoch — gated on `pendingInstantWithdraws != 0`.
- `_defaultPrefundedInstantReserve` (lines 716–723) computes prefunded reserve as `instantWithdrawClaimsByEpoch[epochNumber] - pendingInstantWithdraws`. Because `pendingInstantWithdraws` aggregates *all* epochs while `instantBasis` is only the current epoch, when an old unfunded receipt exists `instantBasis <= pendingInstant` yields `prefundedReserve = 0`, even though the strategy may hold funded cash for the old receipt.
- `claimInstantWithdrawRequest` (lines 380–393) clears only `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` via `_claimDefaultedInstantWithdrawRequest`, then pays the *entire remaining aggregate* `instantWithdrawsRequests[_user]` at par through `_transferFundedClaim`.

Consequences:

1. `totalBasis` under-counts real claims → `recoveryPrice = reserveAmount / totalBasis` is computed too high. Default-epoch instant and normal receipt holders are over-paid pro-rata from `defaultRecoveryReserve`; the last claimant's `_transferDefaultRecovery` underflows/reverts (line 915), permanently freezing their recovery.
2. The old-epoch instant receipt itself is never haircut: it stays in `instantWithdrawsRequests[user]` and falls to `_transferFundedClaim`, whose guard `balance - reserve < amount` (lines 900–905) reverts because post-finalization the strategy balance equals the recovery reserve. The old receipt's claim is permanently frozen — it can never be claimed, and `requestWithdraw` also reverts (line 249 `instantWithdrawsRequests[_user] != 0` → `NotAllowed`), so the user's tranche position is bricked too.

### Impact Explanation
Broken invariant: fair recovery distribution and solvency of `defaultRecoveryReserve`. Real unclaimed-yield/principal impact: an attacker holding (or positioned ahead of) a stale-epoch instant receipt causes (a) other recovery claimants to receive zero/insufficient payout due to reserve underflow, and/or (b) the stale receipt holder's funds frozen permanently with no recovery path. Loss is quantified as `staleInstantBasis * defaultRecoveryPrice / 1e18` stolen from or locked out of the reserve.

### Likelihood Explanation
Requires only an unprivileged lender who made an instant-withdraw request that remained unfunded across an epoch boundary (`pendingInstantWithdraws` never fully collected by `collectInstantWithdrawFunds`), followed by a borrower default and `finalizeDefaultRecovery` in a later epoch. No privileged misbehavior needed; the CDO honestly calls `getInstantWithdrawFunds`/`stopEpoch`. The guard `instantBasis > pendingInstant` cannot fire for cross-epoch receipts because `instantBasis` is scoped to the current epoch only.

### Recommendation
Make `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` account for *all* outstanding instant claims, not only `instantWithdrawClaimsByEpoch[epochNumber]` — either maintain a global `instantWithdrawClaimsTotal`, or iterate/forbid unfunded instant receipts older than the default epoch. Symmetrically, `_claimDefaultedInstantWithdrawRequest` should clear the user's full `instantWithdrawsRequests[user]` basis (all epochs) at `defaultRecoveryPrice` rather than only the default-epoch bucket.

### Proof of Concept
Foundry fork test sketch (extend `test/foundry/IdleCreditVault.t.sol` helpers `_depositWithUser`, `_startEpochAndCheckPrices`, `_stopEpochAndCheckPrices`, `_checkDefault`):

```solidity
function testStaleEpochInstantReceiptExcludedFromRecoveryBasis() external {
    // enable instant withdraws
    uint256 d = cdoEpoch.instantWithdrawDelay();
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(d, 1000, false);

    address staler = makeAddr('stale-instant');
    address pending = makeAddr('pending');
    _depositWithUser(staler, 10_000 * ONE_SCALE, true);
    _depositWithUser(pending, 10_000 * ONE_SCALE, true);

    // Epoch 0: staler requests instant withdraw; CDO never collects funds
    vm.prank(staler);
    cdoEpoch.requestInstantWithdraw(5_000 * ONE_SCALE); // pendingInstantWithdraws = 5k
    // (do NOT call getInstantWithdrawFunds -> receipt stays unfunded)

    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

    // Epoch 1: pending user requests normal withdraw, then default
    vm.prank(pending);
    cdoEpoch.requestWithdraw(0, address(AAtranche));
    _startEpochAndCheckPrices(1);
    _stopEpochAndCheckPrices(1, initialProvidedApr, 0);
    _checkDefault();

    IdleCreditVault cv = IdleCreditVault(address(strategy));
    // BUG: basis omits staler's 5k old-epoch instant receipt
    assertEq(cv.defaultPendingClaimBasis(), /* pending basis only, missing 5k */);

    // finalize at intended ratio; recoveryPrice is inflated because totalBasis is too low
    uint256 recovered = (cdoEpoch.getContractValue() + cdoEpoch.expectedEpochInterest()
        - cdoEpoch.pendingWithdrawFees() + cv.defaultPendingClaimBasis()) * 7e17 / 1e18;
    deal(defaultUnderlying, manager, recovered);
    vm.prank(manager);
    IERC20Detailed(defaultUnderlying).approve(address(strategy), recovered);
    vm.prank(owner);
    cdoEpoch.finalizeDefault(recovered, manager);

    // staler can never claim: par path reverts on reserve guard
    vm.prank(staler);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.claimInstantWithdrawRequest();
    // staler is bricked: cannot even requestWithdraw post-default
    vm.prank(staler);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.requestWithdraw(0, address(AAtranche));
}
```

Caveat: exact helper signatures (`requestInstantWithdraw` wrapper name on the CDO and expected-funds helpers) may differ slightly in the test harness; the core assertion — `defaultPendingClaimBasis()` omitting a stale-epoch unfunded instant receipt while `pendingInstantWithdraws != 0` sets `defaultInstantWithdrawsFinalized` — follows directly from lines 644–649 and 694–696.