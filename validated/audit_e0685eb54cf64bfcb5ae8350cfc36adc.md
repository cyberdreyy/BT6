### Title
Instant-withdraw claim skips per-epoch bookkeeping cleanup, inflating `defaultPendingClaimBasis` and permanently diluting/stranding default-recovery funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The nghttp2 bug class is "cleanup skipped on a completed/error path leaves bookkeeping structures alive, corrupting later aggregate accounting." `IdleCreditVault.claimInstantWithdrawRequest` exhibits exactly this: when a user claims a funded instant withdrawal, the function clears `instantWithdrawsRequests[_user]` but never clears `instantWithdrawsRequestsByEpoch[_user][epoch]` or decrements `instantWithdrawClaimsByEpoch[epoch]` [1](#0-0) . Those per-epoch structures are later read wholesale by `defaultPendingClaimBasis` during `finalizeDefaultRecovery` [2](#0-1) , so already-paid claims are counted a second time in the recovery denominator.

### Finding Description
Normal flow:

1. `requestInstantWithdraw` records three pieces of bookkeeping: `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][epoch]`, and `instantWithdrawClaimsByEpoch[epoch]` [3](#0-2) .
2. The CDO funds the request via `collectInstantWithdrawFunds`, which decrements `pendingInstantWithdraws` [4](#0-3) .
3. The user calls `claimInstantWithdrawRequest`, burns the receipt, and is paid at par — but the two per-epoch entries are left populated [5](#0-4) .

If the same epoch later defaults with `pendingInstantWithdraws != 0` (any other still-unfunded instant request suffices, and `_defaultPrefundedInstantReserve` already contemplates partially funded epochs), `defaultPendingClaimBasis` returns `pendingWithdraws + instantWithdrawClaimsByEpoch[epochNumber]` — including the attacker's already-paid claim [6](#0-5) . `finalizeDefaultRecovery` then computes `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` on the inflated `totalBasis` [7](#0-6) .

No guard stops this: `_transferDefaultRecovery` only decrements the reserve, and the stale-entry double-claim path itself reverts on the `instantWithdrawsRequests[_user] -= claimBasis` underflow [8](#0-7)  — but the dilution is already locked in at finalization, before any claim.

### Impact Explanation
Every legitimate defaulted-epoch claimant (normal withdraw receipts, unfunded instant receipts, post-default requests) is paid `claimBasis * defaultRecoveryPrice / 1e18` from `defaultRecoveryReserve`. Inflating `totalBasis` by the attacker's already-paid amount `X` scales `recoveryPrice` down by a factor of `totalBasis/(totalBasis + X)`, directly transferring value away from all honest claimants. The reserve portion corresponding to the phantom basis has no claimant — the attacker's second claim reverts — so it is permanently stranded in the strategy as undistributable dust. Quantified loss: `X * reserveAmount / (totalBasis + X)` of underlyings is frozen forever, and honest claimants lose that same fraction of their recovery.

### Likelihood Explanation
The attacker is an unprivileged tranche/instant-withdraw user. Requirements: (a) request and claim an instant withdrawal in epoch N; (b) at least one other instant receipt remains unfunded (`pendingInstantWithdraws != 0`) when epoch N's `stopEpoch` triggers borrower default; (c) `finalizeDefaultRecovery` is called while `epochNumber` is still N (it only increments on a subsequent `deposit` while running [9](#0-8) ). Default at epoch stop is a designed, reachable path (`_handleBorrowerDefault`), and the attacker can size `X` arbitrarily to maximize dilution, so the loss scales with their funded claim.

### Recommendation
In `claimInstantWithdrawRequest`, clear the requester's per-epoch bookkeeping on the funded-claim path: after determining the claim, zero `instantWithdrawsRequestsByEpoch[_user][epochNumber]` (or track and clear the request epoch) and decrement `instantWithdrawClaimsByEpoch[epoch]` by the claimed amount, mirroring the cleanup in `_claimDefaultedInstantWithdrawRequest` [10](#0-9) . Alternatively, make `defaultPendingClaimBasis` derive instant basis only from still-unclaimed receipts rather than the lifetime epoch aggregate.

### Proof of Concept
Foundry fork PoC (against the repo's existing `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
// 1. Setup: start epoch N with deposits; borrower draws funds.
// 2. Attacker requests instant withdraw of amount X via cdoEpoch.requestInstantWithdraw(X, AAtranche).
// 3. Honest user also requests instant withdraw of amount Y (kept unfunded).
// 4. Manager funds attacker's request: stopEpoch/fulfill path calls
//    strategy.collectInstantWithdrawFunds(X) -> pendingInstantWithdraws = Y only.
//    Attacker calls cdoEpoch.claimInstantWithdrawRequest() -> paid X at par.
//    Stale: instantWithdrawsRequestsByEpoch[attacker][N] == X,
//           instantWithdrawClaimsByEpoch[N] == X + Y.
// 5. Warp past epochEndDate; borrower underpays so stopEpoch triggers _handleBorrowerDefault.
// 6. Owner calls finalizeDefault/finalizeDefaultRecovery with recoveredAmount R.
//    defaultPendingClaimBasis() = pendingWithdraws + (X + Y)  // X counted twice
//    recoveryPrice = R * 1e18 / (activeBasis + pendingWithdraws + X + Y)  // diluted
// 7. assert: honest user's _transferDefaultRecovery payout <
//    Y * R / (activeBasis + pendingWithdraws + Y)  // expected undiluted payout
// 8. assert: attacker re-claim reverts (underflow) and
//    defaultRecoveryReserve retains ~ X * R / totalBasis dust that no one can claim
//    -> permanently frozen funds.
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-611)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
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
