### Title
Funded instant-withdraw receipts leave stale per-epoch claim data that is re-counted as default recovery basis, diluting `defaultRecoveryPrice` and permanently stranding recovery funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Analogous to the Eventlet trailer-smuggling bug (trailing bytes silently dropped by the parser but reinterpreted downstream), `IdleCreditVault.claimInstantWithdrawRequest` clears only the aggregate receipt (`instantWithdrawsRequests`) and never clears the per-epoch "trailer" accounting `instantWithdrawsRequestsByEpoch` / `instantWithdrawClaimsByEpoch`. When a borrower default is later finalized in that same epoch, `defaultPendingClaimBasis` re-reads those stale entries and counts already-paid receipts as defaulted claim basis. The recovery price is therefore computed over an inflated denominator, every honest defaulted-epoch claimant (and the tranche recovery NAV) is underpaid, and the excess `defaultRecoveryReserve` is permanently stranded because the stale claim can never be executed (it underflows on `instantWithdrawsRequests[_user] -= claimBasis`).

### Finding Description
`requestInstantWithdraw` records three pieces of state per request: [1](#0-0) 

The normal funded claim path only clears the aggregate: [2](#0-1) 

`instantWithdrawsRequestsByEpoch[_user][currentEpoch]` and `instantWithdrawClaimsByEpoch[currentEpoch]` are left populated. They are only ever decremented inside `_claimDefaultedInstantWithdrawRequest`, which is exclusively reachable after `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized`: [3](#0-2) 

At default finalization, the claim basis used to derive `defaultRecoveryPrice` adds the epoch's instant claims whenever an unfunded remainder exists: [4](#0-3) 

So an attacker who already claimed an instant withdrawal in full still contributes `X` to `instantWithdrawClaimsByEpoch[epochNumber]`. If any other instant request remains unfunded (`pendingInstantWithdraws != 0`), the finalized basis is `realBasis + X`. `defaultRecoveryPrice = recovered / basis` is set once at `finalizeDefault` and cannot be corrected afterward.

Sequence (epoch N, instant-withdraw mode, borrower is honest-but-insolvent at stop):

1. Attacker calls `requestInstantWithdraw(X)` → receipt minted, `instantWithdrawsRequestsByEpoch[attacker][N] = X`, `instantWithdrawClaimsByEpoch[N] = X`, `pendingInstantWithdraws = X`.
2. IdleCDO funds the queue via `collectInstantWithdrawFunds(X)` → `pendingInstantWithdraws = 0`, vault holds `X`.
3. Attacker calls `claimInstantWithdrawRequest` → aggregate zeroed, `X` paid out. By-epoch entries still `X`.
4. Victim requests instant withdraw `Y` in the same epoch N → `pendingInstantWithdraws = Y`, `instantWithdrawClaimsByEpoch[N] = X + Y`.
5. Epoch ends; borrower's `stopEpoch` repayment fails → `_handleBorrowerDefault`. `epochNumber` is still N.
6. Honest manager calls `finalizeDefault(recovered, source)` → `defaultRecoveryEpoch = N`, `basis` includes `X + Y`, `defaultRecoveryPrice` is set with the phantom `X` in the denominator.
7. Victim claims `Y * price` — strictly less than the fair `Y * recovered / (realBasis)`. The `X`-sized slice of `defaultRecoveryReserve` has no live receipt: the attacker's defaulted claim reverts (`instantWithdrawsRequests[attacker] = 0 < X`), so those tokens sit in the vault forever with no sweep path.

The same stale-trailer pattern exists on the normal-withdraw side: `_claimFundedWithdrawRequest` zeroes `withdrawsRequests` but not `withdrawsRequestsByEpoch`, though there `pendingWithdraws` accounting limits the blast radius; the instant path is exploitable because `pendingInstantWithdraws` is decremented at funding time while the per-epoch basis survives.

### Impact Explanation
Direct, quantified loss: every defaulted-epoch claimant and the active tranche recovery NAV lose `X / basis` of their rightful recovery, and `X` underlying tokens are permanently frozen in `IdleCreditVault.defaultRecoveryReserve` (no function releases unclaimed reserve). Loss is bounded only by the attacker's instant-withdraw size `X`, which is attacker-chosen and limited only by available instant liquidity. The attacker needs no privilege — any tranche holder can request instant withdrawals.

### Likelihood Explanation
Requires a pool with instant withdrawals enabled, one additional unfunded instant request (`pendingInstantWithdraws != 0`) at finalization, and a borrower default in the same epoch as the attacker's funded claim. Defaults are a designed-for state (the whole `defaultRecovery*` machinery exists for it), and the attacker can sandwich step 4 by monitoring the mempool or simply repeating across epochs; even a partial unfunded remainder of any size triggers the stale-basis inclusion. No existing guard stops it: the code explicitly assumes `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch` are only consumed by the default path, and `collectInstantWithdrawFunds`/`claimInstantWithdrawRequest` never touch them.

### Recommendation
In `claimInstantWithdrawRequest`, when paying a funded receipt, also clear the per-epoch entries for the epoch(s) being claimed: subtract the claimed amount from `instantWithdrawClaimsByEpoch[epoch]` and delete `instantWithdrawsRequestsByEpoch[_user][epoch]`. Since a funded claim proves the receipt was already cash-backed, its basis must never reach `defaultPendingClaimBasis`. Alternatively, derive the default instant basis from `pendingInstantWithdraws` (the actually-unfunded remainder) rather than the cumulative per-epoch claims map. Add a regression test: funded instant claim → second unfunded request → default → `finalizeDefault` → assert `defaultPendingClaimBasis` excludes the paid receipt and reserve conservation (`payouts + reserve == recovered`).

### Proof of Concept
Reproducible Foundry fork PoC sketch against `IdleCDOEpochVariant` + `IdleCreditVault` (instant-withdraw enabled deployment, e.g. mainnet fork of an existing credit vault):

```solidity
function testStaleInstantReceiptInflatesDefaultBasis() public {
    // epoch N running, instant withdrawals enabled
    uint256 X = 1000e6; // attacker instant amount
    uint256 Y = 1000e6; // victim instant amount

    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(X, address(AAtranche)); // or pool-specific entry

    // CDO funds instant queue during epoch
    // (borrower/manager path: getInstantWithdrawFunds -> collectInstantWithdrawFunds)
    fundInstantQueue(X);

    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest(); // attacker fully paid X

    IdleCreditVault vault = IdleCreditVault(address(strategy));
    // stale trailer still present
    assertEq(vault.instantWithdrawsRequestsByEpoch(attacker, vault.epochNumber()), X);

    vm.prank(victim);
    cdoEpoch.requestInstantWithdraw(Y, address(AAtranche)); // remains unfunded

    // epoch ends, borrower fails repayment -> default
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0); // triggers _handleBorrowerDefault

    uint256 realBasis = vault.defaultPendingClaimBasis();
    // BUG: realBasis includes attacker's already-paid X
    assertGt(realBasis, Y, "stale funded receipt counted as default basis");

    // finalize with full real recovery
    deal(underlying, manager, recovered);
    vm.prank(manager);
    cdoEpoch.finalizeDefault(recovered, manager);

    // victim is underpaid relative to fair share, and X of reserve is stranded:
    // attacker's defaulted claim path reverts (underflow), reserve has no sweep.
    vm.prank(victim);
    cdoEpoch.claimInstantWithdrawRequest();
    assertLt(victimPayout, Y * recovered / realBasisWithoutStale);
    assertGt(vault.defaultRecoveryReserve(), strandedX);
}
```

Caveat: exact helper names (`requestInstantWithdraw` signature on the CDO side, `fundInstantQueue`) should be matched to the deployed pool's instant-withdraw API in `test/foundry/IdleCreditVault.t.sol`, which already contains default-finalization harnesses (`_checkDefault`, `finalizeDefault(recovered, manager)`) to reuse.

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
