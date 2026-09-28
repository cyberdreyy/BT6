### Title
Default recovery misses instant-withdraw claims recorded under a stale epoch key, letting unfunded receipts claim at par and drain the recovery reserve - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`requestInstantWithdraw` records per-epoch claim basis under `instantWithdrawClaimsByEpoch[epochNumber]` and per-user basis under `instantWithdrawsRequestsByEpoch[_user][epochNumber]`, where `epochNumber` is the value at request time. But `epochNumber` advances inside `deposit()` whenever the CDO deposits while `isEpochRunning()` — i.e., at the next `stopEpoch`. All default-recovery paths (`defaultPendingClaimBasis`, `_defaultPrefundedInstantReserve`, `_claimDefaultedInstantWithdrawRequest`) look claims up under the *current* `epochNumber`/`defaultRecoveryEpoch`. If an instant receipt created in epoch N survives (partially unfunded, or funded but unclaimed) into a default finalized after `epochNumber` has bumped to N+1, the exact-epoch lookups miss it — the same class as the upstream bug where a moving boundary (`PivotDestinationNumber`) stepped past a `==`-guarded counter mid-process.

### Finding Description
In `requestInstantWithdraw`, claims are keyed to the request-time epoch (`currentEpoch = epochNumber`). [1](#0-0) 

`epochNumber` increments in `deposit()` when called mid-epoch (i.e., during `stopEpoch`). [2](#0-1) 

At finalization, the basis and prefunded reserve are computed only from `instantWithdrawClaimsByEpoch[epochNumber]` — the *new* epoch — so receipts recorded under the previous epoch contribute neither claim basis nor prefunded reserve. [3](#0-2) [4](#0-3) 

`finalizeDefaultRecovery` stores `defaultRecoveryEpoch = epochNumber` and sets `defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0`. [5](#0-4) 

Then `claimInstantWithdrawRequest` calls `_claimDefaultedInstantWithdrawRequest`, which reads `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` — zero for epoch-N receipts — so nothing is haircut or cleared, and the function falls through to paying the *entire* aggregate `instantWithdrawsRequests[_user]` at par via `_transferFundedClaim`. [6](#0-5) [7](#0-6) 

Concrete sequence (attacker = ordinary tranche holder, all privileged actors honest):
1. Buffer of epoch N: attacker calls `IdleCDOEpochVariant.requestWithdraw` while the APR-drop condition routes it to `requestInstantWithdraw`. Receipt recorded under epoch N; `pendingInstantWithdraws += amount`.
2. Manager calls `startEpoch`. If contract underlyings cover only part of the instant queue, `collectInstantWithdrawFunds(totUnderlyings)` leaves `pendingInstantWithdraws > 0` (lines 279-289 of `IdleCDOEpochVariant.sol`), or the attacker simply doesn't claim a fully funded receipt.
3. Epoch N runs, manager calls `stopEpoch`; `deposit()` bumps `epochNumber` to N+1. `pendingInstantWithdraws`/`instantWithdrawsRequests` still hold epoch-N basis.
4. During epoch N+1 the borrower defaults; manager/owner finalize via `finalizeDefaultRecovery`. `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` read `instantWithdrawClaimsByEpoch[N+1]` — missing the epoch-N receipts — so `totalBasis` and `reserveAmount` are undercounted while `defaultInstantWithdrawsFinalized` is still true.
5. Attacker calls `claimInstantWithdrawRequest`. `_claimDefaultedInstantWithdrawRequest` finds zero basis at epoch N+1 and clears nothing; the code then burns/pays the full `instantWithdrawsRequests[_user]` at par, drawing strategy underlyings that include the (undercounted) `defaultRecoveryReserve`. The `_transferFundedClaim` guard only protects `balance - reserve`, and the reserve itself was computed too small — it does not isolate the missed receipts.

### Impact Explanation
Unfunded instant receipts from the pre-default epoch are paid at 100% instead of `defaultRecoveryPrice`, consuming underlyings that belong to the shared recovery reserve. Early claimants extract more than their haircut share; once the reserve/balance is exhausted, later defaulted claimants (normal pending withdraws via `_claimDefaultedWithdrawRequest`, post-default requests, other instant receipts) revert or receive nothing — direct theft plus permanent freezing of others' claims, quantified by `(1 - defaultRecoveryPrice) × missedInstantBasis` stolen and up to the full missed basis frozen for honest claimants.

### Likelihood Explanation
Requires (a) an APR drop large enough to trigger the instant-withdraw path during a buffer, and (b) a borrower default finalized after `epochNumber` advances while an instant receipt is still open — both reachable through ordinary manager/borrower flows with no privileged misbehavior. The attacker controls only the timing of their own `requestWithdraw`/`claimInstantWithdrawRequest` calls. Main uncertainty: whether the CDO default path always finalizes before `epochNumber` can bump with `pendingInstantWithdraws != 0`; if defaults can only be declared while `epochNumber` still equals the request epoch, the window closes. I could not fully trace `_handleBorrowerDefault`/`defaulted` timing in `IdleCDOEpochVariant.sol` within this pass, so the epoch-advance precondition should be confirmed in the PoC.

### Recommendation
Resolve instant-claim basis by the *request* epoch rather than the current `epochNumber` at finalization: e.g., have `defaultPendingClaimBasis`/`_defaultPrefundedInstantReserve` iterate or accumulate over all epochs with outstanding `instantWithdrawClaimsByEpoch` (or maintain a running aggregate of unfunded instant basis), and make `_claimDefaultedInstantWithdrawRequest` clear `instantWithdrawsRequestsByEpoch` entries for every epoch `<= defaultRecoveryEpoch`, not only the exact `defaultRecoveryEpoch` key — mirroring the upstream fix of widening an equality guard to `<=` against an advancing boundary.

### Proof of Concept
```solidity
// test/foundry/InstantClaimEpochBoundary.t.sol — fork test sketch
// Setup: standard IdleCDOEpochVariant + IdleCreditVault fork fixture (as in IdleCreditVault.t.sol)
function testDefaultMissesPriorEpochInstantClaims() external {
    // 1) Deposit AA+BB, start epoch 0, stop with APR drop so instant path arms.
    _depositWithUser(user, 100_000e6);
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr / 2, _expectedFundsEndEpoch());

    // 2) Buffer of epoch N: attacker requests instant withdraw (epochNumber == N).
    uint256 reqEpoch = strategy.epochNumber();
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(attackerAABalance, address(AAtranche));
    assertEq(strategy.instantWithdrawClaimsByEpoch(reqEpoch), attackerAmount);

    // 3) startEpoch: leave pendingInstantWithdraws > 0 (partial funding) OR
    //    simply let the attacker hold the funded receipt unclaimed.
    vm.prank(manager);
    cdoEpoch.startEpoch();

    // 4) Next stopEpoch bumps epochNumber -> N+1 (deposit() while isEpochRunning).
    _stopCurrentEpoch();
    assertEq(strategy.epochNumber(), reqEpoch + 1);
    assertGt(strategy.pendingInstantWithdraws() + strategy.instantWithdrawsRequests(attacker), 0);

    // 5) Borrower defaults; owner finalizes recovery.
    _checkDefault(); // sets defaulted, finalizeDefaultRecovery(...)
    // instantWithdrawClaimsByEpoch[N+1] == 0 -> basis/prefunded reserve undercounted
    assertEq(strategy.instantWithdrawClaimsByEpoch(strategy.defaultRecoveryEpoch()), 0);

    // 6) Attacker claims: defaulted-epoch clear finds 0 basis at N+1,
    //    then pays full instantWithdrawsRequests[attacker] at par from reserve.
    uint256 reservePre = strategy.defaultRecoveryReserve();
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    // attacker received amount (par) instead of amount * defaultRecoveryPrice / 1e18
    // reservePre - reservePost > haircut share -> honest claimants' reserve drained.
}
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-649)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L690-696)
```text
    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L842-856)
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
  }
```
