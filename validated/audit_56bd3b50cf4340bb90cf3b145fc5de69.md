### Title
Stale `instantWithdrawsRequestsByEpoch` entries let an already-paid instant-withdraw receipt be claimed again at the default recovery price, corrupting `defaultPendingClaimBasis` and draining `defaultRecoveryReserve` - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`requestInstantWithdraw` records per-epoch receipt data in `instantWithdrawsRequestsByEpoch[user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]`, but `claimInstantWithdrawRequest` never clears those per-epoch entries when a funded receipt is paid. If the same epoch later ends in a borrower default, the stale, already-paid basis is counted again in `defaultPendingClaimBasis`, `_defaultPrefundedInstantReserve`, and `_claimDefaultedInstantWithdrawRequest`, giving the user a second payout and inflating the reserve with funds that no longer exist.

### Finding Description
The bug is a stale/uninitialized-state analog of the libtiff uninitialized-resource crash: accounting reads per-epoch receipt state that was logically consumed but never zeroed.

- `requestInstantWithdraw` writes `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount` and `instantWithdrawClaimsByEpoch[currentEpoch] += _amount` [1](#0-0) 
- `claimInstantWithdrawRequest` only zeroes the aggregate `instantWithdrawsRequests[_user]`; the per-epoch maps are left populated [2](#0-1) 
- The only place that decrements either per-epoch map is `_claimDefaultedInstantWithdrawRequest` [3](#0-2) 
- At default finalization, `defaultPendingClaimBasis` adds the full `instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0`, and `_defaultPrefundedInstantReserve` treats `instantBasis - pendingInstant` as underlying already held for claimants — even though part of that basis was already paid out [4](#0-3) [5](#0-4) 

Attack sequence (running epoch N → default in epoch N):

1. Attacker (KYC'd lender) calls `requestInstantWithdraw(A)` via the CDO in epoch N.
2. CDO/borrower funds it: `collectInstantWithdrawFunds(A)` moves `pendingInstantWithdraws` back toward 0 and transfers `A` underlyings to the strategy.
3. Attacker calls `claimInstantWithdrawRequest` and receives `A`. `instantWithdrawsRequestsByEpoch[attacker][N]` still equals `A` and `instantWithdrawClaimsByEpoch[N]` still equals `A`.
4. Attacker calls `requestInstantWithdraw(B)` in the same epoch N. Now `instantWithdrawsRequestsByEpoch[attacker][N] = A + B`, `instantWithdrawClaimsByEpoch[N] = A + B`, but only `B` is unfunded (`pendingInstantWithdraws = B`).
5. Epoch N ends with a borrower default and `finalizeDefaultRecovery` runs while `pendingInstantWithdraws != 0`, so `defaultInstantWithdrawsFinalized = true`.
6. `_defaultPrefundedInstantReserve` returns `(A + B) - B = A`, crediting `A` as held recovery funds that were already paid to the attacker; `defaultPendingClaimBasis` counts `A + B` instead of `B`.
7. Attacker calls `claimInstantWithdrawRequest`; `_claimDefaultedInstantWithdrawRequest` pays `(A + B) * defaultRecoveryPrice / RECOVERY_FULL` — a second payout on the already-claimed `A`.

### Impact Explanation
The attacker is paid twice for the same receipt basis `A`, directly draining `defaultRecoveryReserve` at the recovery price. Because `reserveAmount` was inflated by the phantom prefunded `A` that no longer exists in the contract, honest defaulted-epoch claimants (normal and instant) either receive less than the computed recovery price or revert on `defaultRecoveryReserve -= _amount` underflow / insufficient balance, permanently freezing the tail of the recovery distribution. Loss is quantifiable: up to `A * defaultRecoveryPrice` stolen plus up to `A` of unbacked reserve causing underflow reverts for later claimants.

### Likelihood Explanation
Requires only an unprivileged KYC-passing lender able to call `requestInstantWithdraw`/`claimInstantWithdrawRequest` twice in one epoch that ends in default with a partially unfunded instant bucket — a normal operating state (partial prefunding is explicitly supported by `_defaultPrefundedInstantReserve`). No privileged misbehavior, no oracle manipulation, no legacy code. Existing guards do not stop it: `_ensureDefaultRecoveryInitialized` only checks `pendingInstantWithdraws` before the stale epoch is created, and nothing reconciles the by-epoch maps on a funded claim.

### Recommendation
In `claimInstantWithdrawRequest`, also decrement `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and `instantWithdrawClaimsByEpoch[epochNumber]` by the claimed amount (or track a per-user funded marker), so per-epoch basis reflects only still-unpaid receipts. Alternatively, reconcile `instantWithdrawClaimsByEpoch` against already-paid amounts inside `_defaultPrefundedInstantReserve`/`defaultPendingClaimBasis` before computing the recovery basis.

### Proof of Concept
```solidity
// Foundry fork test sketch — IdleCreditVault stale instant receipt double-claim
// Setup: epoch N running, attacker is a wallet-allowed lender.

// 1) Attacker requests instant withdraw A in epoch N
vm.prank(attacker);
cdoEpoch.requestInstantWithdraw(A, address(AAtranche)); // -> strategy.requestInstantWithdraw

// 2) CDO funds the request (borrower/CCO transfers A underlyings to strategy)
vm.prank(address(cdoEpoch));
strategy.collectInstantWithdrawFunds(A);
assertEq(strategy.pendingInstantWithdraws(), 0);

// 3) Attacker claims A — per-epoch maps stay stale
vm.prank(attacker);
cdoEpoch.claimInstantWithdrawRequest();
assertEq(underlying.balanceOf(attacker) - balPre, A);
assertEq(strategy.instantWithdrawsRequestsByEpoch(attacker, strategy.epochNumber()), A); // STALE

// 4) Attacker requests B in the same epoch N; leave it unfunded
vm.prank(attacker);
cdoEpoch.requestInstantWithdraw(B, address(AAtranche));
assertEq(strategy.pendingInstantWithdraws(), B);
assertEq(strategy.instantWithdrawClaimsByEpoch(strategy.epochNumber()), A + B); // inflated basis

// 5) Borrower defaults in epoch N; manager finalizes recovery R
//    pendingInstantWithdraws != 0 -> defaultInstantWithdrawsFinalized = true
//    _defaultPrefundedInstantReserve() returns A (phantom, already paid)
//    defaultPendingClaimBasis() includes A + B instead of B
_stopEpochDefault();
vm.prank(manager);
cdoEpoch.finalizeDefault(recovered, manager);

// 6) Attacker claims defaulted receipt: paid (A + B) * defaultRecoveryPrice
//    -> second payout on A; reserve drained / later claimants underflow
vm.prank(attacker);
cdoEpoch.claimInstantWithdrawRequest();
uint256 stolen = (A * strategy.defaultRecoveryPrice()) / strategy.RECOVERY_FULL();
assertGt(underlying.balanceOf(attacker), expectedHonestPayout);

// 7) Honest claimant's _transferDefaultRecovery underflows or pays short
vm.prank(honestUser);
vm.expectRevert(); // defaultRecoveryReserve -= amount underflow
cdoEpoch.claimInstantWithdrawRequest();
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L844-855)
```text
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
