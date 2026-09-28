### Title
Stale-epoch instant-withdraw claims are excluded from the default recovery basis, inflating `defaultRecoveryPrice` and letting early claimants drain the recovery reserve - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`finalizeDefaultRecovery` computes a single recovery multiplier over `totalBasis = activeBasis + pendingBasis`. The pending side comes from `defaultPendingClaimBasis`, which adds instant-withdraw basis only via `instantWithdrawClaimsByEpoch[epochNumber]` — i.e. only the *current* epoch's instant receipts — gated on `pendingInstantWithdraws != 0`. Unfunded instant receipts from earlier epochs are still tracked in `pendingInstantWithdraws` and in `instantWithdrawsRequestsByEpoch[user][oldEpoch]`, but are silently dropped from the claim basis. The result mirrors the advisory's bug class: the "verified" recovery price does not correspond to the data (the claims) it is supposed to cover, so the reserve is over-distributed to whoever claims first.

### Finding Description
- `defaultPendingClaimBasis` only includes `instantWithdrawClaimsByEpoch[epochNumber]`, the *current* epoch bucket, when `pendingInstantWithdraws != 0` [1](#0-0) .
- `pendingInstantWithdraws` is only decremented in `collectInstantWithdrawFunds` when the CDO actually pulls funds [2](#0-1) . If `getInstantWithdrawFunds`/startEpoch funding only partially covers the instant queue, the unfunded remainder persists into subsequent epochs while the per-epoch claim basis stays keyed to the *request* epoch (`instantWithdrawsRequestsByEpoch[user][currentEpoch]` / `instantWithdrawClaimsByEpoch[currentEpoch]` at lines 371–372).
- When a borrower default is later finalized in epoch `N+k`, `finalizeDefaultRecovery` computes `recoveryPrice = reserveAmount / totalBasis` where `totalBasis` omits the stale-epoch instant claims [3](#0-2) .
- After finalization, `_claimDefaultedInstantWithdrawRequest` only haircuts receipts keyed to `defaultRecoveryEpoch` [4](#0-3) ; the stale-epoch instant receipt falls through to the par payout in `claimInstantWithdrawRequest` (`instantWithdrawsRequests[_user]` via `_transferFundedClaim`, lines 387–392), even though it was never funded and never included in the recovery basis.

Broken invariant: every claim against the pool must be inside the recovery denominator. Here the denominator excludes real claims, so `defaultRecoveryPrice` is too high and `defaultRecoveryReserve` is consumed at an inflated rate.

### Impact Explanation
An attacker holding a defaulted-epoch normal withdraw receipt calls `claimWithdrawRequest` immediately after `finalizeDefaultRecovery`. Because `recoveryPrice` is inflated, they receive more than their fair pro-rata share of `defaultRecoveryReserve`; later claimants (other defaulted receipts, post-default requests, the par-payout of the stale instant receipt itself) find the reserve/balance insufficient and their claims revert — permanent loss of unclaimed recovery proportional to the omitted instant basis. The stale-receipt holder also escapes the haircut entirely (par claim), a second over-distribution.

### Likelihood Explanation
Requires: (a) instant withdrawals enabled and a partially funded instant queue at some startEpoch (borrower underfunds — sequencing around an honest borrower's insufficient balance, not malicious action); (b) a subsequent hard borrower default finalized via `finalizeDefaultRecovery` with `pendingInstantWithdraws != 0` still containing the old remainder; (c) the attacker holding a normal defaulted-epoch receipt and claiming early. These are reachable states in the standard epoch variant; I did not fully trace `getInstantWithdrawFunds`/startEpoch funding paths in `IdleCDOEpochVariant` to confirm a partial-funding path leaves `pendingInstantWithdraws` non-zero across an epoch boundary — if the code always reverts on partial instant funding, the bug is unreachable and this finding collapses.

### Recommendation
In `defaultPendingClaimBasis`, include all outstanding instant claim basis, not just `instantWithdrawClaimsByEpoch[epochNumber]` — e.g. maintain a global `instantClaimsTotal` incremented in `requestInstantWithdraw` and decremented in `collectInstantWithdrawFunds`/claims, or iterate/aggregate prior-epoch buckets. Correspondingly, `_claimDefaultedInstantWithdrawRequest` should haircut *all* unfunded instant receipt epochs at `defaultRecoveryPrice`, not only `defaultRecoveryEpoch`, so no unfunded receipt can claim at par.

### Proof of Concept
Foundry fork sketch against `IdleCreditVault` + `IdleCDOEpochVariant` (see `test/foundry/IdleCreditVault.t.sol` helpers):

1. Deposit with users A (AA) and attacker B (AA); `startEpoch`; enable instant withdraws (`setInstantWithdrawParams`).
2. In epoch N, attacker-adjacent user C calls `requestInstantWithdraw`; at `startEpoch` for N+1, borrower funds only part of the instant queue so `pendingInstantWithdraws > 0` persists and `instantWithdrawClaimsByEpoch[N]` keeps C's basis.
3. During epoch N+1, attacker B calls `requestWithdraw` (normal receipt recorded in `withdrawsRequestsByEpoch[B][N+1]`).
4. Warp past `epochEndDate`; call `stopEpoch` with borrower approval = 0 → `_handleBorrowerDefault`; then `finalizeDefault(_recoveredAmount < total real basis)` → `finalizeDefaultRecovery`.
5. Assert: `defaultRecoveryPrice * (pendingWithdraws + instantWithdrawClaimsByEpoch[N]) > defaultRecoveryReserve` — the stale instant basis is not in the denominator.
6. B calls `claimWithdrawRequest()` and receives `claimBasis * inflatedPrice`, strictly more than the fair pro-rata share; a second defaulted-epoch claimer D's identical claim then reverts on `defaultRecoveryReserve -= _amount` underflow / insufficient balance, or C's par instant claim drains funds beyond its intended haircut — demonstrating reserve theft/permanent freezing of later claims.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-403)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L679-699)
```text
    uint256 pendingBasis = defaultPendingClaimBasis();
    uint256 totalBasis = activeBasis + pendingBasis;
    if (totalBasis == 0) revert NotAllowed();

    // Some recovery funds may already be in this strategy: partially prefunded instant requests
    // and borrower-send funds that failed at epoch start. Count both without pulling them again.
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
    // Bring active CDO NAV to the same recovery ratio. IdleCDOEpochVariant then calls
    // _forceUpdateAccounting so tranche prices/virtualPrice expose the crystallized loss.
    uint256 activeFinalNAV = (activeBasis * recoveryPrice) / RECOVERY_FULL;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L842-856)
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
  }
```
