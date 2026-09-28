Let me check the CDO side instant-withdraw funding flow and whether claimed instant receipts are cleared anywhere.### Title
Claimed instant-withdraw receipts are never removed from per-epoch claim accounting, inflating the default-recovery basis and prefunded reserve until the recovery reserve is insolvent - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`claimInstantWithdrawRequest` clears only the aggregate `instantWithdrawsRequests[_user]`; it never clears `instantWithdrawsRequestsByEpoch[_user][epoch]` and never decrements `instantWithdrawClaimsByEpoch[epoch]` [1](#0-0) . If the borrower defaults in an epoch where instant receipts were partially funded and some users already claimed, `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` both count already-paid-out amounts, so `finalizeDefaultRecovery` computes a `defaultRecoveryPrice` on phantom basis backed by phantom reserve [2](#0-1) [3](#0-2) . Additionally, `_claimDefaultedInstantWithdrawRequest` can re-read a stale per-epoch basis for a user who already claimed, underflowing `instantWithdrawsRequests[_user] -= claimBasis` and permanently reverting that user's (and potentially the whole pool's) claim path [4](#0-3) .

### Finding Description
Analogous to the heap corruption class (stale memory read after free), per-epoch instant receipt bookkeeping is a "use-after-claim":

1. `requestInstantWithdraw` records `instantWithdrawsRequestsByEpoch[_user][epochNumber] += _amount` and `instantWithdrawClaimsByEpoch[epochNumber] += _amount` [5](#0-4) .
2. When the CDO funds instant claims, `collectInstantWithdrawFunds` only reduces `pendingInstantWithdraws`; the per-epoch totals stay [6](#0-5) .
3. When the user claims, `claimInstantWithdrawRequest` zeroes only `instantWithdrawsRequests[_user]` and transfers underlying at par. `instantWithdrawsRequestsByEpoch` and `instantWithdrawClaimsByEpoch` are never touched [7](#0-6) .
4. On a same-epoch borrower default with `pendingInstantWithdraws != 0`, `finalizeDefaultRecovery` adds the full `instantWithdrawClaimsByEpoch[epochNumber]` (including claimed amounts `A`) into `totalBasis`, and `_defaultPrefundedInstantReserve` adds `instantBasis - pendingInstant` (also including `A`) into `reserveAmount` as if that cash were still held [8](#0-7) .
5. `recoveryPrice = reserveAmount / totalBasis` is therefore pushed toward par by a phantom `A` on both sides; every real defaulted-epoch claimant is overpaid relative to the true residual reserve, and `defaultRecoveryReserve -= _amount` eventually underflows, reverting later claims [9](#0-8) .
6. Worse, a user who already claimed retains `instantWithdrawsRequestsByEpoch[_user][defaultEpoch] != 0`; on `claimInstantWithdrawRequest` after finalization, `_claimDefaultedInstantWithdrawRequest` computes `claimBasis` from the stale entry and executes `instantWithdrawsRequests[_user] -= claimBasis` against a zeroed aggregate, reverting and permanently blocking any residual funded claim of that user [10](#0-9) .

### Impact Explanation
Direct theft + permanent freezing: early/defaulted claimants draw `claimBasis * inflatedPrice` from `defaultRecoveryReserve` that is not actually backed, draining recovery funds belonging to later claimants and active tranche holders; final claimants' `_transferDefaultRecovery` reverts on reserve underflow (unrecoverable loss). Separately, users whose instant receipts were claimed pre-default are permanently bricked out of any remaining claim path via the `instantWithdrawsRequests` underflow. Loss magnitude equals the sum of instant withdrawals claimed in the default epoch.

### Likelihood Explanation
Requires only unprivileged actions: an instant withdraw request, a claim, and a subsequent same-epoch borrower default with partial instant funding — a normal market outcome, not attacker-privileged. Any KYC'd lender or tranche holder can create the stale entries; the arithmetic error is deterministic once the default is finalized.

### Recommendation
In `claimInstantWithdrawRequest`, clear `instantWithdrawsRequestsByEpoch[_user]` for all request epochs (or track and decrement per-epoch entries and `instantWithdrawClaimsByEpoch` symmetric to `_clearWithdrawClaimForEpoch`). Alternatively, maintain a separate "funded-but-unclaimed" instant bucket so `defaultPendingClaimBasis`/`_defaultPrefundedInstantReserve` only count receipts whose cash is still held.

### Proof of Concept
A Foundry fork test (mirroring `test/foundry/IdleCreditVault.t.sol` helpers) would: (1) deposit AA, start epoch, call `requestInstantWithdraw` for user U; (2) have the CDO partially fund via `collectInstantWithdrawFunds` so `pendingInstantWithdraws > 0` but U's claim is fully funded; (3) `claimInstantWithdrawRequest(U)` — observe `instantWithdrawsRequestsByEpoch[U][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` remain non-zero; (4) trigger borrower default in the same epoch and call `finalizeDefaultRecovery`; (5) assert `defaultRecoveryPrice` is inflated vs. true backing and that a subsequent `claimInstantWithdrawRequest(U)` reverts on underflow, while the last legitimate defaulted-epoch claimant reverts in `_transferDefaultRecovery`. Note: I was unable to confirm the exact CDO-side sequencing of `collectInstantWithdrawFunds` vs. epoch boundaries within the iteration budget; the per-epoch accounting gap in `IdleCreditVault.sol` itself is confirmed.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L685-688)
```text
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L912-917)
```text
  function _transferDefaultRecovery(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    // Every defaulted or post-default claim consumes the isolated recovery reserve.
    defaultRecoveryReserve -= _amount;
    underlyingToken.safeTransfer(_user, _amount);
  }
```
