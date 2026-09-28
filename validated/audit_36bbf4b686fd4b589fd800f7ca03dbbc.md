### Title
Stale `instantWithdrawsRequestsByEpoch` basis inflates default recovery denominator and DoSes later claims - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The kernel bug is an offload mask that covers more bytes than the partial field it should match. The analog in `IdleCreditVault` is the same class of "boundary covers more than the rule matches" accounting: `instantWithdrawClaimsByEpoch`/`instantWithdrawsRequestsByEpoch` keep counting instant-withdraw receipts even after those receipts were fully funded and already claimed, so `defaultPendingClaimBasis()` builds a recovery basis that covers already-paid claims.

### Finding Description
`requestInstantWithdraw` records receipt basis twice: per user/epoch in `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` and per epoch in `instantWithdrawClaimsByEpoch[currentEpoch]` [1](#0-0) .

However, the funded-claim path `claimInstantWithdrawRequest` only zeroes the aggregate `instantWithdrawsRequests[_user]` and burns that amount. It never clears `instantWithdrawsRequestsByEpoch[_user][epoch]` nor decrements `instantWithdrawClaimsByEpoch[epoch]` [2](#0-1) .

At default finalization, `defaultPendingClaimBasis()` adds the entire `instantWithdrawClaimsByEpoch[epochNumber]` to the recovery basis whenever `pendingInstantWithdraws != 0` [3](#0-2) . That epoch-level bucket still contains basis of receipts that were already funded by `collectInstantWithdrawFunds` and already paid out by `claimInstantWithdrawRequest`. The recovery price computed in `finalizeDefaultRecovery` (`reserveAmount * RECOVERY_FULL / totalBasis`) is therefore diluted by claims that no longer exist [4](#0-3) . `_defaultPrefundedInstantReserve` only compensates for basis that is funded-but-unclaimed (`instantBasis - pendingInstant`); it cannot subtract basis that was claimed, because claimed basis is indistinguishable from funded-but-unclaimed basis in the current storage [5](#0-4) .

Additionally, a user who already claimed a funded instant receipt in the default epoch still has `instantWithdrawsRequestsByEpoch[_user][defaultEpoch] != 0`. If they later hold any new receipt, `_claimDefaultedInstantWithdrawRequest` computes a `claimBasis` that includes the already-paid amount: `instantWithdrawsRequests[_user] -= claimBasis` underflows (or burns receipt tokens the user does not hold), permanently reverting that user's claim [6](#0-5) .

### Impact Explanation
Two concrete harms from an unprivileged user flow (no privileged role misbehavior needed):

1. **Recovery dilution / stranded reserve.** `totalBasis` is inflated by already-paid instant claims, so `defaultRecoveryPrice` is set lower than the true ratio. Every legitimate defaulted-epoch claimant (normal withdraws, APR0, unfunded instant receipts, active AA/BB holders) is underpaid at claim time, and the excess `defaultRecoveryReserve` corresponding to the phantom basis is never claimable — it remains locked in the strategy permanently.

2. **Permanent freezing of claims.** Any user who claimed a funded instant receipt in the defaulted epoch and still holds another receipt hits an arithmetic underflow in `_claimDefaultedInstantWithdrawRequest`, blocking all subsequent `claimInstantWithdrawRequest` calls for that user.

Broken invariant: one receipt, one payout — the recovery "mask" covers already-settled receipts.

### Likelihood Explanation
Requires an epoch where instant withdrawals were partially funded at `startEpoch` (so `pendingInstantWithdraws != 0`), at least one instant receipt was claimed after funding, and the borrower then defaults mid-epoch. Instant-withdraw mode (`lastEpochApr > unscaledApr + instantWithdrawAprDelta`, non-programmable borrower) and partial funding are normal operating states, and a borrower default during an epoch is an in-scope, anticipated event — the whole default-recovery path exists for it.

### Recommendation
Keep per-epoch instant-claim accounting in sync with actual claims:
- In `claimInstantWithdrawRequest`, decrement `instantWithdrawClaimsByEpoch` and zero `instantWithdrawsRequestsByEpoch` for the epochs being paid (or track a per-epoch "claimed" counter), mirroring what `_claimDefaultedInstantWithdrawRequest` already does.
- Alternatively, change `defaultPendingClaimBasis()`/`_defaultPrefundedInstantReserve` to use a per-epoch outstanding-claims counter that is decremented on every funded claim, so `basis` only covers receipts that are actually unsettled.

### Proof of Concept
Foundry fork PoC (instant-withdraw enabled CDO, non-programmable borrower):

```solidity
function testStaleInstantBasisDilutesDefaultRecovery() public {
    // 1. Lender deposits AA; epoch0 runs; borrower repays so APR drops.
    // 2. Attacker (KYC'd lender) calls requestWithdraw -> instant path:
    //    instantWithdrawsRequestsByEpoch[attacker][E] += amt;
    //    instantWithdrawClaimsByEpoch[E] += amt; pendingInstantWithdraws += amt.
    // 3. Victim also requests instant withdraw (amt2).
    // 4. startEpoch: collectInstantWithdrawFunds funds only `amt` (partial):
    //    pendingInstantWithdraws = amt2 (still != 0).
    // 5. Attacker calls claimInstantWithdrawRequest -> paid `amt`,
    //    instantWithdrawsRequests[attacker] = 0,
    //    BUT instantWithdrawsRequestsByEpoch[attacker][E] and
    //    instantWithdrawClaimsByEpoch[E] still equal amt + amt2.
    // 6. Borrower defaults; manager calls _handleBorrowerDefault ->
    //    finalizeDefaultRecovery(recovered, source).
    //    defaultPendingClaimBasis() = pendingWithdraws + (amt + amt2)
    //    instead of pendingWithdraws + amt2.
    // assert: defaultRecoveryPrice < recovered * 1e18 / trueBasis;
    // assert: victim's _claimDefaultedInstantWithdrawRequest pays less than owed;
    // assert: leftover defaultRecoveryReserve is permanently unclaimable;
    // assert: attacker, holding a second (unfunded) instant receipt from step 3b,
    //         reverts in claimInstantWithdrawRequest due to underflow at
    //         instantWithdrawsRequests[attacker] -= claimBasis.
}
```

Note: I verified the stale-basis lifecycle in the indexed source (request, funded claim, finalization, defaulted claim) but could not step through the full `stopEpoch`/`startEpoch` funding sequence within the available search; the PoC funding-order details (partial `collectInstantWithdrawFunds` before claim) should be confirmed against `IdleCDOEpochVariant.startEpoch` during reproduction.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L366-374)
```text
    instantWithdrawsRequests[_user] += _amount;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-648)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L679-692)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L843-855)
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
    _burn(_user, claimBasis);
    _transferDefaultRecovery(_user, (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL);
```
