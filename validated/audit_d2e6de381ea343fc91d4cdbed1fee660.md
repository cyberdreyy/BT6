### Title
Default recovery accounting double-counts already-claimed instant withdrawals, inflating `defaultRecoveryPrice` and freezing recovery reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The kernel bug (CVE-2018-10902) is a missing concurrency guard producing a double free: the same allocation is accounted for twice. The analog in `IdleCreditVault` is that `claimInstantWithdrawRequest` pays out a user's instant-withdraw receipt but never clears `instantWithdrawsRequestsByEpoch` / `instantWithdrawClaimsByEpoch` or decrements `pendingInstantWithdraws`. When a borrower later defaults and `finalizeDefaultRecovery` runs in the same epoch, `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` still count the already-paid receipt, inflating `defaultRecoveryReserve`/`defaultRecoveryPrice` beyond actual held underlyings. Later claimants' `_transferDefaultRecovery` transfers revert for lack of balance — the reserve is accounted for twice but funded once.

### Finding Description
`claimInstantWithdrawRequest` zeroes `instantWithdrawsRequests[_user]` and transfers underlying, but leaves `instantWithdrawsRequestsByEpoch[_user][epoch]` and the per-epoch aggregate `instantWithdrawClaimsByEpoch[epoch]` untouched, and does not decrement `pendingInstantWithdraws` (that only happens in `collectInstantWithdrawFunds`) [1](#0-0) .

At default finalization, if any unfunded instant remainder exists (`pendingInstantWithdraws != 0`), the *full* current-epoch instant basis — including receipts already paid at par — is added to `totalBasis` via `defaultPendingClaimBasis` [2](#0-1) , and `prefundedReserve = instantBasis - pendingInstant` is counted as already-held underlying [3](#0-2)  even though the paid portion left the contract.

Additionally, the paid user's own later call underflows at `instantWithdrawsRequests[_user] -= claimBasis` (0 − claimBasis), permanently reverting [4](#0-3) .

### Impact Explanation
Recovery price is computed as `reserveAmount * RECOVERY_FULL / totalBasis` against a `reserveAmount` that credits funds already disbursed. Each already-claimed instant receipt of size X inflates both basis and reserve by ~X, so the real shortfall is the haircut portion, and when the reserve is depleted `_transferDefaultRecovery` reverts (`defaultRecoveryReserve -= _amount` underflow or insufficient balance), permanently freezing recovery payouts owed to other instant and normal receipt holders [5](#0-4) . Invariant broken: one receipt, one payout — the same underlying is paid once via `claimInstantWithdrawRequest` and counted again via `_defaultPrefundedInstantReserve`.

### Likelihood Explanation
Requires: instant withdrawals enabled, only partial instant funding collected via `collectInstantWithdrawFunds` before an epoch stop, one user claiming the funded portion, then borrower default and `finalizeDefaultRecovery` in the same epoch with `pendingInstantWithdraws != 0`. All attacker steps are unprivileged (request instant withdraw, claim); the default/finalize calls come from honest privileged roles. No existing guard stops it: there is no check that the claiming user's per-epoch basis was already consumed.

### Recommendation
In `claimInstantWithdrawRequest`, clear `instantWithdrawsRequestsByEpoch[_user][epoch]` for the relevant epoch and decrement `instantWithdrawClaimsByEpoch[epoch]` (and, if not already netted by `collectInstantWithdrawFunds`, `pendingInstantWithdraws`) so a paid receipt is excluded from default-epoch basis and prefunded-reserve accounting. Alternatively, when computing `defaultPendingClaimBasis`/`_defaultPrefundedInstantReserve`, derive the instant basis from unclaimed receipts rather than the historical per-epoch aggregate.

### Proof of Concept
Foundry fork PoC outline (standard epoch variant with instant withdrawals):

```solidity
// 1. enable instant withdraws, userA and userB deposit and requestInstantWithdraw in epoch N
//    instantWithdrawClaimsByEpoch[N] = amtA + amtB; pendingInstantWithdraws = amtA + amtB
// 2. borrower/manager path: collectInstantWithdrawFunds funds only amtA
//    pendingInstantWithdraws = amtB; strategy holds amtA
// 3. userA: cdo.claimInstantWithdrawRequest() -> receives amtA at par
//    instantWithdrawsRequestsByEpoch[A][N] still = amtA   // not cleared
// 4. borrower defaults; manager calls finalizeDefault / finalizeDefaultRecovery(recovered, src)
//    basis includes amtA + amtB; prefundedReserve credits amtA as held -> recoveryPrice inflated
// 5. userB: claimInstantWithdrawRequest -> _claimDefaultedInstantWithdrawRequest pays
//    amtB * recoveryPrice, but vault balance < reserve-denominated payout
// 6. last claimant reverts in _transferDefaultRecovery -> permanent freeze of owed recovery
assertEq(strategy.instantWithdrawsRequestsByEpoch(A, epochN), amtA); // stale, double-counted
```

### Citations

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-649)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L685-692)
```text
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L847-848)
```text
    instantWithdrawsRequestsByEpoch[_user][defaultEpoch] = 0;
    instantWithdrawsRequests[_user] -= claimBasis;
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
