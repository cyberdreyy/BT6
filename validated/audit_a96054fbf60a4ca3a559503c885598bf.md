### Title
Already-claimed instant withdrawals remain in `instantWithdrawClaimsByEpoch`, inflating the default-recovery reserve and overpaying early claimants - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
Analogous to the Axis `prefundingRefund = routing.funding + payoutSent_ - sold_` underflow (a counter that is decremented per-claim being reused as if it still held the original total), `IdleCreditVault` derives "prefunded" instant-withdraw reserves from `instantWithdrawClaimsByEpoch[epochNumber] - pendingInstantWithdraws`. `pendingInstantWithdraws` shrinks as funds are collected, but `instantWithdrawClaimsByEpoch` is **not** decremented when a user actually claims via `claimInstantWithdrawRequest` — it is only decremented inside `_claimDefaultedInstantWithdrawRequest`, which runs only *after* default finalization. So already-paid-out claims are counted as both pending claim basis and already-held prefunded reserve during `finalizeDefaultRecovery`, producing an overstated `recoveryPrice`.

### Finding Description
- `requestInstantWithdraw` records `instantWithdrawsRequestsByEpoch[user][epoch]`, `instantWithdrawClaimsByEpoch[epoch]`, and `pendingInstantWithdraws` [1](#0-0) 
- `claimInstantWithdrawRequest` pays the user, burns the receipt, and zeroes only the aggregate `instantWithdrawsRequests[user]` — the per-epoch entries and `instantWithdrawClaimsByEpoch` are left untouched [2](#0-1) 
- `collectInstantWithdrawFunds` decrements `pendingInstantWithdraws` as the borrower/CDO funds the queue [3](#0-2) 
- On default, `defaultPendingClaimBasis` adds the *full* `instantWithdrawClaimsByEpoch[epochNumber]` (including already-claimed receipts) to the recovery basis whenever `pendingInstantWithdraws != 0` [4](#0-3) 
- `_defaultPrefundedInstantReserve` then computes `instantBasis - pendingInstant`, treating the difference as underlying already held by the strategy — but already-claimed funds have left the contract [5](#0-4) 
- `finalizeDefaultRecovery` sums this phantom amount into `reserveAmount`, so `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` is computed on tokens that don't exist [6](#0-5) 

Concrete trace (running epoch, borrower partially funds instant queue, then defaults):
1. Users A and B each call `requestInstantWithdraw(100)` → `instantWithdrawClaimsByEpoch[e] = 200`, `pendingInstantWithdraws = 200`.
2. Borrower repays 100 → `collectInstantWithdrawFunds(100)` → `pendingInstantWithdraws = 100`, strategy holds 100.
3. A claims → receives 100; `instantWithdrawsRequests[A] = 0`, but `instantWithdrawsRequestsByEpoch[A][e]` and `instantWithdrawClaimsByEpoch[e] = 200` persist. Strategy now holds 0.
4. Borrower defaults; `finalizeDefaultRecovery` runs: `basis += 200` and `prefundedReserve = 200 - 100 = 100` is added to `reserveAmount` although the strategy holds nothing for it.
5. `recoveryPrice` is inflated; the reserve counter is drained by claims priced against non-existent funds, and `activeFinalNAV`/`defaultBBNav` are mispriced [7](#0-6) 

### Impact Explanation
`defaultRecoveryReserve` is set to a value larger than the strategy's real balance. Early recovery claimants (defaulted-epoch withdraw and instant receipts via `_transferDefaultRecovery`) are overpaid at the inflated `defaultRecoveryPrice`, and later claimants — including `postDefaultRequests` recipients — hit `defaultRecoveryReserve -= _amount` underflow or failed `safeTransfer`, i.e., permanent freezing/loss of their unclaimed recovery. Active tranche holders' NAV is also mispriced by the `_burn`/`_mint` reconciliation at line 701-705 [8](#0-7) . Loss magnitude equals the total of instant receipts claimed before finalization while `pendingInstantWithdraws` remains non-zero.

### Likelihood Explanation
Requires the confluence of: instant withdrawals enabled, partial borrower funding of the instant queue (`collectInstantWithdrawFunds` covering less than the full queue), at least one claimant withdrawing the funded portion, and then a borrower default in the same epoch. All steps use only unprivileged actions (tranche-token holder instant-withdraw requests and claims) sequenced around honest borrower/manager calls, which is permitted. No existing guard blocks it: the `_transferFundedClaim` reserve check only applies when `defaultRecoveryReserve != 0` (i.e., post-finalization), and nothing reconciles `instantWithdrawClaimsByEpoch` on claim.

### Recommendation
Mirror the audit fix (recompute from an immutable basis rather than a decremented counter): in `claimInstantWithdrawRequest`, also clear `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` and decrement `instantWithdrawClaimsByEpoch[epoch]` for receipts claimed pre-finalization (or track a separate `claimedInstantByEpoch` counter), so that `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` measure only genuinely outstanding, still-held claims. Alternatively compute the prefunded reserve from the strategy's actual `underlyingToken.balanceOf` minus other earmarked buckets.

### Proof of Concept
Foundry fork PoC sketch:

```solidity
// epoch running; users A and B hold tranche tokens
vm.prank(A); cdo.requestInstantWithdraw(trancheAA, 100e18); // via CDO -> vault.requestInstantWithdraw
vm.prank(B); cdo.requestInstantWithdraw(trancheAA, 100e18);

// borrower repays only 100 to CDO; manager routes to strategy
vm.prank(manager); cdo.collectInstantWithdrawFunds(100e18); // pendingInstantWithdraws: 200 -> 100

// A claims the funded half
vm.prank(A); cdo.claimInstantWithdrawRequest();
assertEq(underlying.balanceOf(address(vault)), 0);
assertEq(vault.instantWithdrawClaimsByEpoch(vault.epochNumber()), 200e18); // still 200

// borrower defaults; manager finalizes
cdo.defaulted(); // via _handleBorrowerDefault path
vault.finalizeDefaultRecovery(recovered, source);

// EXPECTED: basis for instant = 100 (only B), prefunded = 0
// ACTUAL:   basis += 200, prefundedReserve = 100 -> recoveryPrice inflated,
//           defaultRecoveryReserve > real balance -> last claimant's
//           _transferDefaultRecovery reverts on safeTransfer / reserve underflow
```

The invariant "strategy-held underlying ≥ `defaultRecoveryReserve`" is broken at finalization, matching the H-10 class: a per-claim-decremented counter reused as if it still held the prefunded total.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L685-705)
```text
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
    defaultBBNav = defaultBBNav * recoveryPrice / RECOVERY_FULL;
    if (activeBalance > activeFinalNAV) {
      _burn(idleCDO, activeBalance - activeFinalNAV);
    } else if (activeFinalNAV > activeBalance) {
      _mint(idleCDO, activeFinalNAV - activeBalance);
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L716-721)
```text
  function _defaultPrefundedInstantReserve() internal view returns (uint256 prefundedReserve) {
    uint256 pendingInstant = pendingInstantWithdraws;
    if (pendingInstant == 0) return prefundedReserve;
    uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
    if (instantBasis > pendingInstant) {
      prefundedReserve = instantBasis - pendingInstant;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L912-917)
```text
  function _transferDefaultRecovery(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    // Every defaulted or post-default claim consumes the isolated recovery reserve.
    defaultRecoveryReserve -= _amount;
    underlyingToken.safeTransfer(_user, _amount);
  }
```
