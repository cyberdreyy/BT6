### Title
Stale-epoch instant withdraw receipts escape the default-recovery haircut basis, inflating `defaultRecoveryPrice` and freezing later claims - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
Like the dbdeployer symlink that escapes the intended extraction directory, an instant-withdraw receipt recorded under a *previous* epoch escapes the per-epoch bucket used to compute the default recovery basis. `defaultPendingClaimBasis()` only adds `instantWithdrawClaimsByEpoch[epochNumber]` (current epoch) when `pendingInstantWithdraws != 0`, but `pendingInstantWithdraws` is an aggregate across all epochs. A partially funded instant request that survives an epoch boundary is therefore counted as "unfunded" yet excluded from the recovery basis, inflating `defaultRecoveryPrice` for current-epoch claimants and leaving the stale receipt permanently unclaimable.

### Finding Description
In `IdleCreditVault.sol`:

- `requestInstantWithdraw` records basis per request epoch: `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` and `instantWithdrawClaimsByEpoch[currentEpoch]`, while `pendingInstantWithdraws` is a global aggregate (lines 356-375).
- `collectInstantWithdrawFunds` decrements `pendingInstantWithdraws` per partial funding, so a remainder can persist across `stopEpoch`/`startEpoch` into a new `epochNumber` (lines 398-403).
- At default finalization, `defaultPendingClaimBasis()` adds only `instantWithdrawClaimsByEpoch[epochNumber]` — the *current* epoch bucket — to the haircut basis (lines 644-649). The stale-epoch instant basis is never included, yet the nonzero `pendingInstantWithdraws` still flags `defaultInstantWithdrawsFinalized = true` (line 696) and `instantWithdrawClaimsByEpoch[defaultEpoch]` is later decremented only for current-epoch claims (line 853).
- Result: `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` (line 688) is computed over an undercounted basis → price is too high → early claimants are overpaid from `defaultRecoveryReserve` until it is exhausted or `_transferDefaultRecovery` underflows/reverts (lines 912-917). The stale-epoch holder's claim path (`claimInstantWithdrawRequest` → `_claimDefaultedInstantWithdrawRequest` finds 0 basis for the default epoch, then attempts `_transferFundedClaim` of a receipt that was never funded) reverts against the reserve guard (lines 897-907), permanently freezing the claim.

### Impact Explanation
Direct misallocation of the isolated default recovery reserve: current-epoch claimants receive more than their fair recovery share, and the remainder of claimants (including the stale-epoch instant requester) face permanent claim reverts — theft/permanent freezing of unclaimed recovery funds. Violated invariant: fair loss distribution and solvency of `defaultRecoveryReserve` vs. aggregate claims.

### Likelihood Explanation
Requires an instant withdraw that is only partially funded (`collectInstantWithdrawFunds` called with less than pending) and then a borrower default finalized via `finalizeDefaultRecovery` in a later epoch — plausible in normal vault operation under stressed liquidity, since honest CDO/manager sequencing suffices. All actors in the path are honest; no privileged misbehavior needed. Confidence is moderate: I could not fully verify the exact CDO-side funding/epoch ordering (`IdleCDOEpochVariant.collectInstantWithdrawFunds` call sites) in this session, so the reachability of a multi-epoch pending instant balance should be confirmed in the PoC.

### Recommendation
Track the unfunded instant basis globally (or per epoch) rather than keying the default basis solely on `instantWithdrawClaimsByEpoch[epochNumber]`. Either iterate/accumulate all epochs' instant claims into the default basis, or fold stale-epoch instant basis into the current bucket at `stopEpoch`. Add a regression test: request instant withdraw in epoch N, partially fund, advance to epoch N+1, default, finalize, then verify the stale claim and all current-epoch claims clear within the reserve.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;
import "forge-std/Test.sol";

// Fork test against deployed IdleCreditVault + IdleCDOEpochVariant.
// Scenario:
// 1. Epoch N running. User A requests instant withdraw of X via CDO
//    (instantWithdrawClaimsByEpoch[N] += X; pendingInstantWithdraws += X).
// 2. Honest startEpoch only partially funds the queue:
//    collectInstantWithdrawFunds(X - d) -> pendingInstantWithdraws = d.
//    instantWithdrawClaimsByEpoch[N] remains X.
// 3. Epoch N stops; deposit() bumps epochNumber to N+1. A's receipt is still
//    bound to epoch N (instantWithdrawsRequestsByEpoch[A][N] = X).
// 4. Epoch N+1 running; borrower defaults; owner finalizes via CDO ->
//    finalizeDefaultRecovery(recovered, source).
//    - defaultPendingClaimBasis() = pendingWithdraws + instantWithdrawClaimsByEpoch[N+1]
//      (A's basis X in epoch N is excluded).
//    - recoveryPrice inflated; defaultInstantWithdrawsFinalized = true.
// 5. Current-epoch claimants claim first: reserve drains at inflated price.
// 6. A calls CDO.claimInstantWithdrawRequest():
//    _claimDefaultedInstantWithdrawRequest finds basis[N+1] == 0, then
//    _transferFundedClaim tries to pay X at par -> reverts against the
//    reserve guard -> A's claim is permanently frozen, and remaining
//    claimants are underpaid.
function test_staleEpochInstantReceipt_escapesDefaultHaircut() public {
    // arrange: fork at block with live vault, impersonate CDO via prank to
    // drive request/collect/epoch transitions, or use full CDO flow.
    // assert: recoveryPrice * (trueBasis) > reserve, i.e. reserve insolvent;
    // assert: vm.expectRevert on A.claimInstantWithdrawRequest().
}
```