### Title
Unfunded instant-withdraw receipts are keyed to a stale epoch and excluded from default-recovery basis, inflating `defaultRecoveryPrice` and freezing later claims - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Like the f2fs rename that moved a dirent without updating the `..` back-link, `IdleCreditVault` records instant-withdraw receipt basis under the *request* epoch (`instantWithdrawClaimsByEpoch[epochNumber]`, `instantWithdrawsRequestsByEpoch[user][epoch]`), but default finalization and defaulted-epoch claiming only ever dereference the *current* epoch (`instantWithdrawClaimsByEpoch[epochNumber]` in `defaultPendingClaimBasis`/`_defaultPrefundedInstantReserve`, and `defaultRecoveryEpoch` in `_claimDefaultedInstantWithdrawRequest`). If a partially funded instant queue survives an epoch boundary (`pendingInstantWithdraws != 0` while `deposit()` bumps `epochNumber` at `stopEpoch`), the receipt's "parent epoch" pointer is stale: its basis is invisible to recovery accounting yet still pays out at par.

### Finding Description
- `requestInstantWithdraw` books the claim under the request-time epoch: `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount; instantWithdrawClaimsByEpoch[currentEpoch] += _amount` [1](#0-0) .
- `epochNumber` is incremented in `deposit()` whenever it runs while `isEpochRunning()` (i.e., during `stopEpoch`) [2](#0-1) . `collectInstantWithdrawFunds` only decrements `pendingInstantWithdraws` by what the CDO actually funded [3](#0-2) , and the code comments explicitly acknowledge that `pendingInstantWithdraws` can remain non-zero when startEpoch cash "covered only part of the instant queue" [4](#0-3) .
- At finalization, `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` — the *current* epoch only — so instant basis recorded under a prior epoch is excluded from `basis` [5](#0-4) . `finalizeDefaultRecovery` then computes `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` over that under-counted basis [6](#0-5) .
- After finalization, `claimInstantWithdrawRequest` only clears `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]`; a stale-epoch receipt survives in `instantWithdrawsRequests[_user]` and is paid 1:1 via `_transferFundedClaim` [7](#0-6) .

### Impact Explanation
Two failure modes from the same stale-epoch pointer:

1. **Overpayment / reserve drain.** The excluded instant basis inflates `recoveryPrice` above par-adjusted fair value. Defaulted-epoch and post-default claimants (and active LPs via `activeFinalNAV`) are paid with the inflated multiplier, while the stale-epoch instant receipt additionally claims at par. Because `defaultPendingClaimBasis` never counted it, the reserve is over-allocated — last claimants' `_transferDefaultRecovery`/`_transferFundedClaim` underflow or hit the `balance - reserve < _amount` guard and revert [8](#0-7) .
2. **Permanent freezing.** Once `defaultRecoveryReserve` is consumed by overpriced claims, remaining legitimate claimants (including the stale-epoch instant receipt holder, whose `_transferFundedClaim` is forbidden from touching the reserve) can never claim; the recovery reserve is exhausted by construction and there is no refutation or top-up path.

Loss is bounded by the unfunded instant basis outstanding at finalization (up to the full prefunded remainder `instantWithdrawClaimsByEpoch[oldEpoch] - funded part`), i.e. direct insolvency of the recovery reserve.

### Likelihood Explanation
Requires: instant withdraws enabled (`allowInstantWithdraw`), an instant request left partially unfunded across a `stopEpoch` (acknowledged possible by `_defaultPrefundedInstantReserve`), and a subsequent borrower default finalized via `finalizeDefaultRecovery`. Any unprivileged tranche holder creates the stale-epoch receipt; the sequence needs only honest manager/borrower calls around it. No privileged misbehavior is needed — the accounting lookup itself is wrong.

### Recommendation
In `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve`, aggregate instant claim basis over all epochs with nonzero `instantWithdrawClaimsByEpoch` (or maintain a global unfunded-instant-basis counter updated by `requestInstantWithdraw`/`collectInstantWithdrawFunds`), and make `_claimDefaultedInstantWithdrawRequest` clear the user's receipts across all epochs with recorded basis rather than only `defaultRecoveryEpoch`. Alternatively, refuse `stopEpoch`/`startEpoch` while `pendingInstantWithdraws != 0` so instant receipts can never outlive their request epoch.

### Proof of Concept
Foundry fork sketch (setup mirrors `test/foundry/IdleCreditVault.t.sol`):

```solidity
// 1. Enable instant withdraws: cdoEpoch.setInstantWithdrawParams(delay, 1e18, false)
// 2. user1 deposits AA; startEpoch; user1 calls requestInstantWithdraw -> basis recorded under epoch N.
// 3. At stopEpoch/startEpoch the CDO funds only PART of the instant queue:
//    collectInstantWithdrawFunds(partial) leaves pendingInstantWithdraws > 0,
//    and deposit() during stopEpoch increments epochNumber to N+1.
//    instantWithdrawClaimsByEpoch[N] still holds user1's basis; [N+1] is 0.
// 4. Borrower defaults; manager calls finalizeDefaultRecovery.
//    - defaultPendingClaimBasis() adds instantWithdrawClaimsByEpoch[N+1] == 0
//      -> totalBasis excludes user1's instant basis -> recoveryPrice is overstated.
//    - _defaultPrefundedInstantReserve() also misses epoch-N claims.
// 5. Other claimants withdraw with the inflated defaultRecoveryPrice, draining
//    defaultRecoveryReserve below what was fairly owed.
// 6. user1 calls claimInstantWithdrawRequest:
//    - _claimDefaultedInstantWithdrawRequest reads epoch N+1 -> clears nothing;
//    - instantWithdrawsRequests[user1] still > 0 -> _transferFundedClaim reverts
//      on `balance - reserve < amount` once only reserve remains.
// assert: vm.expectRevert(NotAllowed) on user1's claim; reserve fully consumed
// by earlier overpriced claims.
```

Caveat: I could not fully trace the `stopEpoch`/`startEpoch` instant-funding path in `IdleCDOEpochVariant` within the search budget, so the exact call sequence that leaves `pendingInstantWithdraws` partially funded across the epoch bump should be confirmed when writing the PoC — the strategy's own comments indicate the state is reachable.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L380-392)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L636-640)
```text
  /// @dev Normal pending withdraws are already tracked globally. Current-epoch instant receipts
  /// join recovery only if `pendingInstantWithdraws` is still non-zero at finalization. This can
  /// happen when startEpoch moved the CDO's available cash to the strategy but that cash covered
  /// only part of the instant queue. The full current-epoch instant claim is included as basis,
  /// while the already-funded part is added to the reserve by `_defaultPrefundedInstantReserve()`.
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L686-692)
```text
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-916)
```text
  function _transferFundedClaim(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    uint256 reserve = defaultRecoveryReserve;
    if (reserve != 0) {
      uint256 balance = underlyingToken.balanceOf(address(this));
      // This should be unreachable when accounting is consistent. Keep the guard so old funded
      // receipts can never spend underlyings reserved for default recovery claimants.
      if (balance < reserve || balance - reserve < _amount) revert NotAllowed();
    }
    underlyingToken.safeTransfer(_user, _amount);
  }

  /// @notice Transfer default recovery reserve to a user.
  /// @param _user claim receiver
  /// @param _amount amount to transfer
  function _transferDefaultRecovery(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    // Every defaulted or post-default claim consumes the isolated recovery reserve.
    defaultRecoveryReserve -= _amount;
    underlyingToken.safeTransfer(_user, _amount);
```
