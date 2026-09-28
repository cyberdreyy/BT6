### Title
Funded withdraw/instant-withdraw claims never unlink their per-epoch receipt nodes, so default finalization dereferences stale claim basis and freezes or dilutes recovery claims - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary

In `IdleCreditVault`, when a withdraw or instant-withdraw receipt is claimed through the funded path, only the aggregate counters are cleared — the per-epoch bookkeeping nodes (`withdrawsRequestsByEpoch[_user][epoch]`, `instantWithdrawsRequestsByEpoch[_user][epoch]`, `instantWithdrawClaimsByEpoch[epoch]`) are left linked, exactly like the Zephyr bug where freeing an armed `k_timer` leaves its node dangling in `_timeout_q`. If the borrower later defaults while `epochNumber` still equals that request epoch, `finalizeDefaultRecovery`/`defaultPendingClaimBasis` and the `_claimDefaulted*` handlers dereference the stale node: the recovery basis is inflated by already-paid claims and the per-user claim math underflows, permanently reverting the victim's claim calls and mispricing `defaultRecoveryPrice`.

### Finding Description

The claim paths free the receipt without clearing its per-epoch linkage:

- `claimInstantWithdrawRequest` burns the receipt, zeroes `instantWithdrawsRequests[_user]`, and pays out, but never clears `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` or decrements `instantWithdrawClaimsByEpoch[currentEpoch]` (`IdleCreditVault.sol:380-392`, `371-374` where they are set).
- `_claimFundedWithdrawRequest` zeroes `withdrawsRequests[_user]` and `lastWithdrawRequest[_user]` but leaves `withdrawsRequestsByEpoch[_user][epoch]` untouched (`IdleCreditVault.sol:338-349`). Only `_clearWithdrawClaimForEpoch` clears the per-epoch entry, and it is invoked solely by the loss-adjusted/defaulted paths (`IdleCreditVault.sol:811-837`).

The stale node is then dereferenced at default finalization, which records `defaultRecoveryEpoch = epochNumber` (`IdleCreditVault.sol:693`):

- `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` — still containing already-claimed amounts — into the recovery basis (`IdleCreditVault.sol:644-649`), and `_defaultPrefundedInstantReserve` treats `instantBasis - pendingInstant` as strategy-held reserve even though part of it was already paid out to claimers (`IdleCreditVault.sol:716-723`).
- `_claimDefaultedInstantWithdrawRequest` reads `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]` (stale), then executes `instantWithdrawsRequests[_user] -= claimBasis` (`IdleCreditVault.sol:842-855`). Because the aggregate was reset to 0 at claim time, any user who claimed a funded instant receipt and opened a new instant request in the same epoch always has `claimBasis > instantWithdrawsRequests[_user]` → arithmetic underflow → revert. Since `_claimDefaultedInstantWithdrawRequest` runs inside `claimInstantWithdrawRequest` before paying, the user's claim is permanently bricked.
- Symmetrically, `_claimDefaultedWithdrawRequest` → `_clearWithdrawClaimForEpoch` computes `claimBasis` from stale `withdrawsRequestsByEpoch` and executes `withdrawsRequests[_user] -= normalAmount` (`IdleCreditVault.sol:772-784`, `815-820`), underflow-reverting for users who already claimed a funded receipt from the defaulted epoch. Because `claimWithdrawRequest` calls `_claimPostDefaultWithdrawRequest` first and then the defaulted path in the same transaction (`IdleCreditVault.sol:301-313`), the revert also freezes legitimately funded post-default claims.

Concrete sequence (instant mode, epoch E running, unprivileged lender):

1. Attacker/victim is a KYC'd tranche holder who calls `requestInstantWithdraw`; CDO calls `getInstantWithdrawFunds`/`collectInstantWithdrawFunds`, funding the request.
2. User calls `claimInstantWithdrawRequest` → paid; `instantWithdrawsRequestsByEpoch[user][E]` and `instantWithdrawClaimsByEpoch[E]` remain non-zero (dangling node).
3. User (or any other user who did the same) makes a second `requestInstantWithdraw` in epoch E.
4. Borrower defaults in epoch E; owner/manager runs `finalizeDefault`/`finalizeDefaultRecovery` → `defaultRecoveryEpoch = E`, `defaultInstantWithdrawsFinalized = true` (since `pendingInstantWithdraws != 0`).
5. Every `claimInstantWithdrawRequest`/`claimWithdrawRequest` for that user now reverts on the stale-node subtraction, and `defaultPendingClaimBasis` is inflated by the already-paid amount, lowering `defaultRecoveryPrice` for all honest claimers.

### Impact Explanation

Two fund-impacting consequences, both from unprivileged actions plus an honest borrower default:

- **Permanent freezing of claims**: any user who claimed a funded withdraw/instant receipt in epoch E and still has (or later opens) a receipt in epoch E can never claim after finalization — arithmetic underflow makes the call revert deterministically, freezing their pending receipts and post-default recovery claims.
- **Recovery dilution / insolvency**: `instantWithdrawClaimsByEpoch[E]` double-counts claimed amounts, so `totalBasis` in `finalizeDefaultRecovery` is inflated while `prefundedReserve` counts underlying that no longer exists in the strategy. `defaultRecoveryPrice` is mispriced and `defaultRecoveryReserve` is drained against phantom basis — honest defaulted-epoch claimers receive less than entitled, or the last claimers' transfers revert when the reserve is exhausted.

Existing guards don't stop it: `_ensureDefaultRecoveryInitialized`, the `_hasWithdrawRequest` check in `requestWithdraw`, and the `NotAllowed` reverts all operate on aggregate state; none of them clear or validate the per-epoch nodes that are stale.

### Likelihood Explanation

Requires (a) instant-withdraw or epoch-withdraw receipts funded and claimed within the same `epochNumber` in which the borrower later defaults, and (b) an honest `_handleBorrowerDefault` + `finalizeDefaultRecovery`. Instant withdrawals exist precisely to be claimed intra-epoch, so condition (a) is a normal usage pattern; condition (b) is an honest privileged action the attacker merely sequences around. No attacker-controlled privileged call is needed — any tranche holder's ordinary claim creates the dangling node.

### Recommendation

Mirror the Zephyr fix (`k_timer_cleanup` unlinking the node before free): when a funded receipt is claimed, unlink its per-epoch nodes.

- In `claimInstantWithdrawRequest`, before/while zeroing the aggregate, iterate or track the user's request epoch and clear `instantWithdrawsRequestsByEpoch[_user][epoch]` and decrement `instantWithdrawClaimsByEpoch[epoch]` by the claimed amount (or store the request epoch alongside the receipt).
- In `_claimFundedWithdrawRequest`, clear `withdrawsRequestsByEpoch[_user][lastWithdrawRequest[_user]]` (or each contributing epoch) when paying the aggregate, so `_clearWithdrawClaimForEpoch` cannot resurrect it.
- In `defaultPendingClaimBasis`/`_defaultPrefundedInstantReserve`, use only still-outstanding basis (e.g., subtract already-claimed amounts from `instantWithdrawClaimsByEpoch` at claim time) so ghost basis cannot enter recovery pricing.

### Proof of Concept

Foundry fork PoC sketch on the in-scope `IdleCreditVault` + `IdleCDOEpochVariant` deployment (instant-withdraw mode):

```solidity
// test/foundry/PoCStaleEpochClaim.t.sol — instant mode vault
function testStaleInstantReceiptBricksDefaultClaim() external {
    // 1. LP deposits, epoch starts (epochNumber == E)
    _depositWithUser(USER, 100e6);
    vm.prank(manager); cdoEpoch.startEpoch();
    uint256 E = strategy.epochNumber();

    // 2. USER requests instant withdraw; CDO/borrower funds it
    vm.prank(USER); cdoEpoch.requestInstantWithdraw(amount, tranche);
    // manager/borrower getInstantWithdrawFunds flow funds pendingInstantWithdraws
    _fundInstantWithdraws(); // collectInstantWithdrawFunds pulls to strategy

    // 3. USER claims -> aggregate cleared, per-epoch node left linked
    vm.prank(USER); cdoEpoch.claimInstantWithdrawRequest();
    assertGt(strategy.instantWithdrawsRequestsByEpoch(USER, E), 0); // dangling

    // 4. USER opens a second instant request in the same epoch E
    vm.prank(USER); cdoEpoch.requestInstantWithdraw(amount2, tranche);

    // 5. Honest borrower defaults; owner finalizes recovery in epoch E
    _defaultAndFinalize(); // defaulted() = true, defaultRecoveryEpoch = E

    // 6. User's claim permanently reverts: stale basis > aggregate -> underflow
    vm.prank(USER);
    vm.expectRevert(); // panic: arithmetic underflow in _claimDefaultedInstantWithdrawRequest
    cdoEpoch.claimInstantWithdrawRequest();

    // 7. Recovery basis inflated by already-paid claims -> defaultRecoveryPrice too low
    // assert defaultPendingClaimBasis counted the claimed amount twice via
    // instantWithdrawClaimsByEpoch[E] including the step-3 claimed amount.
}
```

Uncertainty I could not fully resolve within the search index: whether `requestInstantWithdraw`/`claimInstantWithdrawRequest` are callable while `isEpochRunning()` is true and before `epochNumber` increments (the per-epoch entries are keyed on `epochNumber`, which only advances in `deposit` while running, `IdleCreditVault.sol:610`), and the exact CDO-side `_handleBorrowerDefault`/`finalizeDefault` gating. If instant claims are only possible after the epoch ends, the same-epoch precondition fails and the stale `instantWithdrawsRequestsByEpoch` entries can never match a future `defaultRecoveryEpoch`, which would reduce this to the normal-withdraw variant (claim funded → stale `withdrawsRequestsByEpoch[E]` → same underflow via `_clearWithdrawClaimForEpoch`), which requires the default to land on the request epoch that was already funded — i.e., default during the epoch in which `collectWithdrawFunds` ran.