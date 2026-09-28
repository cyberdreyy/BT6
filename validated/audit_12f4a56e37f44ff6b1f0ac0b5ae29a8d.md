### Title
Stale-epoch instant-withdraw receipts escape the default recovery haircut and drain/overwrite the isolated recovery reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The kernel bug in `udf_find_entry` allocated a buffer sized for one length but `memcpy`'d a larger length past it — an allocation/write-size mismatch. `IdleCreditVault` has the same shape: `finalizeDefaultRecovery` sizes `defaultRecoveryReserve` only over the *current* epoch's instant-withdraw basis (`instantWithdrawClaimsByEpoch[epochNumber]`), while the payouts that consume that reserve are driven by the aggregate `instantWithdrawsRequests`/`pendingInstantWithdraws` counters, which accumulate across *all* epochs. An instant-withdraw receipt left partially unfunded in an older epoch is written outside the allocated recovery "buffer": it receives no haircut, is not added to the reserve, and is later paid at par (or reverts mid-claim) — the vault-side analog of a slab-out-of-bounds write corrupting a neighboring allocation (other claimants' reserve).

### Finding Description
Three accounting paths disagree about which epochs are in scope:

1. `requestInstantWithdraw` records receipts under the *current* `epochNumber` only, in both `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` and `instantWithdrawClaimsByEpoch[currentEpoch]`, while `instantWithdrawsRequests[_user]` and `pendingInstantWithdraws` are aggregates across epochs [1](#0-0) .
2. At default finalization, `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` — the *default epoch only* — whenever `pendingInstantWithdraws != 0` [2](#0-1) . Likewise `_defaultPrefundedInstantReserve` compares the *aggregate* `pendingInstantWithdraws` against only the *current-epoch* `instantBasis`, so an older unfunded instant receipt produces `prefundedReserve = 0` (since `instantBasis <= pendingInstant`) [3](#0-2) .
3. After finalization, `claimInstantWithdrawRequest` first clears only `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` at the recovery haircut, then pays whatever remains in the aggregate `instantWithdrawsRequests[_user]` **at par** via `_transferFundedClaim` [4](#0-3)  and [5](#0-4) .

The "buffer" (`defaultRecoveryReserve`) was sized at `reserveAmount = recovered + prefunded + defaultRecoveryReserve` over `totalBasis` that never included the stale-epoch instant receipt [6](#0-5) . Paying that receipt afterward writes past the allocation: `_transferDefaultRecovery`/`_transferFundedClaim` consume underlyings earmarked for other claimants, or underflow/revert once the balance is exhausted, freezing remaining claims [7](#0-6) .

Attack path (all unprivileged):
- Epoch N, buffer phase: attacker (KYC'd lender) calls `cdoEpoch.requestInstantWithdraw` for a large amount. The borrower/manager startEpoch transfers available cash; `collectInstantWithdrawFunds` funds only part of the instant queue (partial prefunding is an explicitly supported state per the comments at lines 636–640). Epoch N ends normally; the attacker's `instantWithdrawsRequestsByEpoch[attacker][N]` and `pendingInstantWithdraws` remainder persist.
- Epoch N+1: borrower defaults; `finalizeDefault`/`finalizeDefaultRecovery` run. `defaultPendingClaimBasis` includes only `instantWithdrawClaimsByEpoch[N+1]` — the attacker's epoch-N receipt is excluded from both `basis` (no haircut applied to it) and `prefundedReserve` (aggregate-vs-single-epoch comparison yields 0).
- After `defaultRecoveryFinalized`, attacker calls `claimInstantWithdrawRequest`: `_claimDefaultedInstantWithdrawRequest` reads epoch N+1 (0 for attacker), then the fallback pays the attacker's full epoch-N receipt **at par** from `underlyingToken.balanceOf(this)` — un-haircutted recovery funds, i.e., more than their pro-rata share.
- Every other defaulted claimant's `_transferDefaultRecovery` payout is reduced by the same amount; the last claimants' calls revert when `defaultRecoveryReserve -= amount` underflows or `_transferFundedClaim`'s `balance - reserve < amount` guard trips — permanently freezing their claims.

### Impact Explanation
Direct theft / permanent freezing of recovery funds. The stale-epoch receipt is paid 1:1 while every other claimant is paid `claimBasis * defaultRecoveryPrice / RECOVERY_FULL`. The theft equals `receipt × (1 − recoveryPrice)` in excess payout, bounded by the reserve size; once the reserve is drained, remaining `claimWithdrawRequest`/`claimInstantWithdrawRequest` calls revert (arithmetic underflow in `defaultRecoveryReserve -= _amount`), permanently locking the unpaid claimants' recovery — no recovery mechanism exists after `defaultRecoveryFinalized` is set [8](#0-7) .

### Likelihood Explanation
Requires a partially unfunded instant-withdraw queue persisting across an epoch boundary, which the code explicitly contemplates ("partially prefunded instant requests", lines 683–684; "cash covered only part of the instant queue", lines 638–639), followed by a borrower default in a later epoch — a normal, honest-manager sequence. The attacker needs only to hold an unclaimed instant receipt from an earlier epoch, which any KYC-passing lender can obtain via `requestInstantWithdraw`. No privileged action beyond the honest manager's `stopEpoch`/`finalizeDefault` is needed.

### Recommendation
In `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve`, account for *all* outstanding instant receipts, not just `instantWithdrawClaimsByEpoch[epochNumber]`. Since per-epoch basis is needed for per-user haircut clearing, either (a) track a global `pendingInstantClaims` aggregate and use it for basis/reserve sizing while paying non-default-epoch instant receipts at the same `defaultRecoveryPrice` haircut, or (b) require instant receipts from prior epochs to be funded or haircutted before epoch rollover. At minimum, in `claimInstantWithdrawRequest` after finalization, apply `defaultRecoveryPrice` to any remaining `instantWithdrawsRequests[_user]` instead of paying the un-reserved remainder at par, and decrement `defaultRecoveryReserve` consistently so the sum of all instant claims never exceeds the allocated reserve.

### Proof of Concept
Foundry fork PoC outline (against `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
// Setup: deposit as victim (AA) and attacker; epoch 0 running.
// 1. Attacker: cdoEpoch.requestInstantWithdraw(attackerAmt) in epoch 0.
//    Manager startEpoch only partially funds instant queue
//    (collectInstantWithdrawFunds < pendingInstantWithdraws) -> pending remainder persists.
// 2. Warp, stopEpoch(0) -> epoch 1. Attacker does NOT claim.
//    assert(instantWithdrawsRequestsByEpoch[attacker][0] > 0 && pendingInstantWithdraws > 0);
// 3. Epoch 1: borrower defaults -> stopEpoch(1, 0) -> _checkDefault() -> defaulted == true.
// 4. Manager finalizeDefault(recovered, manager) with recovered = basis * 0.5.
//    assert(defaultPendingClaimBasis excludes attacker's epoch-0 instant receipt):
//    basis == pendingWithdraws + instantWithdrawClaimsByEpoch[epochNumber]
//    where instantWithdrawClaimsByEpoch[0] > 0 is omitted.
// 5. Attacker: cdoEpoch.claimInstantWithdrawRequest();
//    attacker receives full attackerAmt (par), NOT attackerAmt * defaultRecoveryPrice.
// 6. Victim with a pending/defaulted receipt: cdoEpoch.claimWithdrawRequest()
//    receives less than claimBasis * defaultRecoveryPrice / 1e18,
//    or reverts (underflow on defaultRecoveryReserve) once reserve is drained.
```

Key assertions: `creditVault.instantWithdrawsRequestsByEpoch(attacker, 0) > 0` survives finalization; attacker's payout `== attackerAmt` (un-haircutted); victim's payout `< claimBasis * defaultRecoveryPrice / 1e18` or the claim reverts — demonstrating the payout written outside the allocated recovery reserve.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-649)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L685-696)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-917)
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
  }

  /// @notice Transfer default recovery reserve to a user.
  /// @param _user claim receiver
  /// @param _amount amount to transfer
  function _transferDefaultRecovery(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    // Every defaulted or post-default claim consumes the isolated recovery reserve.
    defaultRecoveryReserve -= _amount;
    underlyingToken.safeTransfer(_user, _amount);
  }
```
