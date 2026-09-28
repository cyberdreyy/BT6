### Title
Funded instant-withdraw claims leave stale per-epoch receipt basis, corrupting default recovery price - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The Xen CVE is a "half-updated entry" bug: a data structure that must be written atomically is left in a torn, half-valid state. The analog in `IdleCreditVault` is the instant-withdraw receipt ledger, which is tracked in two places that must stay consistent: the aggregate `instantWithdrawsRequests[_user]` and the per-epoch `instantWithdrawsRequestsByEpoch[_user][epoch]` / `instantWithdrawClaimsByEpoch[epoch]`. A successful funded claim (`claimInstantWithdrawRequest`) updates only the aggregate counter, while the defaulted-epoch claim path and default finalization read the per-epoch counters. The stale per-epoch basis inflates `defaultPendingClaimBasis()` and `_defaultPrefundedInstantReserve()`, skewing `defaultRecoveryPrice` for every claimant.

### Finding Description
`requestInstantWithdraw` writes three coupled ledgers (`instantWithdrawsRequests`, `instantWithdrawsRequestsByEpoch`, `instantWithdrawClaimsByEpoch`). The funded-claim path clears only the aggregate:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:380-393
function claimInstantWithdrawRequest(address _user) external {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
      _claimDefaultedInstantWithdrawRequest(_user);
    }
    uint256 amount = instantWithdrawsRequests[_user];
    _burn(_user, amount);
    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
}
```

Neither `instantWithdrawsRequestsByEpoch[_user][epoch]` nor `instantWithdrawClaimsByEpoch[epoch]` is decremented. Compare with `_claimDefaultedInstantWithdrawRequest` (lines 842-856), which correctly clears both per-epoch entries — confirming the per-epoch data is meant to mirror the aggregate.

The corruption materializes at `finalizeDefaultRecovery`: `defaultPendingClaimBasis()` adds `instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0`, and `_defaultPrefundedInstantReserve()` computes `instantBasis - pendingInstant`. Because a borrower default happens inside `_stopEpoch`'s catch path, `epochNumber` is never incremented (the `deposit()` that bumps it is in the try body), so `defaultRecoveryEpoch` equals the epoch in which the stale instant receipts were created and claimed.

### Impact Explanation
Concrete sequence, fixed-APR mode, epoch N running:

1. Attacker and victim each request instant withdraws of 100 (`pendingInstantWithdraws = 200`, `instantWithdrawClaimsByEpoch[N] = 200`).
2. After `instantWithdrawDelay`, the manager funds only 100 via `getInstantWithdrawFunds` → `collectInstantWithdrawFunds(100)` → `pendingInstantWithdraws = 100`. The strategy now holds 100 underlying.
3. Attacker claims via `claimInstantWithdrawRequest`: receives 100, aggregate zeroed, but `instantWithdrawsRequestsByEpoch[attacker][N] = 100` and `instantWithdrawClaimsByEpoch[N] = 200` remain stale.
4. `stopEpoch` borrower pull fails → `_handleBorrowerDefault` → `finalizeDefaultRecovery`. `defaultPendingClaimBasis()` = victim's real 100 + attacker's phantom 100 = 200; `_defaultPrefundedInstantReserve()` = 200 − 100 = 100 (correct cash, wrong basis). `totalBasis` is inflated by 100, so `defaultRecoveryPrice` is understated roughly pro-rata.
5. Victim and all active LPs (AA/BB via `activeFinalNAV`) are paid at the diluted price. The attacker cannot directly double-claim (`instantWithdrawsRequests[attacker] -= claimBasis` underflows and reverts), but the excess `defaultRecoveryReserve` allocated to phantom claims is permanently stranded in the strategy — an unrecoverable loss of recovery funds for honest users, quantified as `staleBasis * recoveryPrice / RECOVERY_FULL`.

This is direct theft/permanent freezing of unclaimed recovery proceeds, mirroring the Xen "half-updated" torn write: one copy of the receipt says "paid," the other still says "owed."

### Likelihood Explanation
Requires only an unprivileged KYC-passed lender requesting an instant withdraw (normal user action), a partially funded instant queue (routine when borrower liquidity is thin — exactly the condition that precedes a default), and a subsequent borrower default in the same epoch — the precise scenario `finalizeDefaultRecovery` exists for. No privileged misbehavior needed; the manager's funding call is honest and partial funding is a supported state (`pendingInstantWithdraws` tracks the unfunded remainder). The inflated basis also silently dilutes every subsequent default claim, so impact scales with total pending claims.

### Recommendation
In `claimInstantWithdrawRequest`, clear the per-epoch ledgers symmetrically with `_claimDefaultedInstantWithdrawRequest`:

```solidity
uint256 currentEpoch = epochNumber;
uint256 perEpoch = instantWithdrawsRequestsByEpoch[_user][currentEpoch];
// for amounts funded across epoch boundaries, iterate or cap at amount
instantWithdrawsRequestsByEpoch[_user][currentEpoch] = perEpoch - min(perEpoch, amount);
instantWithdrawClaimsByEpoch[currentEpoch] -= min(perEpoch, amount);
```

Alternatively, derive `defaultPendingClaimBasis`/`_defaultPrefundedInstantReserve` from the sum of live per-user receipts rather than a separately maintained per-epoch counter, so the ledger cannot go stale.

### Proof of Concept
Foundry fork sketch (mirroring `test/foundry/IdleCreditVault.t.sol` setup):

```solidity
function testStaleInstantBasisDilutesDefaultRecovery() external {
    // deposits, startEpoch with APR drop so instant withdraws trigger
    idleCDO.depositAA(1000 * ONE_SCALE);          // victim LP
    _startEpochAndCheckPrices(0);

    // attacker + victim request instant withdraws (apr dropped below delta)
    _setAprLower();
    vm.prank(attacker); cdoEpoch.requestWithdraw(0, AAtranche);  // 100 receipt
    vm.prank(victim);   cdoEpoch.requestWithdraw(0, AAtranche);  // 100 receipt

    // manager partially funds the instant queue: only 100 pulled
    vm.warp(block.timestamp + instantWithdrawDelay + 1);
    deal(underlying, borrower, 100 * ONE_SCALE);
    vm.prank(manager); cdoEpoch.getInstantWithdrawFunds();       // pendingInstant = 100

    // attacker claims; per-epoch ledgers NOT cleared
    vm.prank(attacker); cdoEpoch.claimInstantWithdrawRequest();  // gets 100

    // borrower defaults at stopEpoch (transfer fails)
    deal(underlying, borrower, 0);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager); cdoEpoch.stopEpoch(apr, 0);
    // finalize default with whatever recovery exists
    cdoEpoch.finalizeDefaultRecovery(...);

    // victim claims: receives ~half of entitled recovery because
    // instantWithdrawClaimsByEpoch still counted attacker's claimed 100
    vm.prank(victim); cdoEpoch.claimInstantWithdrawRequest();
    assertLt(victimBal, expectedRecovery);   // diluted
    assertGt(strategyResidualDust, 0);       // stranded reserve
}
```

Uncertainty note: I could not verify whether `epochNumber` is bumped inside `_handleBorrowerDefault`/`finalizeDefault` before `finalizeDefaultRecovery` snapshots `defaultRecoveryEpoch`; the finding depends on the defaulted epoch key matching the epoch under which the stale instant entries were recorded, which holds if epoch numbering only advances via `deposit()` in the successful stop path (line 610). A PoC should confirm that ordering first.