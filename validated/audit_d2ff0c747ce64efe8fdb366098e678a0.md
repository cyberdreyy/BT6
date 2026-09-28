### Title
Claimed instant-withdraw receipts keep their per-epoch claim basis, inflating `defaultPendingClaimBasis` and diluting/locking default recovery funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The ceph bug class is a reference leak: folios removed from a batch were not `folio_put`, so already-released items kept an owning reference. The analog lives in `IdleCreditVault.claimInstantWithdrawRequest`. When a user is paid an instant withdrawal, the function clears `instantWithdrawsRequests[_user]` but never removes the per-epoch entries `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` that were incremented in `requestInstantWithdraw`. Those stale entries are exactly the "folio refs" that survive removal. At default finalization, `defaultPendingClaimBasis()` adds `instantWithdrawClaimsByEpoch[epochNumber]` to the recovery basis whenever `pendingInstantWithdraws != 0`, counting already-paid receipts as still-owed claims.

### Finding Description
- `requestInstantWithdraw` records both a per-user aggregate and per-epoch ledgers: `instantWithdrawsRequests[_user] += _amount`, `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount`, `instantWithdrawClaimsByEpoch[currentEpoch] += _amount` [1](#0-0) .
- `claimInstantWithdrawRequest` pays the user and zeroes only the aggregate `instantWithdrawsRequests[_user]`; the two per-epoch counters keep the paid amount [2](#0-1) .
- `defaultPendingClaimBasis` treats `instantWithdrawClaimsByEpoch[epochNumber]` as outstanding defaulted-epoch claim basis whenever `pendingInstantWithdraws != 0` [3](#0-2) .
- `finalizeDefaultRecovery` divides the assembled `reserveAmount` by `totalBasis = activeBasis + pendingBasis` to set `defaultRecoveryPrice` [4](#0-3) . Every stale, already-paid instant receipt inflates `totalBasis`, so the recovery price is depressed pro-rata.
- The stale per-epoch entries cannot be drained honestly afterward: `_claimDefaultedInstantWithdrawRequest` computes `claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch]` and does `instantWithdrawsRequests[_user] -= claimBasis` [5](#0-4) , which underflows for a user whose aggregate was zeroed by the earlier paid claim, so the excess basis is never claimed and the corresponding reserve share is stranded in the strategy (there is no reserve sweep).

### Impact Explanation
When a default is finalized in an epoch where some instant withdrawals were already funded and claimed but the instant queue was not fully funded (`pendingInstantWithdraws != 0` — the partial-prefunding state explicitly contemplated by `_defaultPrefundedInstantReserve` [6](#0-5) ), every honest defaulted-epoch claimant — normal withdraw receipt holders, unfunded instant receipt holders, and active AA/BB holders via `defaultBBNav` — receives a strictly smaller recovery than funded, because the denominator includes phantom claims. The phantom share of `defaultRecoveryReserve` is permanently locked in the strategy: the stale entries can never be claimed (underflow on `instantWithdrawsRequests[_user] -= claimBasis`), so it becomes unreachable recovery dust. The misallocation equals `claimedInstantBasis * reserveAmount / totalBasis`, directly quantifiable.

### Likelihood Explanation
Requires instant withdrawals enabled, at least one funded-and-claimed instant receipt in the defaulted epoch, and an unfunded remainder (`pendingInstantWithdraws != 0`) — i.e., the partially-prefunded instant queue the code already models — plus a borrower default that epoch. The attacker needs no privilege: any user who requests and claims an instant withdrawal in the defaulted epoch leaves the stale basis behind; the loss falls on other claimants and the locked reserve. No existing guard stops it: `collectInstantWithdrawFunds` only decrements `pendingInstantWithdraws` [7](#0-6) , and nothing reconciles `instantWithdrawClaimsByEpoch` on payout.

### Recommendation
In `claimInstantWithdrawRequest`, decrement `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and `instantWithdrawClaimsByEpoch[epochNumber]` by the paid amount (or track and clear per-epoch entries on funded claims), mirroring the `folio_put` fix: releasing a claim must release every ledger reference to it. Alternatively, compute the defaulted-epoch instant basis as `pendingInstantWithdraws + _defaultPrefundedInstantReserve()` rather than the cumulative epoch counter.

### Proof of Concept
Foundry fork sketch (mirroring `test/foundry/IdleCreditVault.t.sol` helpers `_depositWithUser`, `_stopEpochAndCheckPrices`, `_createLoss`):

```solidity
// 1. LP deposits AA; attacker also deposits AA.
// 2. Enable instant withdraws: cdoEpoch.setInstantWithdrawParams(delay, minAprDiff, false).
// 3. Attacker calls requestInstantWithdraw(X) during epoch E; CDO funds partially via
//    getInstantWithdrawFunds/collectInstantWithdrawFunds(X) and attacker claims:
//    claimInstantWithdrawRequest(attacker) -> paid, instantWithdrawsRequests[attacker] = 0
//    but instantWithdrawsRequestsByEpoch[attacker][E] and instantWithdrawClaimsByEpoch[E] still = X.
// 4. Victim requests instant withdraw Y that stays unfunded -> pendingInstantWithdraws = Y.
// 5. Borrower repays nothing; stopEpoch(0,0) -> defaulted.
// 6. finalizeDefaultRecovery(R, source):
//    basis includes instantWithdrawClaimsByEpoch[E] = X + Y instead of Y (+prefunded part).
//    assert: strategy.defaultRecoveryPrice() < R * RECOVERY_FULL / trueBasis
//    i.e., victim's claim = Y * price / RECOVERY_FULL is haircut by the phantom X share,
//    and X's share of reserveAmount remains in the strategy forever
//    (attacker's defaulted-claim path reverts on instantWithdrawsRequests underflow).
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L380-393)
```text
  function claimInstantWithdrawRequest(address _user) external {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
      // Clear the defaulted-epoch instant receipt first, then continue so the same call can
      // also pay any older instant receipt that was already funded before default finalization.
      _claimDefaultedInstantWithdrawRequest(_user);
    }
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
  }
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L842-852)
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
```
