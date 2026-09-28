The bug class is "duplicate accounting for the same asset because a context check (`registeredMarketplaces[operator]`) fails to cover all paths into the state-creating code." The strongest analog in idle-tranches is `IdleCreditVault`: an instant-withdraw receipt is tracked in *two* places — aggregate `instantWithdrawsRequests[_user]` and per-epoch `instantWithdrawsRequestsByEpoch[_user][epoch]` — and the normal claim path clears only the aggregate, so the same receipt can be paid out again from the default recovery reserve.

### Title
Instant-withdraw receipts can be double-claimed through `instantWithdrawsRequestsByEpoch` after default finalization - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` pays and zeroes `instantWithdrawsRequests[_user]` but never clears `instantWithdrawsRequestsByEpoch[_user][epoch]` or `instantWithdrawClaimsByEpoch[epoch]` [1](#0-0) . If the vault later defaults and `finalizeDefaultRecovery` runs while `pendingInstantWithdraws != 0`, `_claimDefaultedInstantWithdrawRequest` re-reads the stale per-epoch basis and pays `claimBasis * defaultRecoveryPrice` a second time from `defaultRecoveryReserve` [2](#0-1) . One receipt, two payouts — the same divergence as the two-lien bug.

### Finding Description
`requestInstantWithdraw` records the receipt in both `instantWithdrawsRequests[_user]` and `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, and bumps `instantWithdrawClaimsByEpoch[currentEpoch]` [3](#0-2) . A partially funded instant queue is possible: `collectInstantWithdrawFunds` only decrements `pendingInstantWithdraws` [4](#0-3) , and `claimInstantWithdrawRequest` has no check that the request is fully funded — it transfers whatever the strategy holds via `_transferFundedClaim`. The attacker (a KYC'd lender) requests an instant withdraw, claims it once funds are available, then — in the same epoch — deposits again and requests a second instant withdraw so `_burn(_user, claimBasis)` in the defaulted claim path has receipt tokens to consume. When the borrower defaults and `finalizeDefaultRecovery` finalizes with `pendingInstantWithdraws != 0` (any other unfunded instant request keeps it nonzero), the attacker calls `claimInstantWithdrawRequest` again: `_claimDefaultedInstantWithdrawRequest` reads the still-set `instantWithdrawsRequestsByEpoch[attacker][defaultEpoch]` and pays recovery on the already-claimed amount [5](#0-4) .

### Impact Explanation
Direct theft of `defaultRecoveryReserve`: the attacker is paid `claimBasis * defaultRecoveryPrice` on a receipt that was already paid at par, diluting or draining funds owed to other defaulted-epoch and post-default claimants. Loss scales with the attacker's instant-withdraw size.

### Likelihood Explanation
Requires (a) an instant-withdraw request claimed in the epoch that ends up defaulting, (b) `pendingInstantWithdraws != 0` at finalization so `defaultInstantWithdrawsFinalized` is set [6](#0-5) , and (c) the attacker holds fresh receipt tokens to satisfy `_burn`. All are achievable by an unprivileged lender ordering normal calls around honest manager/borrower actions; no privileged misbehavior needed.

### Recommendation
Clear the per-epoch receipt records when an instant withdraw is claimed normally: set `instantWithdrawsRequestsByEpoch[_user][epochNumber] = 0` and decrement `instantWithdrawClaimsByEpoch[epochNumber]` inside `claimInstantWithdrawRequest`, mirroring how `_claimDefaultedInstantWithdrawRequest` clears them [7](#0-6) .

### Proof of Concept
Foundry fork PoC sketch:

```solidity
// 1. Epoch running. Attacker (KYC'd lender) deposits, then calls
//    IdleCDOEpochVariant.requestInstantWithdraw -> IdleCreditVault.requestInstantWithdraw(X).
// 2. Borrower/manager partially fund instant queue via collectInstantWithdrawFunds
//    (another user's instant request stays unfunded -> pendingInstantWithdraws > 0).
// 3. Attacker calls claimInstantWithdrawRequest(attacker): receives X, but
//    instantWithdrawsRequestsByEpoch[attacker][E] still == X.
// 4. Attacker deposits again and requests instant withdraw Y (fresh receipt tokens).
// 5. Borrower defaults; manager calls finalizeDefault; CDO calls
//    finalizeDefaultRecovery -> defaultInstantWithdrawsFinalized = true.
// 6. Attacker calls claimInstantWithdrawRequest(attacker) again:
//    _claimDefaultedInstantWithdrawRequest pays X * defaultRecoveryPrice from
//    defaultRecoveryReserve a second time.
// Assert: attacker underlying balance == X + Y_claimed + X * recoveryPrice.
```

Caveat: this hinges on `claimInstantWithdrawRequest` paying out while per-epoch records persist and on a partially funded instant queue coexisting with a default in the same epoch — worth verifying against the CDO's `getInstantWithdrawFunds`/claim gating in `IdleCDOEpochVariant` before finalizing severity.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L696-696)
```text
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
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
