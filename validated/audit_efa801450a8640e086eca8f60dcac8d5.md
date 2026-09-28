### Title
Claimed instant-withdraw receipts are never removed from `instantWithdrawClaimsByEpoch`, inflating `defaultPendingClaimBasis` and `defaultRecoveryPrice`, which strands recovery reserve and permanently freezes later claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug is a share-burn path that fails to decrement a tracked share aggregate, so the accounting overstates backing and later liquidation/claims fail for lack of shares. The same desync exists in `IdleCreditVault`: `claimInstantWithdrawRequest` burns the user's receipt and pays it out, but never decrements `instantWithdrawsRequestsByEpoch[_user][epoch]` or `instantWithdrawClaimsByEpoch[epoch]`. If a borrower default is later finalized in that same epoch while `pendingInstantWithdraws != 0`, `defaultPendingClaimBasis()` adds the stale (already-claimed) amount, and `_defaultPrefundedInstantReserve()` counts it again as prefunded reserve, inflating `defaultRecoveryPrice` above the reserve's real backing.

### Finding Description
`requestInstantWithdraw` records both per-user and aggregate per-epoch receipt basis:

```solidity
instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
```
(`IdleCreditVault.sol:371-372`)

A normal claim only clears `instantWithdrawsRequests[_user]` and burns the receipt:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```
(`IdleCreditVault.sol:387-392`)

Neither `instantWithdrawsRequestsByEpoch[_user][epochNumber]` nor `instantWithdrawClaimsByEpoch[epochNumber]` is reduced — unlike the default path `_claimDefaultedInstantWithdrawRequest`, which does decrement both (`IdleCreditVault.sol:847-853`), proving the aggregate is intended to track *outstanding* claims.

At default finalization:

```solidity
basis = pendingWithdraws;
if (pendingInstantWithdraws != 0) {
  basis += instantWithdrawClaimsByEpoch[epochNumber];
}
```
(`defaultPendingClaimBasis`, `IdleCreditVault.sol:644-649`)

```solidity
prefundedReserve = instantBasis - pendingInstant;   // includes already-paid receipts
reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;
```
(`IdleCreditVault.sol:685-688`, `716-722`)

Because `instantBasis` includes receipts already claimed at par, `prefundedReserve` (counted as underlying *held* in the strategy) and `totalBasis` are both overstated by ghost claims whose tokens left the contract. `defaultRecoveryPrice` is therefore computed against a reserve that does not exist.

### Impact Explanation
Two symmetric failure modes, both quantified by the stale claimed amount `C`:

- If `instantWithdrawClaimsByEpoch` dominates the inflation, `recoveryPrice` is set too low: every legitimate recovery claimant is underpaid and `defaultRecoveryReserve` retains unclaimable dust that is permanently locked (no sweep path spends it; `transferToken` is owner-only and explicitly an emergency rescue, and the reserve accounting would be inconsistent anyway).
- If `prefundedReserve` dominates (phantom "already-held" funds added to `reserveAmount`), `recoveryPrice` exceeds real backing: early `_claimDefaultedInstantWithdrawRequest`/`_claimPostDefaultWithdrawRequest` calls overpay, and the last claimant's `_transferDefaultRecovery` either underflows on `defaultRecoveryReserve -= _amount` (`IdleCreditVault.sol:915`) or reverts in `safeTransfer` — a permanent freeze of that claimant's recovery funds.

This mirrors the external report exactly: a claim/withdraw action fails to decrement a tracked aggregate, so the system believes more backing exists than it holds, and the final liquidations/claims fail.

### Likelihood Explanation
Requirements are modest and need no privileged misbehavior:

1. An unprivileged lender (KYC-passing via `isWalletAllowed` gating on the CDO) calls `requestInstantWithdraw` in epoch N.
2. The CDO funds it (`collectInstantWithdrawFunds` from available cash during the epoch — the prefunded-instant path is explicitly contemplated by the comments at `IdleCreditVault.sol:636-640`) and the user claims at par. The stale basis `C` remains in `instantWithdrawClaimsByEpoch[N]`.
3. Any other instant or normal request remains unfunded (`pendingInstantWithdraws != 0`) or pending when the borrower defaults within the same epoch N (a default can occur at any `stopEpoch`/`onStopEpoch` failure inside that epoch).
4. `finalizeDefaultRecovery` runs, mispriced as above.

The attacker does not even need to act adversarially — ordinary instant-withdraw usage in the defaulting epoch creates the stale basis; an attacker can deliberately sequence step 1–2 to maximize the mispricing. Honest borrower/manager defaults provide the trigger. No existing guard stops it: `_transferFundedClaim`'s reserve check only protects the *recovery* reserve from funded claims, not the converse; nothing validates `instantWithdrawClaimsByEpoch` against outstanding receipts.

### Recommendation
In `claimInstantWithdrawRequest`, clear the per-epoch basis alongside the aggregate receipt:

```solidity
uint256 reqEpoch = epochNumber; // or track per-user request epoch like lastWithdrawRequest
instantWithdrawsRequestsByEpoch[_user][reqEpoch] -= amount;
instantWithdrawClaimsByEpoch[reqEpoch] -= amount;
```

Since instant receipts can span multiple epochs, store the request epoch per user (analogous to `lastWithdrawRequest`) and decrement the correct epoch buckets; alternatively make `_defaultPrefundedInstantReserve`/`defaultPendingClaimBasis` recompute outstanding basis from live per-user state rather than the cumulative `instantWithdrawClaimsByEpoch` counter.

### Proof of Concept
Foundry fork skeleton (mainnet fork of an `IdleCDOEpochVariant` credit vault, e.g. a Pareto/Clearpool deployment):

```solidity
function test_StaleInstantBasisDilutesRecovery() public {
    // 1. Whitelisted lender deposits AA during buffer, epoch N starts.
    vm.prank(lender);
    idleCDO.depositAA(DEPOSIT);
    vm.prank(manager);
    idleCDO.startEpoch(DURATION, APR);

    // 2. Lender requests instant withdraw of X; CDO's getInstantWithdrawFunds
    //    pulls X from unlent cash -> strategy.collectInstantWithdrawFunds(X).
    vm.prank(lender);
    idleCDO.withdrawAA(X); // routes to requestInstantWithdraw in instant mode
    // fund + claim at par in the SAME epoch N
    // ... trigger the CDO instant-funding path, then:
    idleCDO.claimInstantWithdrawRequest(lender); // pays X, burns receipt
    // BUG: instantWithdrawClaimsByEpoch[N] still == X
    assertEq(strategy.instantWithdrawClaimsByEpoch(strategy.epochNumber()), X);

    // 3. Second lender requests instant withdraw of Y in epoch N, unfunded.
    vm.prank(lender2);
    idleCDO.requestInstantWithdraw(Y); // pendingInstantWithdraws = Y

    // 4. Borrower fails onStopEpoch -> default, owner finalizes recovery.
    //    basis = pendingWithdraws + (X + Y)  [X is ghost]
    //    prefundedReserve = (X + Y) - Y = X  [phantom, X was already paid out]
    // ... borrower default simulated per existing default tests ...
    // vm.prank(owner); idleCDO.finalizeDefault(...);

    // 5. recoveryPrice is computed as if X extra underlying sat in the
    //    strategy. Claiming sequence:
    idleCDO.claimInstantWithdrawRequest(lender2); // over/under-paid vs real reserve
    // last recovery claim reverts or leaves `defaultRecoveryReserve` dust:
    // assertEq(reserve leftover, ~X * recoveryPrice / 1e18) or expectRevert on final claim
}
```

Uncertainty note: I verified the missing decrement and the double use of `instantWithdrawClaimsByEpoch` in `IdleCreditVault.sol`, but did not fully trace the `IdleCDOEpochVariant` instant-funding path (`getInstantWithdrawFunds`/`collectInstantWithdrawFunds` call sites) to confirm a funded instant claim can complete inside the same epoch that later defaults. If instant claims can only settle after `epochNumber` increments, the stale basis would sit under an old epoch and not inflate `defaultPendingClaimBasis` — in that case this reduces to a lesser accounting inconsistency and the finding should be downgraded.