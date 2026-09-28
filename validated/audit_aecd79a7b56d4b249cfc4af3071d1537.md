### Title
Stale `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch` are never cleared on funded claims, inflating the default-recovery basis and enabling a second payout from `defaultRecoveryReserve` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary

The CVE bug class (use of an uninitialized/stale value) maps to `IdleCreditVault`'s per-epoch instant-withdraw receipt accounting. `requestInstantWithdraw` records three pieces of state per request: the aggregate `instantWithdrawsRequests[user]`, the per-epoch receipt `instantWithdrawsRequestsByEpoch[user][epoch]`, and the epoch-level `instantWithdrawClaimsByEpoch[epoch]`. The normal funded claim path `claimInstantWithdrawRequest` clears only the aggregate — the two per-epoch values stay stale forever. When the borrower later defaults in the same epoch while other instant receipts are still unfunded, `defaultPendingClaimBasis` counts the stale epoch basis, and `_claimDefaultedInstantWithdrawRequest` pays against the stale per-user entry a second time from the recovery reserve.

### Finding Description

In `requestInstantWithdraw` (lines 356-375) the vault writes:

```solidity
instantWithdrawsRequests[_user] += _amount;
instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
pendingInstantWithdraws += _amount;
```

In `claimInstantWithdrawRequest` (lines 380-393), the non-default path burns `instantWithdrawsRequests[_user]`, sets it to 0, and transfers funded underlyings — but never touches `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` or `instantWithdrawClaimsByEpoch[currentEpoch]`. The only place those per-epoch entries are decremented is `_claimDefaultedInstantWithdrawRequest` (lines 842-856), which runs exclusively after default finalization.

Two broken invariants follow when a default is finalized in the same epoch with `pendingInstantWithdraws != 0` (i.e., `defaultInstantWithdrawsFinalized == true`):

1. **Inflated recovery basis.** `defaultPendingClaimBasis` (lines 644-649) returns `pendingWithdraws + instantWithdrawClaimsByEpoch[epochNumber]`. Already-claimed-and-paid instant receipts remain in `instantWithdrawClaimsByEpoch`, so `totalBasis` in `finalizeDefaultRecovery` (line 680) is overstated, depressing `defaultRecoveryPrice` for every honest claimant while the matching reserve is unchanged.

2. **Double payout.** After finalization, a user with a stale `instantWithdrawsRequestsByEpoch[user][defaultEpoch]` calls `claimInstantWithdrawRequest`; `_claimDefaultedInstantWithdrawRequest` reads the stale `claimBasis`, burns `claimBasis` receipt tokens, and pays `claimBasis * defaultRecoveryPrice / 1e18` from `defaultRecoveryReserve` — funds the user already received once. The `instantWithdrawsRequests[_user] -= claimBasis` underflow at line 848 is avoided because `requestInstantWithdraw` has no `defaultRecoveryFinalized` guard (lines 356-375): the attacker first opens a new instant request of at least the stale amount through the CDO, minting fresh receipt tokens and restoring a positive aggregate.

### Impact Explanation

Direct theft of the default recovery reserve. The attacker is paid twice for one instant withdrawal: once at par via `_transferFundedClaim` before default, and again at `defaultRecoveryPrice` from `defaultRecoveryReserve` after finalization. Loss equals `staleBasis * defaultRecoveryPrice / 1e18`, up to the attacker's full original instant amount, drawn from underlyings reserved for defaulted-epoch receipt holders. Every honest defaulted receipt is additionally haircut by the basis inflation in `finalizeDefaultRecovery`.

### Likelihood Explanation

Requires an epoch with instant withdrawals enabled, a borrower default in that epoch, and at least one other instant receipt left unfunded at finalization so `defaultInstantWithdrawsFinalized` is set — a routine default scenario. The attacker must post-default acquire/request receipt tokens ≥ their stale basis (the CDO must still route `requestInstantWithdraw`; the strategy itself does not block it), which costs roughly the haircut price of the stolen amount, so the attack is profitable whenever `defaultRecoveryPrice` exceeds the post-default cost of the receipt — typically true since post-default tranche/virtual price reflects the same haircut.

### Recommendation

In `claimInstantWithdrawRequest`, clear the per-epoch accounting alongside the aggregate: subtract the claimed amount from `instantWithdrawsRequestsByEpoch[_user][current-or-stored epoch]` and `instantWithdrawClaimsByEpoch[epoch]` (tracking the request epoch per user, analogous to `lastWithdrawRequest` for normal withdraws). Alternatively, make `defaultPendingClaimBasis` and `_claimDefaultedInstantWithdrawRequest` derive basis from still-outstanding receipts rather than cumulative epoch counters. Also add a `defaultRecoveryFinalized` guard to `requestInstantWithdraw`.

### Proof of Concept

```solidity
// Foundry fork test, contracts/strategies/idle/IdleCreditVault.sol
// Setup: epoch running, instant withdraws allowed, users A (attacker) and B.
uint256 amt = 10_000e6;

// 1. A and B request instant withdrawals in epoch N
cdo.requestInstantWithdraw(amt, AAtranche);        // as A
cdo.requestInstantWithdraw(amt, AAtranche);        // as B

// 2. CDO funds only A's claim (collectInstantWithdrawFunds for amt)
//    pendingInstantWithdraws == amt (B's) still > 0
vm.prank(address(cdo));
strategy.collectInstantWithdrawFunds(amt);
vm.prank(A);
cdo.claimInstantWithdrawRequest();                  // A paid at par
// BUG: instantWithdrawsRequestsByEpoch[A][N] and
//      instantWithdrawClaimsByEpoch[N] still hold amt (stale)

// 3. Borrower defaults in epoch N; manager/owner finalize
cdo._handleBorrowerDefault(...);                    // defaulted() = true
strategy.finalizeDefaultRecovery(recovered, source);
// defaultPendingClaimBasis == pendingWithdraws + 2*amt  (A counted twice)
// defaultRecoveryPrice is depressed by the phantom amt

// 4. A re-requests an instant withdraw >= stale basis post-finalization
//    (requestInstantWithdraw has no defaultRecoveryFinalized check)
//    then claims again: _claimDefaultedInstantWithdrawRequest pays
//    amt * defaultRecoveryPrice / 1e18 from defaultRecoveryReserve.
uint256 pre = underlying.balanceOf(A);
vm.prank(A);
cdo.claimInstantWithdrawRequest();
assertGt(underlying.balanceOf(A) - pre, 0);        // second payout on same receipt
```

Caveat: I could not fully verify the `IdleCDOEpochVariant`-side gating for post-finalization `requestInstantWithdraw` (the grep returned matches but line detail was not retrieved before iteration end). If the CDO blocks post-default instant requests, the residual impact is invariant (1): permanent dilution of `defaultRecoveryPrice` for all defaulted claimants plus a revert/DoS for stale-entry holders, which still warrants the fix.