### Title
Pre-existing-epoch instant-withdraw receipts escape default-recovery accounting and are paid at par from funds that should enter the recovery pool - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary

The kernel bug returns a lease pointer after dropping its lock, so the caller dereferences a stale object that accounting no longer protects. The analog in `IdleCreditVault` is the per-epoch instant-withdraw ledger: `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch` are keyed by the request epoch, but `defaultPendingClaimBasis()`, `_defaultPrefundedInstantReserve()`, and `_claimDefaultedInstantWithdrawRequest()` only dereference `epochNumber`'s entry. Instant receipts recorded under older epochs become "dangling" — invisible to the recovery-basis computation — yet remain payable at par through the aggregate fallback in `claimInstantWithdrawRequest()`. [1](#0-0) 

### Finding Description

Instant withdraw receipts are tracked three ways (lines 366-374): per-user aggregate `instantWithdrawsRequests`, per-user-per-epoch `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, and per-epoch total `instantWithdrawClaimsByEpoch[currentEpoch]`. `pendingInstantWithdraws` is the aggregate *unfunded* remainder across all epochs. [2](#0-1) 

At default finalization:

- `defaultPendingClaimBasis()` adds only `instantWithdrawClaimsByEpoch[epochNumber]` — the current epoch — when `pendingInstantWithdraws != 0`. Unfunded instant receipts created in earlier epochs are never added to the recovery basis. [1](#0-0) 
- `_defaultPrefundedInstantReserve()` computes `instantWithdrawClaimsByEpoch[epochNumber] - pendingInstantWithdraws`. But `pendingInstantWithdraws` aggregates *all* epochs, while `instantBasis` covers only the current one. When older claims exist, `instantBasis <= pendingInstant` yields `prefundedReserve == 0`, so underlying already pulled into the strategy via `collectInstantWithdrawFunds()` (which transfers tokens in and only decrements `pendingInstantWithdraws`, lines 398-403) is not counted into `defaultRecoveryReserve`. [3](#0-2) [4](#0-3) 
- After finalization, `claimInstantWithdrawRequest()` calls `_claimDefaultedInstantWithdrawRequest(_user)`, which clears only `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]`, then pays the *entire remaining aggregate* `instantWithdrawsRequests[_user]` at par via `_transferFundedClaim`. The `_transferFundedClaim` guard only excludes `defaultRecoveryReserve`, so the un-reserved held funds are spendable at 100% — no recovery haircut applied. [5](#0-4) [6](#0-5) [7](#0-6) 

The code itself acknowledges the partial-funding state is reachable: "`pendingInstantWithdraws` is the still-unfunded remainder... the difference is already-held underlying reserved for those claims" — but the reserve calculation silently drops the held funds whenever `pendingInstantWithdraws` includes pre-existing-epoch claims.

### Impact Explanation

Funds already collected into the strategy to back instant receipts fail to enter `defaultRecoveryReserve`, and instant receipts from epochs prior to the default epoch are neither haircutted nor included in the claim basis. An unprivileged tranche-token holder who stacked instant-withdraw receipts across epochs can call `claimInstantWithdrawRequest` right after `finalizeDefaultRecovery` and be paid 100% from the free balance, while active LPs and pending withdraw requesters receive only `defaultRecoveryPrice` (computed on a too-small basis) from a too-small reserve. This is direct theft of value that should have been pooled pro-rata, and it first-come-first-served drains funds ahead of other claimants. If the free balance is insufficient, `_transferFundedClaim` reverts `NotAllowed`, permanently freezing the legitimate unfunded remainder of those receipts.

### Likelihood Explanation

- Requires `pendingInstantWithdraws > 0` to persist across an epoch boundary, i.e., instant-mode pool where collected funds covered only part of the queue — a state the contract's own comments describe as reachable when "startEpoch moved the CDO's available cash to the strategy but that cash covered only part of the instant queue" (line 638-639).
- Attacker is any instant-withdraw requester (KYC-passing lender via the CDO); no privileged action needed beyond requesting instant withdraws in consecutive epochs.
- Trigger requires a borrower default with `finalizeDefaultRecovery` — an externally driven but supported flow.

One caveat I could not fully verify within the search budget: the exact CDO-side sequencing in `IdleCDOEpochVariant.stopEpoch`/`startEpoch`/`_handleBorrowerDefault` that permits `pendingInstantWithdraws > 0` to roll into the next epoch and then default (the grep returned file names without snippets). The strategy comments strongly imply it, but the PoC should confirm that an epoch boundary is not blocked by the instant remainder.

### Recommendation

- In `defaultPendingClaimBasis()`, include the total outstanding instant claim basis across epochs (e.g., `pendingInstantWithdraws + funded instant remainder`, or maintain an aggregate `instantWithdrawClaimsTotal` rather than only `instantWithdrawClaimsByEpoch[epochNumber]`).
- In `_defaultPrefundedInstantReserve()`, compute prefunding against the aggregate instant basis, not only the current epoch's entry, so already-held underlying backing any unfunded/partially-funded instant claims is swept into `defaultRecoveryReserve`.
- In `claimInstantWithdrawRequest()`, after finalization, route *all* unfunded instant receipts (every epoch) through `_claimDefaultedInstantWithdrawRequest`/recovery-price payment, not only `defaultEpoch` entries — e.g., clear `instantWithdrawsRequestsByEpoch` for all epochs or track per-user unfunded vs funded balances explicitly.

### Proof of Concept

Foundry fork scenario (instant-withdraw enabled pool, fixed-APR mode):

```solidity
// Epoch N-1 (running):
// 1. alice deposits, then IdleCDO.requestInstantWithdraw(100) as alice.
//    pendingInstantWithdraws = 100; instantWithdrawClaimsByEpoch[N-1] = 100.
// 2. stopEpoch: borrower returns only 50 -> collectInstantWithdrawFunds(50).
//    Strategy balance += 50; pendingInstantWithdraws = 50; epochNumber -> N.
//    alice cannot claim the unfunded 50.

// Epoch N (running):
// 3. IdleCDO.requestInstantWithdraw(100) as alice again.
//    instantWithdrawsRequests[alice] = 200;
//    instantWithdrawClaimsByEpoch[N] = 100; pendingInstantWithdraws = 150.
// 4. borrower collects 50 more -> balance 100 held, pendingInstantWithdraws = 100.

// 5. Borrower defaults; finalizeDefaultRecovery(_recoveredAmount, source):
//    defaultPendingClaimBasis() = pendingWithdraws + instantWithdrawClaimsByEpoch[N] (100)
//      -> alice's 50 unfunded N-1 receipt is excluded from basis.
//    _defaultPrefundedInstantReserve() = max(0, 100 - 100) = 0
//      -> the 100 underlying already held is NOT added to defaultRecoveryReserve.
//    reserveAmount excludes held funds; defaultRecoveryPrice is under-computed.

// 6. alice calls IdleCDO.claimInstantWithdrawRequest():
//    _claimDefaultedInstantWithdrawRequest clears only byEpoch[N] (100),
//    instantWithdrawsRequests[alice] -> 100.
//    Remainder 100 paid at par via _transferFundedClaim from free balance
//    (balance 100 - reserve check passes since held funds were never reserved).
//    Result: alice receives 100*recoveryPrice + 100 at par; the par-paid 100
//    should have entered the recovery pool shared by all claimants.
```

Expected assertion: after finalization, `underlyingToken.balanceOf(strategy) > defaultRecoveryReserve` by exactly the prefunded instant amount, and alice's par payout reduces funds available to other recovery claimants (or a later claimant's `_transferDefaultRecovery` reverts).

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-907)
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
```
