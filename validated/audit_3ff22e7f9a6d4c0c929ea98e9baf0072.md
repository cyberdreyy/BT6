### Title
Instant-withdraw claims keyed to the current `epochNumber` let older unfunded instant requests escape the default-recovery haircut - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The external bug is a privileged action that operates on a single global "latest" counter (`roundsCount`) instead of a per-item id, so earlier pending entries are never addressed and funds get locked or the wrong entry is acted on. The closest analog lives in `IdleCreditVault`: instant-withdraw receipt accounting is bucketed only under the *current* `epochNumber`, while `pendingInstantWithdraws` is an aggregate that can span multiple epochs. When default recovery is finalized, only the current-epoch instant bucket joins the recovery basis — older unfunded instant receipts silently bypass the haircut and remain claimable at par.

### Finding Description
`requestInstantWithdraw` records the request under `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and `instantWithdrawClaimsByEpoch[epochNumber]` (the *current* epoch counter), while `pendingInstantWithdraws` accumulates across epochs. [1](#0-0) 

A new epoch can start while instant requests are still partially unfunded — the code comments explicitly acknowledge this ("startEpoch moved the CDO's available cash to the strategy but that cash covered only part of the instant queue"), so `pendingInstantWithdraws != 0` can persist while `epochNumber` is bumped inside `deposit`. [2](#0-1) 

When `finalizeDefaultRecovery` runs, `defaultPendingClaimBasis` adds only `instantWithdrawClaimsByEpoch[epochNumber]` — the *latest* epoch bucket — to the recovery basis, exactly the same "only the latest counter matters" defect as `roundsCount` in YoloV2. [3](#0-2) 

Symmetrically, `_defaultPrefundedInstantReserve` compares `instantWithdrawClaimsByEpoch[epochNumber]` against `pendingInstantWithdraws`, so an older-epoch unfunded remainder inflates `pendingInstant` and zeroes the prefunded reserve for the current epoch too. [4](#0-3) 

After finalization, `_claimDefaultedInstantWithdrawRequest` clears only `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` (the latest epoch). The older-epoch portion stays inside `instantWithdrawsRequests[_user]` and falls through to the par-funded claim path in `claimInstantWithdrawRequest`, which burns the aggregate and pays out 1:1 via `_transferFundedClaim`. [5](#0-4) [6](#0-5) 

### Impact Explanation
An unprivileged lender whose instant request from epoch N was not fully funded (partial funding is an acknowledged state) escapes the `defaultRecoveryPrice` haircut entirely when default is finalized in epoch N+1. Once any post-default instant funding is collected via `collectInstantWithdrawFunds`, the attacker claims the stale portion at 100% while every same-epoch claimant receives only the recovery price — a direct (1 − recoveryPrice) × staleAmount transfer of value away from the funded instant pool and/or it permanently inflates `pendingInstantWithdraws`/`pendingWithdraws` accounting, which feeds `expectedEpochInterest`/solvency math. If no funding ever arrives, the stale receipt plus the distorted `pendingInstantWithdraws` counter leave that portion of claims permanently frozen — the same "funds locked forever" impact as the YoloV2 report.

### Likelihood Explanation
Requires: (1) a partially funded instant queue crossing an epoch boundary — a state the code itself documents as reachable, (2) a later default finalized by honest manager/owner. No privileged malice needed; the attacker only needs two ordinary `requestInstantWithdraw` calls in different epochs. The accounting gap is deterministic once both conditions hold.

### Recommendation
Track the unfunded instant basis per epoch (or iterate `instantWithdrawClaimsByEpoch` over all epochs contributing to `pendingInstantWithdraws`) in `defaultPendingClaimBasis`, `_defaultPrefundedInstantReserve`, and `_claimDefaultedInstantWithdrawRequest`, so every unfunded instant receipt — not only the latest-epoch bucket — joins the recovery basis at `defaultRecoveryPrice`. Alternatively, snap each user's total `instantWithdrawsRequests` into the current-epoch bucket when finalization begins.

### Proof of Concept
```solidity
// Foundry fork PoC sketch against IdleCreditVault + IdleCDOEpochVariant
// 1. Epoch N running: attacker calls cdo.requestInstantWithdraw(A, attacker)
//    -> instantWithdrawsRequestsByEpoch[attacker][N] = A, pendingInstantWithdraws = A
// 2. Manager calls stopEpoch/startEpoch; collectInstantWithdrawFunds funds only A/2.
//    pendingInstantWithdraws = A/2 remains; epochNumber is now N+1.
// 3. Epoch N+1: attacker calls requestInstantWithdraw(B, attacker).
//    instantWithdrawClaimsByEpoch[N+1] = B only.
// 4. Borrower fails to repay; manager triggers default; CDO calls
//    finalizeDefaultRecovery(recovered, source) with recoveryPrice = 0.5e18.
//    defaultPendingClaimBasis() includes pendingWithdraws + instantWithdrawClaimsByEpoch[N+1]
//    => A/2 (attacker's epoch-N remainder) is missing from the basis.
// 5. Attacker calls claimInstantWithdrawRequest: defaulted path clears only B at 0.5;
//    then the funded path burns instantWithdrawsRequests[attacker] = A/2 and pays it AT PAR.
//    assert(attackerPayout > honestHaircutPayout) // haircut evasion
//    // pendingInstantWithdraws remains inconsistent -> residual claims frozen
```

Caveat: step 5's par payout needs post-default funding to reach the strategy for instant claims; if the vault never funds it, the impact manifests as permanent freezing plus permanently overstated `pendingInstantWithdraws` rather than an immediate payout. Either branch traces to the same defect — instant-claim accounting bound to a single latest `epochNumber` bucket.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L367-374)
```text
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L387-392)
```text
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-614)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
      // deposit done between epochs so we increase the counter
      totEpochDeposits += _amount;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-649)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L716-723)
```text
  function _defaultPrefundedInstantReserve() internal view returns (uint256 prefundedReserve) {
    uint256 pendingInstant = pendingInstantWithdraws;
    if (pendingInstant == 0) return prefundedReserve;
    uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
    if (instantBasis > pendingInstant) {
      prefundedReserve = instantBasis - pendingInstant;
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L842-855)
```text
  function _claimDefaultedInstantWithdrawRequest(address _user) internal returns (uint256 claimBasis) {
    uint256 defaultEpoch = defaultRecoveryEpoch;
    claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch];
    if (claimBasis == 0) return claimBasis;

    instantWithdrawsRequestsByEpoch[_user][defaultEpoch] = 0;
    instantWithdrawsRequests[_user] -= claimBasis;
    uint256 pending = pendingInstantWithdraws;
    // `pendingInstantWithdraws` is only the unfunded remainder. If this user's claim is larger,
    // the extra amount was already counted as prefunded reserve during default finalization.
    pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis;
    instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis;
    _burn(_user, claimBasis);
    _transferDefaultRecovery(_user, (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL);
```
