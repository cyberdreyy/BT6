### Title
Claimed instant-withdraw receipts stay in `instantWithdrawsRequestsByEpoch`, bricking the user's recovery claim after a same-epoch borrower default - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault` records instant-withdraw receipts both in the aggregate `instantWithdrawsRequests[user]` and per-epoch in `instantWithdrawsRequestsByEpoch[user][epoch]`. The normal claim path (`claimInstantWithdrawRequest`) zeroes only the aggregate and never clears the per-epoch entry. If a user claims a funded instant receipt and then opens a second instant request in the same epoch, a subsequent borrower default finalization treats the stale per-epoch sum as the defaulted claim basis. `instantWithdrawsRequests[_user] -= claimBasis` and `_burn(_user, claimBasis)` both underflow, so `claimInstantWithdrawRequest` reverts forever and the user's still-pending instant receipt (and its share of the default-recovery reserve) is permanently frozen.

### Finding Description
- `requestInstantWithdraw` adds `_amount` to both `instantWithdrawsRequests[_user]` and `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` (`contracts/strategies/idle/IdleCreditVault.sol:366-372`).
- `claimInstantWithdrawRequest` only zeroes the aggregate (`instantWithdrawsRequests[_user] = 0`) and burns that amount; `instantWithdrawsRequestsByEpoch[_user][epoch]` is left populated (`contracts/strategies/idle/IdleCreditVault.sol:387-392`). This differs from the normal-withdraw path, where `_clearWithdrawClaimForEpoch` explicitly zeroes `withdrawsRequestsByEpoch` (`contracts/strategies/idle/IdleCreditVault.sol:815-820`).
- On a borrower default, `finalizeDefault` records `defaultRecoveryEpoch = epochNumber` and `_claimDefaultedInstantWithdrawRequest` later computes `claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch]` and subtracts it from `instantWithdrawsRequests[_user]` and from the user's token balance (`contracts/strategies/idle/IdleCreditVault.sol:842-856`).
- Sequence: during running epoch N, `allowInstantWithdraw` is enabled (either at `startEpoch` when `pendingInstant` is fully funded, `contracts/IdleCDOEpochVariant.sol:286-292`, or via `getInstantWithdrawFunds`, `contracts/IdleCDOEpochVariant.sol:558-570`). User requests instant withdraw of amount A, claims it (aggregate → 0, `byEpoch[N] = A` remains). User deposits again and requests instant withdraw of amount B (permitted: `requestInstantWithdraw` has no "existing request" gate) → aggregate = B, `byEpoch[N] = A + B`. Borrower defaults at `stopEpoch` (`_handleBorrowerDefault`), manager finalizes with `finalizeDefault`/`finalizeDefaultRecovery`.
- User calls `claimInstantWithdrawRequest` → `_claimDefaultedInstantWithdrawRequest` computes `claimBasis = A + B > instantWithdrawsRequests[user] = B` → `instantWithdrawsRequests[_user] -= claimBasis` reverts with arithmetic underflow (and even if reordered, `_burn(_user, A + B)` would exceed the B receipt balance). Every subsequent call reverts identically.

### Impact Explanation
Permanent freezing of user funds: the user's pending instant-withdraw principal B and its recovery-claim value `B * defaultRecoveryPrice / RECOVERY_FULL` can never be withdrawn — the receipt tokens cannot be redeemed through any other path and there is no admin recovery for per-user receipt state. The accounting corruption is one-directional (underflow reverts rather than overpaying), so the direct impact is the locked recovery share of the affected receipt; the inflated `instantWithdrawClaimsByEpoch[defaultEpoch]` remainder additionally distorts global default accounting for that epoch. This matches the source bug class (a crafted state/input causes a crash in a processing path) mapped to the vault's claim path: a valid transaction ordering produces an unrevertable revert — a segfault analog with direct fund impact.

### Likelihood Explanation
Likelihood is moderate: it requires three conditions, none attacker-privileged — (1) instant withdrawals funded mid-epoch so a receipt is claimable while `epochNumber` is unchanged; (2) the same wallet requests a second instant withdraw in the same epoch (natural when APR drops and a user re-tops-up to exit); (3) the borrower defaults at that epoch's `stopEpoch` and default recovery is finalized. The rules exclude "freezing after a borrower default" only in the sense of frozen-by-design default mechanics; here the freeze is an accounting bug blocking a claim that the protocol intends to honor at `defaultRecoveryPrice`. No guard catches it: `requestInstantWithdraw` doesn't check for claimed-but-stale per-epoch entries, and the default claim path doesn't cap `claimBasis` at the aggregate.

### Recommendation
Clear the per-epoch record in `claimInstantWithdrawRequest` (and in any other path that settles `instantWithdrawsRequests`), e.g. delete `instantWithdrawsRequestsByEpoch[_user][epochNumber]` (and decrement `instantWithdrawClaimsByEpoch[epochNumber]`) when a funded instant receipt is claimed before default. Alternatively, in `_claimDefaultedInstantWithdrawRequest`, compute `claimBasis = min(instantWithdrawsRequestsByEpoch[_user][defaultEpoch], instantWithdrawsRequests[_user])` so stale entries cannot exceed the outstanding aggregate.

### Proof of Concept
```solidity
// Foundry fork-style PoC (adapt helpers from test/foundry/IdleCreditVault.t.sol)
function testClaimedInstantReceiptStalesByEpochEntry() external {
    // 1) deposit + startEpoch with fully funded instant withdraws
    idleCDO.depositAA(20_000 * ONE_SCALE);
    _startEpochAndCheckPrices(0); // pendingInstant <= contract balance -> allowInstantWithdraw = true

    // force APR drop so instant-withdraw mode is used
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0); // lastEpochApr > currentApr + delta

    // 2) user instant-withdraws A, funds arrive, user claims -> aggregate 0, byEpoch[N] = A
    uint256 trancheA = IERC20(AAtranche).balanceOf(user1) / 2;
    vm.prank(user1);
    cdoEpoch.requestWithdraw(trancheA, address(AAtranche)); // instant path mints receipt A
    vm.prank(user1);
    cdoEpoch.claimInstantWithdrawRequest();               // burns A; byEpoch[N] still = A

    // 3) same user requests instant withdraw B in the SAME epoch
    uint256 trancheB = IERC20(AAtranche).balanceOf(user1);
    vm.prank(user1);
    cdoEpoch.requestWithdraw(trancheB, address(AAtranche)); // aggregate = B, byEpoch[N] = A + B

    // 4) borrower defaults at stopEpoch; manager finalizes default recovery
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);            // getFundsFromBorrower reverts -> _handleBorrowerDefault
    // finalizeDefault(recovered, manager) ... -> defaultRecoveryFinalized = true, defaultRecoveryEpoch = N

    // 5) user tries to claim pending receipt B -> permanent underflow revert
    vm.prank(user1);
    vm.expectRevert(); // panic: arithmetic underflow on instantWithdrawsRequests[user] -= A + B
    cdoEpoch.claimInstantWithdrawRequest();
}
```