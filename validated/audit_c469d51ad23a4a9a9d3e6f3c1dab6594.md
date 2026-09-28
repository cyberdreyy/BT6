### Title
Claimed instant-withdraw receipts leak into default recovery basis, inflating claim pool and draining the recovery reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The multer bug class — an aborted/finished operation that removes the visible artifact but never releases the underlying resource — maps to `claimInstantWithdrawRequest`. When a user claims a funded instant withdrawal, the function clears the aggregate `instantWithdrawsRequests[_user]` but never clears the per-epoch entries `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]`. Those stale per-epoch entries are later counted in `defaultPendingClaimBasis()` during `finalizeDefaultRecovery`, inflating both the recovery basis and the phantom "prefunded reserve" for receipts whose funds were already paid out.

### Finding Description
`requestInstantWithdraw` records three pieces of per-epoch state [1](#0-0) : `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, and `instantWithdrawClaimsByEpoch[currentEpoch]`.

The normal funded-claim path only clears the aggregate [2](#0-1) . Contrast with the default claim path `_claimDefaultedInstantWithdrawRequest`, which correctly clears all of them [3](#0-2) . No other code path (`collectInstantWithdrawFunds` at lines 398-403, CDO-side funding) decrements `instantWithdrawClaimsByEpoch` — the per-epoch claim total is leaked, exactly like multer's deleted-but-open file descriptor.

The stale state is consumed at default finalization:

- `defaultPendingClaimBasis()` adds `instantWithdrawClaimsByEpoch[epochNumber]` to the recovery basis whenever `pendingInstantWithdraws != 0` [4](#0-3) .
- `_defaultPrefundedInstantReserve()` computes `instantBasis - pendingInstant` and adds it to `reserveAmount` as supposedly-held underlying [5](#0-4)  — but that underlying was already transferred to the claiming user and is not held.

`finalizeDefaultRecovery` then computes `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` [6](#0-5)  with both numerator and denominator inflated by phantom claims.

### Impact Explanation
Let `C` be instant receipts already claimed in the default epoch. Then `totalBasis` and `reserveAmount` are each inflated by `C`, while the actual strategy balance only backs `reserveAmount - C`. 

- If real recovery `R < totalBasis - C` (a genuine loss), the phantom term pushes `recoveryPrice` above the sustainable ratio. Every real claim pays `claimBasis * recoveryPrice / RECOVERY_FULL` against a reserve that is `C` short. The reserve is drained early and later honest claimants' `_transferDefaultRecovery` reverts — permanent freezing of their unclaimed recovery, or direct theft by earlier claimants of funds that should have been shared pro rata.
- The attacker (any lender) profits by being first to claim at the inflated price; alternatively the leaked basis guarantees insolvency of the recovery pool even with zero attacker sophistication — it triggers on any default in an epoch where some instant requests were funded and claimed while others remained pending.

Broken invariant: one receipt, one payout; recovery reserve must equal sum of outstanding claims times recovery price.

### Likelihood Explanation
Requires instant withdrawals enabled (`allowInstantWithdraw`), a default in the same epoch where at least one instant claim was already paid and `pendingInstantWithdraws` is still non-zero. Attacker is an ordinary KYC-passed lender; no privileged action needed — the leak is created by the honest claim path itself. Default timing depends on the borrower (honest per scope), but the misaccounting exists unconditionally and crystallizes on any qualifying default.

### Recommendation
In `claimInstantWithdrawRequest`, mirror the cleanup in `_claimDefaultedInstantWithdrawRequest`: zero `instantWithdrawsRequestsByEpoch[_user][epoch]` and decrement `instantWithdrawClaimsByEpoch[epoch]` for each epoch contributing to the claimed amount (or track and clear the current-epoch entries). Alternatively, recompute the default-epoch instant basis from `pendingInstantWithdraws` plus unclaimed per-user entries rather than the never-decremented `instantWithdrawClaimsByEpoch`.

### Proof of Concept
Foundry fork sketch (extend `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testInstantClaimLeaksRecoveryBasis() external {
    // standard setup with instant withdraws enabled
    _depositWithUser(user1, 100_000e6, true);
    _depositWithUser(user2, 100_000e6, true);

    // stop epoch, set lower APR so instant withdraws are allowed
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

    // user1 and user2 both request instant withdraw in epoch N
    vm.prank(user1); cdoEpoch.requestInstantWithdraw(...);
    vm.prank(user2); cdoEpoch.requestInstantWithdraw(...);

    // startEpoch funds only part of pendingInstant (or all; user1 claims)
    vm.prank(manager); cdoEpoch.startEpoch();
    // borrower tops up so pendingInstant > 0 remains for user2 only
    vm.prank(user1); cdoEpoch.claimInstantWithdrawRequest(); // pays user1 fully

    // borrower defaults in the same epoch
    _handleBorrowerDefault(); // via owner/manager default flow

    // inflate check: instantWithdrawClaimsByEpoch[N] still counts user1
    assertGt(strategy.instantWithdrawClaimsByEpoch(strategy.epochNumber()), user2Basis);

    vm.prank(manager);
    cdoEpoch.finalizeDefault(recovered, recoverySource);

    // recoveryPrice is computed on inflated basis/reserve:
    // user2 claims at inflated price; recovery reserve runs dry before
    // all honest claimants are paid -> later claim reverts on insufficient reserve
}
```

The assertion `instantWithdrawClaimsByEpoch[epoch] > outstanding claims` after `claimInstantWithdrawRequest` is the minimal reproducer; it demonstrates the leaked descriptor. Expected outcome: `defaultRecoveryPrice` is skewed and `defaultRecoveryReserve` underflows/drains before the last honest defaulted-epoch claim.

Caveat: I verified `collectInstantWithdrawFunds` does not clear the per-epoch mappings, but I could not exhaustively confirm there is no other clearing path elsewhere in the CDO contract within the available search budget; the grep over `contracts/**` for `instantWithdrawClaimsByEpoch` showed writes only in `IdleCreditVault.sol`, supporting the finding.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L847-853)
```text
    instantWithdrawsRequestsByEpoch[_user][defaultEpoch] = 0;
    instantWithdrawsRequests[_user] -= claimBasis;
    uint256 pending = pendingInstantWithdraws;
    // `pendingInstantWithdraws` is only the unfunded remainder. If this user's claim is larger,
    // the extra amount was already counted as prefunded reserve during default finalization.
    pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis;
    instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis;
```
