### Title
Stale `instantWithdrawClaimsByEpoch` / `instantWithdrawsRequestsByEpoch` after a normal instant claim inflates default-recovery basis and prefunded reserve, corrupting `defaultRecoveryPrice` — ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
Analogous to the missing `of_node_put()` refcount decrement, `requestInstantWithdraw` increments the per-epoch aggregate `instantWithdrawClaimsByEpoch[currentEpoch]` (IdleCreditVault.sol:371-374), but the normal claim path `claimInstantWithdrawRequest` never decrements it or clears `instantWithdrawsRequestsByEpoch[_user][epoch]` (IdleCreditVault.sol:387-392). The only place that decrements is `_claimDefaultedInstantWithdrawRequest` (IdleCreditVault.sol:847-853). This stale "refcount" is later read by `defaultPendingClaimBasis` (line 647) and `_defaultPrefundedInstantReserve` (lines 716-722) during `finalizeDefaultRecovery`, so already-paid instant receipts are counted again in the recovery basis and treated as already-held strategy funds.

### Finding Description
In `claimInstantWithdrawRequest` (IdleCreditVault.sol:380-393) the funded path does:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

It never touches `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` or `instantWithdrawClaimsByEpoch[currentEpoch]`, which were incremented at request time. `pendingInstantWithdraws` is decremented separately via `collectInstantWithdrawFunds` (line 401), so the per-epoch ledgers survive a fully paid claim.

At default finalization:

- `defaultPendingClaimBasis()` adds `instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0` (lines 646-648), inflating `totalBasis`.
- `_defaultPrefundedInstantReserve()` returns `instantBasis - pendingInstant` (lines 716-722). Stale basis makes this count "already-held" underlying that was actually paid out to the claimed user, so `reserveAmount` is overstated without any corresponding tokens.

Since `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` (line 688), both effects push `defaultRecoveryPrice` above the true reserve-backed ratio. Recovery claims then pay `claimBasis * defaultRecoveryPrice` (lines 783, 855) against a reserve that is short by exactly the stale amount.

### Impact Explanation
Sequence (unprivileged lender as attacker/victim-free setup, honest privileged roles):

1. Epoch N running: user A (KYC'd lender) requests instant withdraw; `instantWithdrawClaimsByEpoch[N] += X`.
2. `startEpoch`/`collectInstantWithdrawFunds` funds it; A calls `claimInstantWithdrawRequest` and is paid in full. `instantWithdrawClaimsByEpoch[N]` still contains X; `instantWithdrawsRequestsByEpoch[A][N]` still contains X.
3. Still in epoch N, user B requests an instant withdraw of Y; `pendingInstantWithdraws = Y`, `instantWithdrawClaimsByEpoch[N] = X + Y`.
4. Borrower defaults; `_handleBorrowerDefault`/`finalizeDefaultRecovery` runs. `prefundedReserve = (X + Y) - Y = X` is added to `reserveAmount` even though those X underlyings left the strategy. `totalBasis` also includes X.
5. `defaultRecoveryPrice` is set too high (claims assume X phantom tokens are held). B's claim — and genuine defaulted-epoch receipts — draw down `defaultRecoveryReserve` at the inflated price; the final claimant's `_transferDefaultRecovery` reverts on insufficient balance, permanently freezing their unclaimed recovery, or earlier claimants over-withdraw, draining recovery owed to later claimants. Quantified loss: up to the full stale instant-claim amount X.

### Likelihood Explanation
Medium. It requires (a) an instant withdraw claimed mid-epoch, (b) a subsequent instant request in the same epoch leaving `pendingInstantWithdraws != 0`, and (c) a borrower default before `epochNumber` increments. Instant-withdraw and default flows are both supported, exercised paths; no guard clears the per-epoch ledgers on the funded claim path, and nothing in `requestWithdraw`/`requestInstantWithdraw` blocks re-requesting in the same epoch.

### Recommendation
In `claimInstantWithdrawRequest`, mirror the decrement done in `_claimDefaultedInstantWithdrawRequest`: clear `instantWithdrawsRequestsByEpoch[_user][current-or-request-epoch]` and subtract the claimed amount from `instantWithdrawClaimsByEpoch`. Because receipts may span epochs, the claim path should track the request epoch per user (as `lastWithdrawRequest` does for normal withdraws) or clear the user's per-epoch entries proportionally before zeroing `instantWithdrawsRequests[_user]`.

### Proof of Concept
Foundry fork sketch (extend `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testStaleInstantBasisInflatesRecovery() external {
    // epoch N running; allow instant withdraws
    idleCDO.depositAA(200_000 * ONE_SCALE);
    _startEpochAndCheckPrices(0);            // epochNumber becomes N+1 at stop; use N = strategy.epochNumber()

    // user A instant-withdraws X
    vm.prank(userA);
    cdoEpoch.requestInstantWithdraw(X, address(AAtranche));
    // CDO funds the instant queue and A claims normally
    cdoEpoch.getInstantWithdrawFunds();      // manager/honest call -> collectInstantWithdrawFunds
    vm.prank(userA);
    cdoEpoch.claimInstantWithdrawRequest();  // instantWithdrawsRequests[A] = 0
    assertEq(strategy.instantWithdrawsRequests(userA), 0);
    // BUG: stale ledgers remain
    uint256 stale = strategy.instantWithdrawClaimsByEpoch(strategy.epochNumber());
    assertEq(stale, X);

    // user B instant-withdraws Y in the SAME epoch
    vm.prank(userB);
    cdoEpoch.requestInstantWithdraw(Y, address(AAtranche));
    assertEq(strategy.instantWithdrawClaimsByEpoch(strategy.epochNumber()), X + Y);

    // borrower defaults; owner/manager finalize recovery
    cdoEpoch.stopEpochWithDuration(/* borrower fails to fund */);
    cdoEpoch.finalizeDefaultRecovery(recovered, recoverySource);

    // recoveryPrice assumed prefundedReserve = X that is NOT held
    uint256 reserve = strategy.defaultRecoveryReserve();
    uint256 price   = strategy.defaultRecoveryPrice();
    // claimed payouts total more than reserve -> last claim reverts / freezes
    vm.prank(userB);
    vm.expectRevert(); // ERC20 insufficient balance in _transferDefaultRecovery
    cdoEpoch.claimInstantWithdrawRequest();
}
```

The assertions `instantWithdrawClaimsByEpoch == X` after A's full claim and the inflated `prefundedReserve = X` at finalization demonstrate the refcount-style leak and the resulting reserve shortfall.