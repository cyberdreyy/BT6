### Title
Stale `instantWithdrawClaimsByEpoch`/`instantWithdrawsRequestsByEpoch` entries are never cleared on funded instant claims, inflating `defaultPendingClaimBasis` and the prefunded reserve during `finalizeDefaultRecovery` — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The closest analog to CVE-2020-0444's "bad kfree" (a free of the wrong/stale object caused by a bookkeeping logic error) is in `IdleCreditVault.claimInstantWithdrawRequest`: when an instant-withdraw receipt is paid at par, the aggregate `instantWithdrawsRequests[_user]` is cleared but the per-epoch ledgers `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` are left populated. Those stale entries are later read by `defaultPendingClaimBasis()` and `_defaultPrefundedInstantReserve()` as if the receipt were still outstanding, corrupting default-recovery finalization.

### Finding Description
- `requestInstantWithdraw` records `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount` and `instantWithdrawClaimsByEpoch[currentEpoch] += _amount` [1](#0-0) .
- `claimInstantWithdrawRequest` pays `instantWithdrawsRequests[_user]` at par via `_transferFundedClaim`, zeroes only the aggregate, and **does not** decrement `instantWithdrawsRequestsByEpoch` or `instantWithdrawClaimsByEpoch` [2](#0-1) . `collectInstantWithdrawFunds` likewise only decrements `pendingInstantWithdraws` [3](#0-2) .
- On borrower default in that same epoch, `finalizeDefaultRecovery` computes `pendingBasis = defaultPendingClaimBasis()`, which adds the full `instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0` [4](#0-3) . `_defaultPrefundedInstantReserve` adds `instantBasis - pendingInstant` to the reserve, also counting already-paid receipts [5](#0-4) .
- The per-epoch entry is only cleared inside `_claimDefaultedInstantWithdrawRequest`, which is gated by `defaultInstantWithdrawsFinalized` and subtracts the stale `claimBasis` from `instantWithdrawsRequests[_user]` (underflow → revert) and burns `claimBasis` receipt tokens the user no longer holds (underflow → revert) [6](#0-5) .

### Impact Explanation
Two concrete harms in the `defaulted`/`finalized` phase:

1. `reserveAmount` is overstated by the already-paid instant amount `C` (those underlyings left the strategy at the par claim), while `totalBasis` is inflated by the same `C`. `defaultRecoveryReserve` is therefore set to more tokens than the contract holds; the last defaulted claimants' `_transferDefaultRecovery` calls revert on insufficient balance, permanently freezing part of the recovery reserve, and `defaultRecoveryPrice` is computed against a phantom reserve.
2. Any user whose stale `instantWithdrawsRequestsByEpoch[user][defaultEpoch]` entry exceeds their current `instantWithdrawsRequests[user]` (e.g., they claimed earlier in the epoch and hold no/smaller new receipt) has `claimInstantWithdrawRequest` permanently revert via subtraction/burn underflow, freezing their legitimately funded receipts.

An unprivileged lender or any EOA able to call `requestInstantWithdraw`+`claimInstantWithdrawRequest` in the epoch where the honest borrower defaults triggers this; no privileged role misbehaves.

### Likelihood Explanation
Requires a borrower default in the same epoch where instant withdrawals were both claimed (funded) and left pending (unfunded) — an ordinary, intended configuration (`pendingInstantWithdraws != 0` is exactly what sets `defaultInstantWithdrawsFinalized`). The missing ledger cleanup is unconditional, so the corruption is deterministic once that state occurs. Severity is bounded by the instant-withdraw volume in the default epoch, so impact is medium rather than critical.

### Recommendation
In `claimInstantWithdrawRequest` (and/or `collectInstantWithdrawFunds`), clear per-epoch accounting when paying funded receipts: track which epoch each user's funded instant receipt belongs to and decrement `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` by the paid amount, mirroring the cleanup in `_claimDefaultedInstantWithdrawRequest`.

### Proof of Concept
Foundry fork PoC sketch (against `test/foundry/IdleCreditVault.t.sol` harness, standard epoch variant with instant withdraws enabled):

```solidity
// epoch E running, instantDelay elapsed
// 1. attacker requests instant withdraw A and other users request U (U stays unfunded)
cdoEpoch.requestInstantWithdraw(A_amount, address(AAtranche)); // attacker
// manager funds the instant queue so attacker can claim, leaving U pending
// (getInstantWithdrawFunds -> strategy.collectInstantWithdrawFunds covers A only partially? fund A fully, U partially)
// 2. attacker claims at par -> instantWithdrawsRequests[attacker] = 0,
//    but instantWithdrawsRequestsByEpoch[attacker][E] and instantWithdrawClaimsByEpoch[E] keep A_amount
cdoEpoch.claimInstantWithdrawRequest();
// 3. borrower defaults during epoch E while pendingInstantWithdraws == U != 0
cdoEpoch._handleBorrowerDefault();           // via manager/borrower path
// 4. finalizeDefaultRecovery:
//    totalBasis includes A_amount (phantom), prefundedReserve includes A_amount (not held)
strategy.finalizeDefaultRecovery(recovered, recoverySource);
// assert: defaultRecoveryReserve > strategy's actual underlying balance by A_amount
// 5. other users' claims revert (insufficient balance) or attacker's claim reverts on underflow
vm.expectRevert();
cdoEpoch.claimInstantWithdrawRequest();      // for user holding funded receipt U
```

The asserted invariant violation is `defaultRecoveryReserve <= underlyingToken.balanceOf(strategy)` and "one receipt, one payout" — the same instant basis is counted once at the par claim and again inside the default recovery basis.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L387-392)
```text
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
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
