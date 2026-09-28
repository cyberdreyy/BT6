### Title
Stale-epoch instant-withdraw receipts bypass the default haircut and permanently freeze/drain recovery funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` keys instant-withdraw claim basis to `instantWithdrawClaimsByEpoch[epochNumber]` — the epoch in which `requestInstantWithdraw` was called — but default finalization only looks up the **current** `epochNumber`. An instant receipt requested in epoch N that remains (partially) unfunded while the vault completes another epoch (epochNumber bumps on every `stopEpoch` deposit) becomes "stale": it is excluded from `defaultPendingClaimBasis()` and from `_defaultPrefundedInstantReserve()`, yet `claimInstantWithdrawRequest` still tries to pay it at par from `_transferFundedClaim`. The result is either a permanent revert that freezes the user's receipt, or a silent drain of the default-recovery reserve owed to other claimants.

### Finding Description
The analog to the GPAC NULL-pointer dereference is a *missing-entry dereference*: recovery accounting looks up a per-epoch bucket that does not exist for the claimant's actual request epoch, so the claim path hits an inconsistent (zero) state.

Relevant mechanics in `contracts/strategies/idle/IdleCreditVault.sol`:

- `requestInstantWithdraw` records the receipt under the **request-time** epoch: `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount; instantWithdrawClaimsByEpoch[currentEpoch] += _amount` (lines 367–372), and `pendingInstantWithdraws` can legitimately survive an epoch boundary — the test `testFinalizeDefaultHaircutsPendingInstantRedeems` shows a request staying partially unfunded across `startEpoch`.
- `epochNumber` is incremented on every deposit performed while an epoch is running, i.e., on each `stopEpoch` funding deposit (lines 607–610).
- At finalization, `defaultPendingClaimBasis()` only adds `instantWithdrawClaimsByEpoch[epochNumber]` — the *current* epoch (lines 644–649). `_defaultPrefundedInstantReserve()` uses the same key (lines 716–723). `defaultInstantWithdrawsFinalized` is set to `pendingInstantWithdraws != 0` (line 696), so the strategy *knows* unfunded instant claims exist, but prices none of the stale-epoch ones.
- Post-finalization, `claimInstantWithdrawRequest` → `_claimDefaultedInstantWithdrawRequest` clears only `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` (lines 842–856). A stale receipt returns `claimBasis == 0`, is skipped, then `amount = instantWithdrawsRequests[_user]` (the full stale receipt) is burned and paid via `_transferFundedClaim` (lines 387–392).
- `_transferFundedClaim` reverts with `NotAllowed` whenever `balance - defaultRecoveryReserve < _amount` (lines 899–905). Since the stale receipt's unfunded portion was never added to the reserve basis nor funded, the claim either:
  - reverts permanently — every `claimInstantWithdrawRequest` for that user reverts, permanently freezing even the legitimately prefunded portion of their receipt; or
  - if `balance - reserve` happens to cover it (e.g., other users' unclaimed funded cash), pays the stale receipt at par while `pendingInstantWithdraws` was already counted in `totalBasis` — wait, it was *not* counted — meaning reserve computed for other claimants is consumed, short-changing defaulted/post-default claimants pro-rata.

Broken invariant: every claim included in the finalized recovery must be haircut by `defaultRecoveryPrice`; and `defaultRecoveryReserve` must exactly back all haircut claims. A stale-epoch instant receipt satisfies neither.

### Impact Explanation
Two failure modes, both with direct fund impact and no privileged attacker:

1. **Permanent freeze**: when `balance - reserve < staleReceipt`, the user's `claimInstantWithdrawRequest` always reverts (`NotAllowed`), permanently freezing the funded portion of their receipt — there is no other claim path for instant receipts.
2. **Reserve theft**: when other cash covers the gap, the stale receipt is paid 1:1 from funds reserved for defaulted/post-default claimants, so later legitimate claimants' `_transferDefaultRecovery` underflows (`defaultRecoveryReserve -= _amount`) or their transfers fail for lack of balance — an effective theft of recovery proceeds proportional to the stale receipt size.

The loss is bounded by the stale instant receipt amount, which is user-controlled up to their deposited position.

### Likelihood Explanation
Requires a specific but entirely honest-manager sequence:

- Instant withdraws enabled (`setInstantWithdrawParams(..., true)`).
- Attacker (any KYC'd tranche holder) calls `requestWithdraw`/`requestInstantWithdraw` in epoch N.
- The epoch ends without the instant queue being fully collected (borrower under-funds instant liquidity but `stopEpoch` still succeeds for the normal book — the contract explicitly models partial prefunding via `pendingInstantWithdraws` and `_defaultPrefundedInstantReserve`, and tests show instant claims surviving `startEpoch`).
- A further epoch boundary passes (any successful `stopEpoch` deposit bumps `epochNumber`), then the borrower defaults and `finalizeDefault` runs.

Every step is an unprivileged or honest-actor action; no malicious privileged role is needed. The only caveat is whether the CDO permits a successful `stopEpoch` while `pendingInstantWithdraws > 0`; the contract's own handling of partially prefunded instant claims across epochs indicates this state is reachable.

### Recommendation
In `defaultPendingClaimBasis()` and `_defaultPrefundedInstantReserve()`, do not rely solely on `instantWithdrawClaimsByEpoch[epochNumber]`; account for all still-unfunded instant receipts, e.g.:

- Track an aggregate `pendingInstantClaims` counter (sum over all epochs) alongside the per-epoch map, and use it — not the current-epoch bucket — for basis and prefunded-reserve computation; or
- In `_claimDefaultedInstantWithdrawRequest`, iterate/aggregate all `instantWithdrawsRequestsByEpoch[_user][*]` entries that were never funded (or maintain a per-user unfunded basis) instead of only `defaultRecoveryEpoch`; or
- On `startEpoch`, roll `instantWithdrawClaimsByEpoch[oldEpoch]` into `instantWithdrawClaimsByEpoch[newEpoch]` for the still-unfunded remainder so the basis key always matches `epochNumber` at finalization.

Add a regression test: instant request in epoch N left partially unfunded, successful `stopEpoch`/`startEpoch`, default in epoch N+1, `finalizeDefault`, then `claimInstantWithdrawRequest` — assert the receipt is haircut by `defaultRecoveryPrice` and does not consume other claimants' reserve.

### Proof of Concept
```solidity
// Foundry fork test skeleton (extend test/foundry/IdleCreditVault.t.sol)
function testStaleEpochInstantReceiptSkipsDefaultHaircut() external {
    uint256 amount = 10_000 * ONE_SCALE;
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(instantDelay, instantAprDelta, true);

    address attacker = makeAddr('stale-instant');
    address victim   = makeAddr('defaulted-pending');
    _depositWithUser(attacker, amount, true);
    _depositWithUser(victim,   amount, true);

    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());
    // epochNumber is now 1 (bumped by the stopEpoch deposit)

    // Attacker instant request recorded under epoch 1
    vm.prank(attacker);
    uint256 attackerReceipt = cdoEpoch.requestWithdraw(0, address(AAtranche));

    // Victim normal pending request, also epoch 1
    vm.prank(victim);
    cdoEpoch.requestWithdraw(0, address(AAtranche));

    _startEpochAndCheckPrices(1);
    // Manager collects only part of the instant queue -> pendingInstantWithdraws > 0
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    vm.prank(manager);
    cdoEpoch.getInstantWithdrawFunds(); // partial funding

    // Epoch 1 ends WITHOUT default; epochNumber bumps to 2 on the stopEpoch deposit.
    _stopEpochAndCheckPrices(1, initialProvidedApr, _expectedFundsEndEpoch());

    // Epoch 2 runs, borrower defaults
    _startEpochAndCheckPrices(2);
    deal(defaultUnderlying, borrower, 0);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);
    _checkDefault();

    IdleCreditVault cv = IdleCreditVault(address(strategy));
    uint256 activeBasis = cdoEpoch.getContractValue()
        + cdoEpoch.expectedEpochInterest() - cdoEpoch.pendingWithdrawFees();
    uint256 recovered = (activeBasis + cv.defaultPendingClaimBasis()) * 7e17 / ONE_TRANCHE;
    deal(defaultUnderlying, manager, recovered);
    vm.startPrank(manager);
    IERC20Detailed(defaultUnderlying).approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();

    // BUG: attacker's receipt lives in epoch 1, defaultRecoveryEpoch == 2.
    // _claimDefaultedInstantWithdrawRequest skips it; the claim then either
    // pays it at par (draining reserve that victim claims against) or reverts
    // permanently in _transferFundedClaim.
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest(); // over-pays attacker OR reverts NotAllowed

    vm.prank(victim);
    cdoEpoch.claimWithdrawRequest(); // underpaid or reverts: reserve was consumed
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L366-375)
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
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-610)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-906)
```text
  function _transferFundedClaim(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    uint256 reserve = defaultRecoveryReserve;
    if (reserve != 0) {
      uint256 balance = underlyingToken.balanceOf(address(this));
      // This should be unreachable when accounting is consistent. Keep the guard so old funded
      // receipts can never spend underlyings reserved for default recovery claimants.
      if (balance < reserve || balance - reserve < _amount) revert NotAllowed();
    }
    underlyingToken.safeTransfer(_user, _amount);
```

**File:** test/foundry/IdleCreditVault.t.sol (L4453-4456)
```text
    _startEpochAndCheckPrices(1);
    uint256 pendingInstant = creditVault.pendingInstantWithdraws();
    assertLt(pendingInstant, instantClaimBasis, 'instant request should be partially prefunded before default');
    uint256 prefundedInstant = instantClaimBasis - pendingInstant;
```
