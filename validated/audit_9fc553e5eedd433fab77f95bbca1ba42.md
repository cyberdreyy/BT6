### Title
Stale per-epoch instant-withdraw records are never cleared on the funded-claim path, inflating `instantWithdrawClaimsByEpoch` and corrupting default-recovery pricing — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` zeroes the aggregate `instantWithdrawsRequests[_user]` but never clears `instantWithdrawsRequestsByEpoch[_user][epoch]` or decrements `instantWithdrawClaimsByEpoch[epoch]` [1](#0-0) . If the borrower later defaults in the same epoch while other instant requests remain unfunded, `finalizeDefaultRecovery` re-reads these stale records via `_defaultPrefundedInstantReserve()` and `defaultPendingClaimBasis()`, counting already-paid-out underlying as if it were still held reserve and already-settled receipts as live claims [2](#0-1) [3](#0-2) . This is the direct analog of CVE-2022-38861's use-after-free/double-free memory corruption in `free_mp_image()`: a receipt is "freed" (paid and zeroed in the aggregate) but its per-epoch bookkeeping remains live and is used again.

### Finding Description
In `IdleCreditVault`, instant-withdraw accounting tracks three structures:

- `instantWithdrawsRequests[_user]` — aggregate receipt balance
- `instantWithdrawsRequestsByEpoch[_user][epoch]` — per-epoch basis used for default claims
- `instantWithdrawClaimsByEpoch[epoch]` — epoch-level claim basis used at finalization [4](#0-3) 

On the funded path, `claimInstantWithdrawRequest` burns the receipt, zeroes only the aggregate, and pays at par via `_transferFundedClaim` [5](#0-4) . The per-epoch entries survive. At default, `finalizeDefaultRecovery` computes `prefundedReserve = instantBasis - pendingInstant` from `instantWithdrawClaimsByEpoch[epochNumber]` and adds it to `reserveAmount`, then sets `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` where `totalBasis` also includes the same stale instant claims [6](#0-5) . Since the funded claims already transferred underlying out of the contract, `prefundedReserve` counts tokens that no longer exist, inflating `defaultRecoveryPrice` beyond what the real balance supports.

Attack path (buffer/running epoch → defaulted → finalized):

1. Epoch N starts; `pendingInstantWithdraws` grows as users request instant withdraws.
2. Borrower (honest) partially funds instant claims via `getInstantWithdrawFunds`/`collectInstantWithdrawFunds`, leaving `pendingInstantWithdraws > 0` for other users [7](#0-6) .
3. Attacker (a KYC'd lender who requested an instant withdraw) calls `claimInstantWithdrawRequest` and is paid at par. His `instantWithdrawsRequestsByEpoch[N]` and the epoch's `instantWithdrawClaimsByEpoch[N]` remain set.
4. Borrower defaults (honest-manager sequence, e.g., failed `stopEpoch` repayment / `startEpoch` send failure); manager calls `finalizeDefault`. Because `pendingInstantWithdraws != 0`, `defaultInstantWithdrawsFinalized = true` and the stale claims inflate both `pendingBasis` and `prefundedReserve`.
5. `defaultRecoveryPrice` is set above the sustainable ratio. The attacker — or any early claimant, including via `_claimDefaultedInstantWithdrawRequest` (a second user who still has a live receipt) — redeems at the inflated price from `defaultRecoveryReserve` [8](#0-7) . Once the phantom portion is exhausted, `_transferDefaultRecovery`/`underlyingToken.safeTransfer` reverts for remaining claimants, and tranche prices crystallized via `_forceUpdateAccounting` embed the same inflated ratio for active LPs who exit first [9](#0-8) .

A secondary effect: for the already-paid attacker, `_claimDefaultedInstantWithdrawRequest` performs `instantWithdrawsRequests[_user] -= claimBasis` on a zeroed aggregate, causing an arithmetic underflow that permanently reverts `claimInstantWithdrawRequest` for that user.

### Impact Explanation
Direct theft and insolvency in the default-recovery waterfall. The phantom prefunded reserve raises `recoveryPrice` without corresponding tokens, so early claimants (the attacker, who can hold both a stale-claimed receipt position via active tranches and a legitimately pending receipt) extract more underlying than the reserve holds; later defaulted-epoch claimants and remaining LPs are either underpaid or permanently frozen when `safeTransfer`/`defaultRecoveryReserve -= _amount` underflows. Loss is quantified as the sum of funded instant claims executed in the defaulted epoch (bounded only by `instantWithdrawClaimsByEpoch[epochNumber]`), i.e., up to the full instant-withdraw volume of that epoch paid twice.

### Likelihood Explanation
Requires a specific but realistic sequence: instant withdraws partially funded during an epoch (the code explicitly supports partial funding via `pendingInstantWithdraws` remainder [10](#0-9) ), at least one funded claim, then a borrower default in the same epoch. All attacker steps are unprivileged user actions (request, claim); default and finalization are performed by the honest manager/borrower. No privileged malicious behavior is needed. The conditions are not exotic: partial instant-liquidity plus default is precisely the stressed scenario this code path was built for, and no existing guard (`_transferFundedClaim` reserve check, `defaultRecoveryFinalized` flag, `_onlyIdleCDO`) detects the stale per-epoch records.

### Recommendation
In `claimInstantWithdrawRequest`, clear the per-epoch records for every epoch contributing to the claimed aggregate — i.e., iterate/record the request epoch (e.g., store `lastInstantWithdrawRequest[_user]` or clear `instantWithdrawsRequestsByEpoch[_user][epochNumber]`-equivalent entries) and decrement `instantWithdrawClaimsByEpoch[epoch]` by the claimed amount, mirroring what `_claimDefaultedInstantWithdrawRequest` does [11](#0-10) . Alternatively, derive `instantWithdrawClaimsByEpoch` solely from still-live per-user records at finalization rather than trusting a cumulative counter that the funded path never decrements.

### Proof of Concept
Foundry fork PoC sketch on `test/foundry/IdleCreditVault.t.sol` harness:

```solidity
// 1. depositAA two users (attacker + victim), enable instant withdraws,
//    start epoch 0.
// 2. Both request instant withdraw in epoch 0.
//    Manager calls getInstantWithdrawFunds() collecting only enough to
//    cover the attacker's request -> pendingInstantWithdraws = victimAmt.
// 3. Attacker calls cdoEpoch.claimInstantWithdrawRequest() -> paid at par.
//    Assert: strategy.instantWithdrawsRequestsByEpoch(attacker, 0) == amt
//    (stale) and instantWithdrawClaimsByEpoch(0) unchanged.
// 4. Force default: warp past epochEndDate, stopEpoch(0,0) with borrower
//    underfunded -> defaulted == true, pendingInstantWithdraws > 0.
// 5. Manager finalizeDefault(recovered, source) with recovered < basis.
//    Observe defaultRecoveryPrice higher than
//    realReserve * RECOVERY_FULL / realBasis by the stale-funded amount.
// 6. First claimant calls claimInstantWithdrawRequest/claimWithdrawRequest
//    -> overpaid; subsequent claim reverts (reserve underflow) or pays
//    less, demonstrating double-spend of the freed receipt.
//    Additionally: attacker's own claimInstantWithdrawRequest reverts with
//    arithmetic underflow (stale per-epoch basis > zeroed aggregate).
```

Uncertainty note: I could not fully verify within available iterations every CDO-side gate (e.g., exact timing windows for `getInstantWithdrawFunds` vs default triggers), but the stale-record invariant violation in `claimInstantWithdrawRequest` versus the finalization readers is directly visible in the cited code.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-649)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L679-699)
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
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
    // Bring active CDO NAV to the same recovery ratio. IdleCDOEpochVariant then calls
    // _forceUpdateAccounting so tranche prices/virtualPrice expose the crystallized loss.
    uint256 activeFinalNAV = (activeBasis * recoveryPrice) / RECOVERY_FULL;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L714-723)
```text
  /// current-epoch claim basis, the difference is already-held underlying reserved for those claims.
  /// @return prefundedReserve amount of current instant claims already backed by strategy underlyings
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L912-917)
```text
  function _transferDefaultRecovery(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    // Every defaulted or post-default claim consumes the isolated recovery reserve.
    defaultRecoveryReserve -= _amount;
    underlyingToken.safeTransfer(_user, _amount);
  }
```
