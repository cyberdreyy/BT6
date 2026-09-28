One more look at the remaining pieces — `collectWithdrawFunds` continuation and `_claimDefaultedWithdrawRequest`.### Title
Claimed instant-withdraw receipts are never erased from the per-epoch ledgers, corrupting default-recovery accounting (phantom reserve + phantom claim basis) - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The btrfs bug is a "deleted-but-not-really-deleted" index entry: an entry is erased from `root->inodes` while a live object still references it (and vice versa), so aggregate teardown loops over phantom/stale state forever. The analog in `IdleCreditVault` is the same ledger-shape bug: `claimInstantWithdrawRequest` clears the per-user aggregate `instantWithdrawsRequests[_user]` and burns the receipt tokens, but never erases the per-epoch entries `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]`. Those stale entries are later read as if they were live by `defaultPendingClaimBasis()` and `_defaultPrefundedInstantReserve()` during `finalizeDefaultRecovery`, inflating both the recovery basis and the counted reserve with funds that were already paid out. Recovery price is then computed against phantom underlying, so late recovery claimants' payouts exceed the real balance and their claims permanently revert.

### Finding Description
Three mappings track instant withdraws:

- `instantWithdrawsRequests[_user]` (aggregate, line 366)
- `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` (per-epoch, lines 371-372)

On the normal funded path, `claimInstantWithdrawRequest` does: [1](#0-0) 

It zeroes only the aggregate. Compare with the default path `_claimDefaultedInstantWithdrawRequest`, which correctly clears all three (`instantWithdrawsRequestsByEpoch`, aggregate, `instantWithdrawClaimsByEpoch`, `pendingInstantWithdraws`): [2](#0-1) 

So after a successful funded claim, `instantWithdrawsRequestsByEpoch[user][N]` and `instantWithdrawClaimsByEpoch[N]` remain nonzero — a live xarray entry for a dead object.

These stale entries are consumed at default finalization within the **same** `epochNumber` (epochNumber only bumps at `stopEpoch`, and instant claims are collected/claimed mid-epoch): [3](#0-2) [4](#0-3) 

Both are gated on `pendingInstantWithdraws != 0`, which is decremented by `collectInstantWithdrawFunds` (line 401). So the corruption needs a mixed epoch: at least one funded+claimed instant receipt (stale entry `C`) and at least one still-unfunded instant receipt (`U > 0`) in the same `epochNumber`.

At finalization (lines 685-692):

- `instantBasis = instantWithdrawClaimsByEpoch[N] = C + U`
- `prefundedReserve = instantBasis - pendingInstant` — counts `C` as underlying "already held" by the strategy, but `C` was transferred to the user at claim time. `reserveAmount` is overstated by `C`.
- `pendingBasis` includes `instantBasis` fully — overstated by `C`.
- `defaultRecoveryPrice = reserveAmount / totalBasis` is computed as if `C` phantom tokens sat in the contract backing a phantom claim.

Total legitimate payouts drawn from `defaultRecoveryReserve` equal `reserveAmount`, but the actual token balance only contains `recovered + U_prefunded_real`, i.e. short by exactly `C`. `_transferDefaultRecovery` will therefore overpay early claimants (each claim pays `claimBasis * price`) and the final claimant's transfer/underflow reverts — permanent freezing of the remainder of the recovery reserve. Additionally, `activeFinalNAV` / `defaultBBNav` are scaled by the inflated `recoveryPrice`, mispricing AA/BB tranche recovery.

The stale-entry user also can never clean up: calling `claimInstantWithdrawRequest` again hits `_claimDefaultedInstantWithdrawRequest` with stale `claimBasis = C`, then `instantWithdrawsRequests[_user] -= C` underflows (aggregate is already 0) or `_burn(_user, C)` fails — a permanent per-user freeze of that entry, mirroring the never-emptying `delayed_nodes` xarray.

No existing guard stops this: `pendingInstantWithdraws != 0` is satisfied by the legitimate unfunded receipt; `defaultRecoveryInitialized` only gates the loss path; and nothing validates `instantWithdrawClaimsByEpoch` against actually-held instant receipts.

### Impact Explanation
Direct theft + permanent freezing of unclaimed yield. With `C` = previously claimed amount and `price` = finalized recovery ratio, the contract pays out `C * price / 1e18` more underlying than it holds. Early recovery claimants (defaulted-epoch normal/instant receipt holders and, via the inflated `recoveryPrice`, AA/BB active holders through `defaultBBNav`) extract value backed by phantom reserves; the last claimant's claim reverts permanently, freezing their recovery share. Loss is bounded by the total instant-withdraw basis claimed in the defaulted epoch — up to the full instant queue for that epoch, e.g. with a 1M USDC claimed instant receipt, ~`1e6 * recoveryPrice` USDC is stolen/frozen.

### Likelihood Explanation
Requires a borrower default in an epoch where at least one instant withdrawal was already funded and claimed and another remains unfunded — a plausible sequence since `getInstantWithdrawFunds`/`claimInstantWithdrawRequest` and `_checkDefault`/`finalizeDefault` all operate within one `epochNumber`. All attacker-facing actions (requestInstantWithdraw, claimInstantWithdrawRequest) are callable by any tranche holder through `IdleCDOEpochVariant`; default finalization is performed by the honest manager. No privileged misbehavior needed. Medium likelihood: needs a default coinciding with a partially-funded instant queue in the same epoch, which the protocol explicitly supports and tests (mixed funded/unfunded instant claims, `testFinalizeDefault*` flows).

### Recommendation
Mirror the `__xa_cmpxchg` fix: delete the per-epoch entries when the live claim is deleted. In `claimInstantWithdrawRequest`, after computing `amount`, erase `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and decrement `instantWithdrawClaimsByEpoch[epochNumber]` by the user's per-epoch basis for the epoch(s) being claimed (the aggregate should track the still-open epochs, or store claim epoch per user). Alternatively, compute `instantWithdrawClaimsByEpoch` lazily from live receipts, or have `collectInstantWithdrawFunds`/`claimInstantWithdrawRequest` reconcile `instantWithdrawClaimsByEpoch[epoch]` with `pendingInstantWithdraws`. Add a regression test: two instant requests in one epoch, fund+claim one, default, finalize — assert `instantWithdrawClaimsByEpoch[epoch]` equals only the unclaimed basis.

### Proof of Concept
Foundry fork PoC (mirroring `testFinalizeDefault*` helpers in `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testStaleInstantEntryInflatesRecovery() external {
    // epoch running; userA and userB hold AA tranches
    // 1) userA + userB request instant withdraw in epoch N
    vm.prank(userA); cdoEpoch.requestWithdraw(amountA, address(AAtranche)); // instant path
    vm.prank(userB); cdoEpoch.requestWithdraw(amountB, address(AAtranche));

    // 2) manager funds only userA's part (partial collect), userA claims
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    // getInstantWithdrawFunds pulls amountA worth -> pendingInstantWithdraws = amountB
    vm.prank(manager); cdoEpoch.getInstantWithdrawFunds(); // funds amountA only
    vm.prank(userA);   cdoEpoch.claimInstantWithdrawRequest(); // funded claim succeeds

    IdleCreditVault cv = IdleCreditVault(address(strategy));
    // BUG: per-epoch ledgers still contain userA's claimed amount
    assertEq(cv.instantWithdrawsRequestsByEpoch(userA, cv.epochNumber()), amountA); // stale
    assertEq(cv.instantWithdrawClaimsByEpoch(cv.epochNumber()), amountA + amountB); // stale
    assertGt(cv.pendingInstantWithdraws(), 0); // userB still unfunded

    // 3) borrower defaults in same epochNumber; manager finalizes
    _checkDefault();
    uint256 recovered = /* computed via defaultPendingClaimBasis() */;
    deal(defaultUnderlying, manager, recovered);
    vm.startPrank(manager);
    IERC20Detailed(defaultUnderlying).approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();

    // 4) recoveryPrice was computed against reserveAmount inflated by amountA,
    //    but amountA already left the contract. Sum of claims > balance:
    //    userB claims -> ok; remaining claimants' _transferDefaultRecovery reverts,
    //    permanently freezing C * price worth of recovery.
    vm.prank(userB); cdoEpoch.claimInstantWithdrawRequest();
    vm.expectRevert(); // last defaulted-epoch receipt holder can never claim
    vm.prank(userC); cdoEpoch.claimWithdrawRequest();
}
```

Note: exact manager-side funding amounts follow the existing `testFinalizeDefault*` test patterns (`_getInstantFunds`, `_expectedFundsEndEpoch`, `defaultPendingClaimBasis` formula at test line ~4531). The assertion of interest is the stale `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch` values after step 2 and the insolvent `defaultRecoveryReserve` after step 3 — I verified the ledger-clearing asymmetry and the finalization math in `IdleCreditVault.sol` but did not execute the PoC against the full `IdleCDOEpochVariant` call chain, so the exact revert point in step 4 (`safeTransfer` insufficiency vs. `defaultRecoveryReserve` underflow inside `_transferDefaultRecovery`, which I could not read in full) should be confirmed by running it.

### Citations

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L842-855)
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
```
