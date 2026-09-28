### Title
Post-default instant withdraw requests are settled from the default recovery reserve via `_claimDefaultedInstantWithdrawRequest` — ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`requestInstantWithdraw` remains callable after `finalizeDefaultRecovery` and records new receipts under the still-current `epochNumber`, which equals `defaultRecoveryEpoch`. On claim, `_claimDefaultedInstantWithdrawRequest` treats those fresh receipts as defaulted-epoch basis and pays them out of `defaultRecoveryReserve` at `defaultRecoveryPrice`, even though no borrower/strategy funding ever backed them — an error-path double-cleanup analog where the same reserve is "freed" for receipts that were never part of the default claim basis.

### Finding Description
The kernel bug class is cleanup executed on the wrong object in an error/finalization path. Here:

- `requestInstantWithdraw` (lines 356–375) runs `_ensureDefaultRecoveryInitialized()` but never checks `defaulted()`/`defaultRecoveryFinalized`. It adds `_amount` to `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][epochNumber]`, `instantWithdrawClaimsByEpoch[epochNumber]`, and `pendingInstantWithdraws`, and mints a strategy-token receipt to `_user` [1](#0-0) .
- After a default, `epochNumber` is not advanced (epochs stop incrementing once the pool defaults), so `epochNumber == defaultRecoveryEpoch`.
- `claimInstantWithdrawRequest` (lines 380–393) calls `_claimDefaultedInstantWithdrawRequest` whenever `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized`. That function reads `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` as claim basis and pays `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` out of `defaultRecoveryReserve` [2](#0-1) [3](#0-2) .
- Post-default receipts are therefore commingled into the defaulted-epoch bucket: the attacker burns the receipt and withdraws recovery-reserve underlyings that were sized at finalization only for pre-default claimants (`defaultPendingClaimBasis` + active basis, lines 644–690). The backing for the new request was supposed to arrive via `collectInstantWithdrawFunds` at a future `stopEpoch`, which never happens post-default [4](#0-3) .

Contrast with `requestWithdraw`, which does have an explicit post-default path that haircuts the request up front and routes it to `postDefaultRequests` [5](#0-4)  — `requestInstantWithdraw` has no equivalent guard.

### Impact Explanation
Any tranche-token holder can, after default finalization, call `IdleCDOEpochVariant.requestInstantWithdraw` and then `claimInstantWithdrawRequest` in the same transaction to extract `amount * defaultRecoveryPrice / RECOVERY_FULL` from `defaultRecoveryReserve` without supplying any underlying. This directly steals recovery funds reserved for legitimate defaulted-epoch and active claimants; if enough reserve is drained, later `_transferDefaultRecovery` calls revert or underpay, permanently freezing other users' recovery [6](#0-5) . Loss is bounded by `defaultRecoveryReserve` and scales with the attacker's minted receipt size (limited by their tranche position value).

### Likelihood Explanation
Requires the pool to have defaulted and `defaultInstantWithdrawsFinalized` to be set, plus instant withdrawals enabled (`setInstantWithdrawParams`). The attacker only needs tranche tokens to mint a receipt — the KYC'd-lender/tranche-holder attacker class is in scope. No privileged action is needed; the missing `defaultRecoveryFinalized` gate in `requestInstantWithdraw` is the single point of failure.

### Recommendation
In `requestInstantWithdraw`, revert when `defaultRecoveryFinalized` (or `IIdleCDOEpochVariant(idleCDO).defaulted()`), mirroring the post-default handling in `requestWithdraw`. Alternatively, track a `postDefaultEpoch` marker so post-default instant receipts are not recorded under `defaultRecoveryEpoch` in `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch`.

### Proof of Concept
Foundry fork sketch (extends `test/foundry/IdleCreditVault.t.sol` default-finalization tests):

```solidity
// 1. Deposit AA, start epoch, stop epoch with borrower returning 0 -> default
// 2. manager finalizes: cdoEpoch.finalizeDefault(recovered, manager)
//    -> defaultRecoveryFinalized = true, reserve = recovered
// 3. Attacker (tranche holder):
vm.startPrank(attacker);
uint256 receipt = cdoEpoch.requestInstantWithdraw(attackerTrancheBal, address(AAtranche));
uint256 balPre = underlying.balanceOf(attacker);
cdoEpoch.claimInstantWithdrawRequest();
vm.stopPrank();
// attacker received receipt * defaultRecoveryPrice / RECOVERY_FULL
// from defaultRecoveryReserve with zero underlying funded:
assertGt(underlying.balanceOf(attacker) - balPre, 0);
assertLt(strategy.defaultRecoveryReserve(), reservePre); // reserve drained
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L247-257)
```text
    if (defaultRecoveryFinalized) {
      // user should first claim old already-funded withdraw requests before requesting new ones after default
      if (_hasWithdrawRequest(_user) || instantWithdrawsRequests[_user] != 0 || postDefaultRequests[_user] != 0) {
        revert NotAllowed();
      }
      // Preserve request/claim UX after default without increasing borrower-facing pendingWithdraws.
      // The CDO passes an already-haircut amount because finalization lowered virtualPrice first.
      _burn(msg.sender, _amount);
      _mint(_user, _amount);
      postDefaultRequests[_user] = _amount;
      return;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-375)
```text
  function requestInstantWithdraw(uint256 _amount, address _user) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    // burn strategy tokens from cdo
    _burn(msg.sender, _amount);
  
    // mint equal amount of strategy tokens to the user as receipt, useful in case of default
    _mint(_user, _amount);

    // increase the instant withdraw requests for the user
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
