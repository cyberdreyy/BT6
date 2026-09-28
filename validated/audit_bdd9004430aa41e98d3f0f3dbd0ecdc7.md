### Title
Instant-withdraw receipts from non-current epochs are excluded from default recovery basis, inflating `defaultRecoveryPrice` and letting earlier-epoch receipts claim at par - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
Analog of CVE-2024-7255 (out-of-bounds read): `defaultPendingClaimBasis()` performs a single-slot read of `instantWithdrawClaimsByEpoch[epochNumber]`, i.e. it only sees instant-withdraw receipts indexed under the *current* epoch. Instant receipts recorded under earlier epoch keys (`instantWithdrawsRequestsByEpoch[user][pastEpoch]`) that were never fully funded are invisible to the recovery-basis calculation. The recovery price is therefore computed over a too-small basis (`reserveAmount * 1e18 / totalBasis`), is inflated, and the orphaned receipts remain payable at full par afterward.

### Finding Description
- `requestInstantWithdraw` buckets each receipt under the *current* `epochNumber`: `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount; instantWithdrawClaimsByEpoch[currentEpoch] += _amount` and increments the global `pendingInstantWithdraws` [1](#0-0) .
- `collectInstantWithdrawFunds` only decrements `pendingInstantWithdraws` by what was actually transferred; partial funding leaves a nonzero remainder while the per-epoch buckets stay under the *old* epoch key [2](#0-1) . The code comments explicitly acknowledge this scenario: "startEpoch moved the CDO's available cash to the strategy but that cash covered only part of the instant queue" [3](#0-2) .
- `epochNumber` increments on `deposit` while an epoch is running [4](#0-3) , so a partially unfunded instant receipt from epoch N is still keyed under N when default is finalized in epoch N+1.
- `defaultPendingClaimBasis` then reads only `instantWithdrawClaimsByEpoch[epochNumber]` (N+1), missing the epoch-N basis entirely [5](#0-4) , so `totalBasis` is understated and `defaultRecoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` is too high [6](#0-5) .
- After finalization, `claimInstantWithdrawRequest` clears only `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` at the recovery haircut; the leftover `instantWithdrawsRequests[_user]` balance (which still includes the epoch-N receipt) is paid in full via `_transferFundedClaim` [7](#0-6)  — i.e. an unfunded receipt escapes both the recovery haircut and the prefunded-reserve accounting.

### Impact Explanation
Two compounding effects:
1. **Inflated recovery price**: excluding earlier-epoch instant basis from `totalBasis` raises `defaultRecoveryPrice` for every defaulted claim. Early claimers (`_claimDefaultedInstantWithdrawRequest`, `_claimDefaultedWithdrawRequest`) draw more than their fair share from `defaultRecoveryReserve`, leaving later claimers and active tranche holders underpaid or unpaid — direct redistribution/theft of recovery funds.
2. **Par payout of unfunded receipts**: the epoch-N instant receipt was never funded (it is part of the nonzero `pendingInstantWithdraws` remainder) yet is paid at 100% via `_transferFundedClaim`, spending underlying that backs active tranche positions. Loss bounded by the orphaned instant basis, but it is a direct solvency break with no privileged action required.

### Likelihood Explanation
Requires: instant withdrawals enabled, an instant request only partially funded at an epoch boundary (acknowledged possible by the prefunded-reserve logic), `epochNumber` advancing before the receipt is claimed, and a subsequent borrower default with `pendingInstantWithdraws != 0` at finalization. All steps use only unprivileged or honest-role actions (lender request, honest manager start/stop, honest default finalization). Likelihood is moderate: instant-withdraw partial funding plus a default is an unusual but reachable combination, and no guard (`_ensureDefaultRecoveryInitialized`, epoch gating, reserve checks) prevents it — the bug is the single-index read itself.

### Recommendation
Track unfunded instant basis across all epochs rather than only `epochNumber`. Options:
- At `finalizeDefaultRecovery`, use the aggregate `instantWithdrawsRequests`-style total for unfunded instant basis (e.g. a global `instantWithdrawClaimsTotal`), or
- When `pendingInstantWithdraws != 0`, fold stale epoch buckets forward (carry `instantWithdrawClaimsByEpoch[N]` into the new epoch at epoch rollover / first new request), and iterate/sum all nonzero epoch buckets in `defaultPendingClaimBasis`, and
- In `claimInstantWithdrawRequest`, apply `defaultRecoveryPrice` to *every* receipt epoch that was unfunded at finalization, not just `defaultRecoveryEpoch`.

### Proof of Concept
Foundry fork sketch (extends `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function testStaleEpochInstantReceiptEscapesRecovery() external {
    _useStandardEpochVariant();
    _stopCurrentEpochWithApr(10e18);            // enter epoch #1 buffer

    // enable instant withdrawals
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(100, 1e18, false);

    address attacker = makeAddr('attacker');
    uint256 tranches = _depositWithUser(attacker, 100e6);

    vm.prank(manager);
    cdoEpoch.startEpoch();                      // epoch #1 running

    // attacker requests instant withdraw in epoch N (=1)
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(tranches, address(AAtranche));

    IdleCreditVault vault = IdleCreditVault(address(strategy));
    uint256 epochN = vault.epochNumber();
    assertGt(vault.instantWithdrawClaimsByEpoch(epochN), 0);

    // CDO only partially collects instant funds -> pendingInstantWithdraws stays > 0
    // (simulate by funding only part of the instant queue at stopEpoch)
    // ... borrower repays principal but not enough to cover instant queue ...

    // epoch rolls: deposits during running epoch bump epochNumber to N+1
    // while the attacker receipt remains keyed under epoch N
    _startEpochAndCheckPrices(epochN + 1);

    // borrower defaults in epoch N+1; manager finalizes recovery
    deal(defaultUnderlying, borrower, 0, true);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(cdoEpoch.owner());
    cdoEpoch.stopEpoch(0, 0);
    assertTrue(cdoEpoch.defaulted());

    uint256 basis = vault.defaultPendingClaimBasis();
    // BUG: basis excludes instantWithdrawClaimsByEpoch[epochN] even though
    // pendingInstantWithdraws != 0 counts that receipt as unfunded
    // => defaultRecoveryPrice is inflated

    // finalize recovery with honest manager
    // cdoEpoch.finalizeDefault(recovered, manager);

    // attacker claims: epoch-N receipt is not cleared by
    // _claimDefaultedInstantWithdrawRequest (which only reads defaultRecoveryEpoch)
    // and is paid at PAR via _transferFundedClaim despite being unfunded
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    // assert attacker received ~100% while defaulted claims got only defaultRecoveryPrice
}
```

Key assertion: `vault.defaultPendingClaimBasis()` after the epoch rollover omits `instantWithdrawClaimsByEpoch[epochN]` while `pendingInstantWithdraws` still includes the attacker's receipt, so `defaultRecoveryPrice` exceeds the economically correct value and the stale-epoch receipt is paid at par.

Caveat: the exact funding path used by `IdleCDOEpochVariant` to leave `pendingInstantWithdraws` partially collected across `startEpoch` was inferred from the in-code comments describing partial instant-queue funding; the PoC wiring of that step should be confirmed against `getInstantWithdrawFunds`/`collectInstantWithdrawFunds` call sites in `contracts/IdleCDOEpochVariant.sol`.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L367-374)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L636-640)
```text
  /// @dev Normal pending withdraws are already tracked globally. Current-epoch instant receipts
  /// join recovery only if `pendingInstantWithdraws` is still non-zero at finalization. This can
  /// happen when startEpoch moved the CDO's available cash to the strategy but that cash covered
  /// only part of the instant queue. The full current-epoch instant claim is included as basis,
  /// while the already-funded part is added to the reserve by `_defaultPrefundedInstantReserve()`.
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
