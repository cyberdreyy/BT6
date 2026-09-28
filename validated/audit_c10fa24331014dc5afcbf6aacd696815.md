### Title
Stale `instantWithdrawsRequestsByEpoch` / `instantWithdrawClaimsByEpoch` after a funded instant claim corrupts default-recovery accounting and freezes recovery funds — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Analogous to CVE-2017-12457's NULL/dangling-record dereference, `claimInstantWithdrawRequest` zeroes only the aggregate `instantWithdrawsRequests[_user]` but never clears the per-epoch records `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]`. These stale records are later dereferenced by `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` during `finalizeDefaultRecovery`, double-counting already-paid claims. The resulting `defaultRecoveryPrice` is computed against a phantom reserve that no longer exists in the contract, so honest claimants' `_transferDefaultRecovery` calls eventually underflow (`defaultRecoveryReserve -= _amount`) or fail on the token transfer, permanently freezing part of the recovery reserve.

### Finding Description
In `claimInstantWithdrawRequest` (lines 380–393) the funded-claim path does: [1](#0-0) 

Only `instantWithdrawsRequests[_user]` is reset. Compare with the default path `_claimDefaultedInstantWithdrawRequest`, which correctly clears all three buckets: [2](#0-1) 

The stale `instantWithdrawClaimsByEpoch[epochNumber]` is then consumed in two places at finalization:

1. `defaultPendingClaimBasis` adds it to the defaulted-claim basis whenever `pendingInstantWithdraws != 0`: [3](#0-2) 

2. `_defaultPrefundedInstantReserve` treats `instantBasis - pendingInstant` as underlying already held by the strategy: [4](#0-3) 

In `finalizeDefaultRecovery`, this phantom prefunded reserve is added to `reserveAmount`, and the inflated `totalBasis` is used both as denominator for `recoveryPrice` and (via `activeBalance`) to set active LP NAV: [5](#0-4) 

Because the "prefunded" component was already paid out to the earlier claimant, `defaultRecoveryReserve` (line 691) records tokens the contract does not hold. Claims then drain the real balance; the final claimants hit `defaultRecoveryReserve -= _amount` underflow or an insufficient-balance `safeTransfer` revert at lines 912–916, so their recovery is permanently unclaimable.

### Impact Explanation
After any borrower default finalized via `finalizeDefault`/`finalizeDefaultRecovery` in an epoch where (a) an instant-withdraw receipt was funded and already claimed, and (b) at least one instant receipt remains unfunded (`pendingInstantWithdraws != 0`), the recovery basis is overstated by the stale amount X. Concretely: `recoveryPrice ≈ (recovered + X + reserve) / (activeBasis + pendingBasis + X)` is minted/burned against `activeBalance` that never included X, and `defaultRecoveryReserve` includes X phantom tokens. The last ~X worth of claims — defaulted withdraw receipts, post-default requests, or instant receipts — revert in `_transferDefaultRecovery`, permanently freezing that recovery value. Attacker (any KYC'd lender) can deliberately create the stale record (see PoC) to maximize X; loss is bounded by the stale instant basis but can be the full recovery reserve if the attacker times a large instant request, lets it be funded via `collectInstantWithdrawFunds`, claims it, then triggers default in the same epoch.

### Likelihood Explanation
Requires: instant-withdraw mode enabled (`instantWithdrawDelay`), an epoch where the CDO prefunds an instant receipt which is then claimed, another unfunded instant receipt outstanding in the same epoch, and a borrower default finalized in that epoch. All attacker steps use only unprivileged flows: `requestInstantWithdraw`, `claimInstantWithdrawRequest`, plus sequencing around the honest manager/owner `stopEpoch`/`finalizeDefault` calls. No privileged misbehavior needed; `defaultRecoveryFinalized`/`defaultInstantWithdrawsFinalized` gating does not help because the corruption is in the basis inputs, not the finalize flag. The `_transferFundedClaim` reserve guard (lines 899–905) protects funded claims from spending the reserve, but does nothing about reserve over-accounting.

### Recommendation
Mirror the default-path cleanup in `claimInstantWithdrawRequest`: after computing `amount`, decrement `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` for the request epoch(s) being paid (storing the request epoch per user, similar to `lastWithdrawRequest`, may be needed since requests can span epochs). Alternatively, net off claimed basis inside `collectInstantWithdrawFunds`. Add a regression test: fund + claim an instant receipt, leave a second unfunded instant receipt, then `finalizeDefaultRecovery` and assert `defaultPendingClaimBasis`/`_defaultPrefundedInstantReserve` exclude the claimed amount.

### Proof of Concept
Foundry fork outline (setup mirrors `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testStaleInstantBasisCorruptsRecovery() external {
  // 1. Enable instant withdraws; epoch 0 running with attacker + victim deposits.
  //    manager: cdoEpoch.setInstantWithdrawParams(delay, feeBps, true)
  address attacker = makeAddr("attacker");
  address victim   = makeAddr("victim");
  _depositWithUser(attacker, 100_000 * ONE_SCALE, true);
  _depositWithUser(victim,   100_000 * ONE_SCALE, true);

  // 2. Attacker requests instant withdraw; manager/epoch collects funds
  //    (collectInstantWithdrawFunds -> pendingInstantWithdraws -= X) and
  //    attacker claims via cdoEpoch.claimInstantWithdrawRequest().
  //    BUG: instantWithdrawsRequestsByEpoch[attacker][N] and
  //    instantWithdrawClaimsByEpoch[N] still contain X.

  // 3. Victim requests an instant withdraw that stays unfunded
  //    (pendingInstantWithdraws == Y > 0), same epochNumber N.

  // 4. Borrower defaults; owner calls cdoEpoch.finalizeDefault(recovered, src).

  // 5. Assertions:
  //    - defaultPendingClaimBasis() == pendingWithdraws + Y + X  (X stale)
  //    - defaultRecoveryReserve == recovered + prefunded(X) + reserve
  //    - victim.claimInstantWithdrawRequest / claimWithdrawRequest pays out
  //      price*claim, but the residual claim of size ~X reverts with
  //      arithmetic underflow in _transferDefaultRecovery, while
  //      defaultRecoveryReserve still reports > 0 (permanently stranded).
}
```

Key reproducibility point: no storage manipulation is needed; steps 2–3 are ordinary user calls, and step 4 is the honest owner finalization path.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L387-392)
```text
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L716-722)
```text
  function _defaultPrefundedInstantReserve() internal view returns (uint256 prefundedReserve) {
    uint256 pendingInstant = pendingInstantWithdraws;
    if (pendingInstant == 0) return prefundedReserve;
    uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
    if (instantBasis > pendingInstant) {
      prefundedReserve = instantBasis - pendingInstant;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L843-853)
```text
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
```
