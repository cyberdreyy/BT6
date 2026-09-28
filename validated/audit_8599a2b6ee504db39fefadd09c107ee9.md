### Title
Already-claimed instant-withdraw receipts leave stale `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch` entries, corrupting default recovery accounting and permanently freezing defaulted-epoch claims - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`claimInstantWithdrawRequest` pays a funded instant receipt but never clears the per-epoch bookkeeping (`instantWithdrawsRequestsByEpoch[user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]`). If the borrower defaults in the same epochNumber and recovery is finalized, those stale entries are treated as unfunded defaulted receipts: they inflate `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve`, and they make `_claimDefaultedInstantWithdrawRequest` underflow on `instantWithdrawsRequests[_user] -= claimBasis` (the aggregate was already zeroed), reverting the entire claim path. The result is a diluted/mispriced `defaultRecoveryPrice`, phantom reserve backing, and permanent freezing of recovery claims — the closest analog of the CVE's stale-state/injection-after-use bug class, applied to the double-claim/stale-epoch withdrawal surface.

### Finding Description
In `requestInstantWithdraw`, the vault records the receipt three ways: `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, and `instantWithdrawClaimsByEpoch[currentEpoch]` [1](#0-0) . On a normal funded claim, only the aggregate is cleared — the per-epoch entries persist forever [2](#0-1) . Cleanup of the per-epoch entries exists only inside `_claimDefaultedInstantWithdrawRequest` [3](#0-2) .

Attack sequence (running epoch, instant mode enabled, unprivileged KYC'd lender):

1. Buffer of epoch N: attacker calls `requestInstantWithdraw` via the CDO. Receipt recorded under epoch N.
2. `startEpoch` funds the instant queue; `allowInstantWithdraw` becomes true [4](#0-3) .
3. Attacker claims via `claimInstantWithdrawRequest` — receives underlying in full, but `instantWithdrawsRequestsByEpoch[attacker][N]` and `instantWithdrawClaimsByEpoch[N]` remain nonzero.
4. Borrower fails to repay (e.g., `getInstantWithdrawFunds`/`stopEpoch` pull fails) → `_handleBorrowerDefault` sets `defaulted` while `epochNumber` is still N [5](#0-4) .
5. Manager finalizes recovery. `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[N]`, which still includes the already-paid claim [6](#0-5) . `_defaultPrefundedInstantReserve` computes `instantBasis - pendingInstant`, counting the paid-out funds as reserve [7](#0-6) .
6. Attacker (or any victim with a stale entry) calls `claimInstantWithdrawRequest`; `_claimDefaultedInstantWithdrawRequest` computes `claimBasis > 0` from the stale entry, then `instantWithdrawsRequests[_user] -= claimBasis` underflows (aggregate is 0) → revert [8](#0-7) .

Broken invariant: one receipt, one payout / consistent recovery accounting.

### Impact Explanation
- **Insolvency of the recovery reserve**: `reserveAmount` includes phantom prefunded amounts no longer held by the strategy, so `defaultRecoveryReserve` exceeds the real token balance; `defaultRecoveryPrice` is computed against an inflated basis. Late defaulted-epoch claimants' `_transferDefaultRecovery` either pays less than intended or reverts on insufficient balance — direct loss to honest claimants [9](#0-8) .
- **Permanent freezing**: any user whose already-paid instant receipt sits in the default epoch can never execute `claimInstantWithdrawRequest` again — the underflow reverts before their legitimately funded remainder is paid [10](#0-9) .

### Likelihood Explanation
Requires only that a user claims an instant withdrawal and the pool defaults within the same `epochNumber` before it increments — a normal sequence (instant requests are a designed feature; defaults are a designed flow). The attacker needs no privileged role; funding timing is driven by honest manager calls (`startEpoch`, `getInstantWithdrawFunds`, `finalizeDefaultRecovery`). Existing guards do not help: `_ensureDefaultRecoveryInitialized` passes (instant entries are per-epoch, not `pendingInstantWithdraws` once funded), and `_transferFundedClaim`'s reserve guard does not prevent the underflow in the defaulted path.

### Recommendation
In `claimInstantWithdrawRequest`, clear the request-epoch entries alongside the aggregate: iterate the user's known request epochs (or store the latest request epoch per user, like `lastWithdrawRequest`) and zero `instantWithdrawsRequestsByEpoch[_user][epoch]` and decrement `instantWithdrawClaimsByEpoch[epoch]`, so paid receipts can never be counted again in `defaultPendingClaimBasis`/`_defaultPrefundedInstantReserve` or replayed through `_claimDefaultedInstantWithdrawRequest`.

### Proof of Concept
Foundry fork-style PoC outline (mirroring `test/foundry/IdleCreditVault.t.sol` harness helpers `_depositWithUser`, `_stopCurrentEpoch`):

```solidity
function testStaleInstantReceiptPoisonsDefaultRecovery() external {
    // epoch 0 stopped; instant withdraws enabled for epoch 1 buffer
    _stopCurrentEpochWithApr(10e18);
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(100, 1e18, false);

    uint256 amount = 100e6;
    address attacker = makeAddr('attacker');
    address victim   = makeAddr('victim');
    _depositWithUser(attacker, amount);
    _depositWithUser(victim, amount);

    // buffer: attacker + victim request instant withdrawals
    _requestInstantWithdrawWithUser(attacker);
    _requestInstantWithdrawWithUser(victim);
    uint256 epoch = strategy.epochNumber();

    // startEpoch funds the instant queue in full
    vm.prank(manager);
    cdoEpoch.startEpoch();

    // both claim -> paid at par, but per-epoch entries remain
    vm.prank(attacker); cdoEpoch.claimInstantWithdrawRequest();
    vm.prank(victim);   cdoEpoch.claimInstantWithdrawRequest();
    assertGt(strategy.instantWithdrawsRequestsByEpoch(attacker, epoch), 0, 'stale entry');

    // borrower defaults during same epochNumber (stopEpoch pull fails)
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0); // borrower did not approve -> default
    assertTrue(cdoEpoch.defaulted());

    // finalize recovery
    _finalizeDefaultRecovery(); // helper: deal + approve recovery source, call finalizeDefaultRecovery via CDO

    // attacker tries to claim again -> instantWithdrawsRequests underflow revert
    vm.expectRevert();
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();

    // basis inflated: defaultPendingClaimBasis included the already-paid instant claims
    // -> defaultRecoveryPrice mispriced / reserve undercollateralized for real claimants
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L380-392)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-648)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L685-699)
```text
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
    // Bring active CDO NAV to the same recovery ratio. IdleCDOEpochVariant then calls
    // _forceUpdateAccounting so tranche prices/virtualPrice expose the crystallized loss.
    uint256 activeFinalNAV = (activeBasis * recoveryPrice) / RECOVERY_FULL;
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

**File:** contracts/IdleCDOEpochVariant.sol (L279-292)
```text
    uint256 pendingInstant = _pendingInstant();
    uint256 totUnderlyings = _contractTokenBalance(token);
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();
    _strategy.collectInstantWithdrawFunds(pendingInstant > totUnderlyings ? totUnderlyings : pendingInstant);

    // if there are more requests than the current underlyings we simply send all underlyings
    // to the IdleCreditVault contract
    if (pendingInstant > totUnderlyings) {
      // if borrower is programmable, notify epoch start even if no funds were sent
      _startEpochProgrammableBorrower(_pendingWithdraws);
      return;
    }
    // allow instant withdraws right away without waiting for the deadline
    allowInstantWithdraw = true;
```

**File:** contracts/IdleCDOEpochVariant.sol (L501-505)
```text
    } catch {
      // if borrower defaults, prev instant withdraw requests can still be withdrawn
      // as were already fullfilled prior to the default (all funds already sent to the strategy)
      _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
    }
```
