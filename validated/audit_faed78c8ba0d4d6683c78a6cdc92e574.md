### Title
Default-recovery claims can drain underlyings earmarked for older funded instant-withdraw receipts, permanently freezing those claims - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary

The external bug (a `}` seen before `{` makes a check fire on the wrong boundary, corrupting progressively more memory each iteration) maps to a missing per-epoch boundary in `IdleCreditVault`'s claim ordering: `finalizeDefaultRecovery` builds `defaultRecoveryReserve` from the *current-epoch* instant-withdraw basis only, while earlier-epoch funded instant receipts stay commingled in the same token balance and are paid from `_transferFundedClaim`, which protects the reserve but not the older receipts.

### Finding Description

The strategy contract holds one underlying balance that backs three different claim classes:

1. Funded normal withdraw receipts (`withdrawsRequests`) whose underlying was collected via `collectWithdrawFunds`.
2. Funded instant-withdraw receipts (`instantWithdrawsRequests`) whose underlying was collected via `collectInstantWithdrawFunds` in earlier epochs.
3. After a borrower default, `defaultRecoveryReserve`, consumed exclusively by `_transferDefaultRecovery`.

`defaultPendingClaimBasis` only counts pending normal withdraws plus `instantWithdrawClaimsByEpoch[epochNumber]` — the *current* epoch's instant claims — and only when `pendingInstantWithdraws != 0` (contracts/strategies/idle/IdleCreditVault.sol:644-649). Correspondingly, `_defaultPrefundedInstantReserve` reserves only `instantWithdrawClaimsByEpoch[epochNumber] - pendingInstantWithdraws`, i.e. the already-funded portion of *current-epoch* instant receipts (contracts/strategies/idle/IdleCreditVault.sol:716-723). Older-epoch instant receipts that were fully funded in a prior epoch are excluded from both the recovery basis and the prefunded reserve, yet their backing underlyings remain in `address(this)` balance.

`_transferFundedClaim` guards only one direction: `balance - reserve >= _amount` ensures funded claims never spend the recovery reserve (contracts/strategies/idle/IdleCreditVault.sol:897-907). There is no symmetric guard: `_transferDefaultRecovery` simply decrements `defaultRecoveryReserve` and transfers (contracts/strategies/idle/IdleCreditVault.sol:912-917). So defaulted/post-default claimants can spend underlyings that were collected to back older-epoch instant receipts. In `claimInstantWithdrawRequest`, the defaulted-epoch portion is cleared first, then the full remaining `instantWithdrawsRequests[_user]` is paid at par via `_transferFundedClaim` (contracts/strategies/idle/IdleCreditVault.sol:380-393) — which succeeds only while enough non-reserve balance remains.

Concretely: `defaultRecoveryPrice` is computed as `reserveAmount * RECOVERY_FULL / totalBasis` where `reserveAmount` includes the prefunded instant reserve but excludes older funded instant backing (lines 685-708). Each defaulted-epoch claim then pulls `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` from the shared balance. If defaulted claims are executed before an older funded instant receipt is claimed, the shared balance can be drawn below what that instant receipt is owed. Its `claimInstantWithdrawRequest` then reverts in `_transferFundedClaim` (`balance < reserve || balance - reserve < _amount` — the guard, designed for reserve protection, blocks the legitimate funded claim) or the `safeTransfer` fails.

### Impact Explanation

A tranche-token holder with a funded instant-withdraw receipt from epoch `N-1` can be permanently unable to claim because an unprivileged defaulted-epoch claimant (epoch `N`) claimed first and the reserve accounting absorbed the underlying that backed the older receipt. This is permanent freezing / effective theft of a funded claim — the tokens were already collected from the borrower in the prior epoch. Loss equals the victim's `instantWithdrawsRequests` amount, bounded by the overlap between `defaultRecoveryReserve` payouts and the unreserved funded balance.

### Likelihood Explanation

Requires a specific but plausible sequence: an instant-withdraw request funded via `collectInstantWithdrawFunds` in epoch `N-1` is left unclaimed (common while APR is falling — the CDO's `requestWithdraw` auto-routes to instant mode when `lastEpochApr > currentApr + instantWithdrawAprDelta`, IdleCDOEpochVariant.sol:761-768), then the borrower defaults in epoch `N` with `pendingInstantWithdraws != 0` at finalization so the recovery machinery activates, and defaulted claimants claim before the victim. All actors are unprivileged lenders; no privileged misbehavior needed.

### Recommendation

Segregate funded instant-receipt backing per epoch the same way `withdrawsRequestsByEpoch` does for normal receipts: when `collectInstantWithdrawFunds` collects for epoch `E`, record the collected amount against `instantWithdrawClaimsByEpoch[E]` claimants, and include *all* outstanding funded instant claims (not just `instantWithdrawClaimsByEpoch[epochNumber]`) in `defaultPendingClaimBasis`/`_defaultPrefundedInstantReserve`, or track a separate `fundedInstantReserve` that `_transferDefaultRecovery` may never consume. Alternatively, extend the `_transferFundedClaim` guard symmetrically by tracking total outstanding funded claims and capping `_transferDefaultRecovery` at `balance - outstandingFundedClaims`.

### Proof of Concept

Foundry fork sketch (against an epoch-variant CDO + IdleCreditVault deployment):

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;
import "forge-std/Test.sol";

contract InstantReceiptFrozenByRecoveryTest is Test {
  // contracts/IdleCDOEpochVariant.sol + contracts/strategies/idle/IdleCreditVault.sol

  function test_oldestInstantReceiptFrozen() public {
    // --- Epoch N-1 ---
    // 1. KYC'd lender Alice deposits AA, triggers APR drop so her
    //    requestWithdraw routes to requestInstantWithdraw(_underlyings, alice)
    //    (IdleCDOEpochVariant.sol:761-768). instantWithdrawsRequests[alice] = A.
    // 2. stopEpoch: CDO calls collectInstantWithdrawFunds(A); underlying for A
    //    now sits in the strategy. instantWithdrawsRequestsByEpoch[alice][N-1] = A.
    //    Alice does not claim yet.
    // --- Epoch N ---
    // 3. startEpoch runs; epochNumber becomes N. New instant/normal requests exist
    //    in epoch N; pendingInstantWithdraws > 0 (partially funded).
    // 4. Borrower defaults; CDO calls finalizeDefaultRecovery.
    //    - basis includes pendingWithdraws + instantWithdrawClaimsByEpoch[N]
    //      (defaultPendingClaimBasis, IdleCreditVault.sol:644-649)
    //    - reserve gets only epoch-N prefunded portion
    //      (_defaultPrefundedInstantReserve, :716-723)
    //    - Alice's epoch-(N-1) funded backing A stays in the contract balance
    //      but is NOT inside defaultRecoveryReserve.
    // 5. Attacker Bob (any defaulted-epoch claimant) calls claimWithdrawRequest /
    //    claimInstantWithdrawRequest repeatedly via the CDO. Each
    //    _transferDefaultRecovery spends from the shared balance (:912-917)
    //    until balance - reserve < A.
    // 6. Alice calls the CDO claim path -> claimInstantWithdrawRequest(alice)
    //    -> _transferFundedClaim(alice, A) reverts:
    //       balance - reserve < _amount  (IdleCreditVault.sol:904)
    //    Alice's funded receipt is permanently frozen while Bob extracted
    //    recovery funded (in part) by Alice's collected underlying.
    assertTrue(true); // assertions: alice claim reverts; bob received > entitled share
  }
}
```

Uncertainty: I verified the accounting asymmetry in `IdleCreditVault.sol` (`defaultPendingClaimBasis`, `_defaultPrefundedInstantReserve`, `_transferFundedClaim`, `_transferDefaultRecovery`, `claimInstantWithdrawRequest`), but I could not confirm the exact CDO-side `stopEpoch`/`_handleBorrowerDefault` call ordering within the iteration budget. If the CDO sweeps all pre-default instant receipts' backing into a segregated bucket before finalization, the impact reduces to reserve-underfunding rather than cross-epoch draining — worth confirming against `finalizeDefault` in `IdleCDOEpochVariant.sol` before reporting.