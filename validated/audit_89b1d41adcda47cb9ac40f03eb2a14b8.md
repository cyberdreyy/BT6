### Title
Claimed instant-withdraw receipts stay counted in `instantWithdrawsRequestsByEpoch`, inflating the user's default-recovery claim and stealing recovery reserve - (contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.claimInstantWithdrawRequest` burns the user's strategy-token receipt and zeroes the aggregate `instantWithdrawsRequests[_user]`, but it never clears the per-epoch ledgers `instantWithdrawsRequestsByEpoch[_user][epochNumber]` or `instantWithdrawClaimsByEpoch[epochNumber]` [1](#0-0) . If that same epoch later defaults and recovery is finalized, `_claimDefaultedInstantWithdrawRequest` reads the stale per-epoch basis and pays out `(claimBasis * defaultRecoveryPrice) / RECOVERY_FULL` for receipts that were already burned and paid [2](#0-1) . This is the same class as the Nouns Builder bug: tokens are burned but remain in the accounting that determines a claimant's share, so the claimant's share inflates.

### Finding Description
- `requestInstantWithdraw` records both `instantWithdrawsRequestsByEpoch[user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` for every request [3](#0-2) .
- `claimInstantWithdrawRequest` burns the minted receipt tokens and resets only `instantWithdrawsRequests[_user] = 0`; the two per-epoch mappings keep the full, already-paid basis [1](#0-0) .
- On default finalization, `defaultPendingClaimBasis()` adds `instantWithdrawClaimsByEpoch[epochNumber]` (still containing the claimed amount) whenever `pendingInstantWithdraws != 0` [4](#0-3) , and `_claimDefaultedInstantWithdrawRequest` pays the stale `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` basis again [2](#0-1) .
- The required burn inside `_claimDefaultedInstantWithdrawRequest` can be satisfied by a second instant request minted in the same default epoch (`_mint(_user, _amount)` in `requestInstantWithdraw`) that remains unfunded at default [5](#0-4) , so the user burns fresh receipt A2 while claiming recovery on stale basis A1 + A2.

Attack sequence (running epoch, instant-withdraw mode enabled):
1. User calls `requestWithdraw` → `requestInstantWithdraw(A1)`; receipt tokens minted.
2. Borrower fronts funds; user calls `claimInstantWithdrawRequest` → A1 paid, receipts burned, per-epoch ledgers untouched.
3. User requests another instant withdraw `A2` in the same epoch; receipts minted, `instantWithdrawsRequestsByEpoch[user][epoch] = A1 + A2`.
4. Epoch stops, borrower defaults, `finalizeDefaultRecovery` runs. `defaultPendingClaimBasis` counts `instantWithdrawClaimsByEpoch[epoch] = A1 + A2`.
5. User calls `claimInstantWithdrawRequest` → `_claimDefaultedInstantWithdrawRequest` burns the A2 receipt and pays `(A1 + A2) * defaultRecoveryPrice / RECOVERY_FULL`, double-paying A1.

### Impact Explanation
The user is paid a recovery share on already-settled receipts, directly draining `defaultRecoveryReserve` (`_transferDefaultRecovery` decrements it [6](#0-5) ). Other recovery claimants lose up to the stolen `A1 * defaultRecoveryPrice` amount, or their claims revert once the reserve is exhausted — direct theft / freezing of unclaimed recovery. Additionally, `defaultPendingClaimBasis` over-counts total claims, skewing `defaultRecoveryPrice` for everyone.

### Likelihood Explanation
Requires instant-withdraw mode (`allowInstantWithdraw`, non-programmable borrower), a user with two instant requests in the same epoch where the first is funded and claimed, and a subsequent borrower default in that epoch. All steps use only unprivileged lender actions (`requestWithdraw`/`claimInstantWithdrawRequest`); borrower default is an environmental condition, not attacker action.

### Recommendation
In `claimInstantWithdrawRequest`, decrement `instantWithdrawsRequestsByEpoch[_user][epochNumber]` (and `instantWithdrawClaimsByEpoch[epochNumber]`) by the claimed amount before zeroing the aggregate, analogous to decrementing `totalSupply` on burn in the original report. Alternatively clear per-epoch entries for the user's recorded request epoch.

### Proof of Concept
I was not able to fully verify `finalizeDefault`'s choice of `defaultRecoveryEpoch` and the reserve-funding path within the available iterations, so the reserve sizing on step 4 should be confirmed before submitting a Foundry PoC. Sketch:

```solidity
// setup: instant withdraw enabled, epoch E running
uint a1 = 1000e18, a2 = 500e18;
cdoEpoch.requestInstantWithdrawViaCDO(user, a1);   // receipts minted, ledger[A1]
borrower.prefundInstant(a1);
cdoEpoch.claimInstantWithdrawRequest();            // paid A1, ledgers NOT cleared
cdoEpoch.requestInstantWithdrawViaCDO(user, a2);   // ledger[user][E] = A1+A2, mint A2
// epoch ends, borrower defaults
cdoEpoch.stopEpoch(...); // funding fails -> default
// finalizeDefaultRecovery(...) sets defaultRecoveryPrice, defaultInstantWithdrawsFinalized
cdoEpoch.claimInstantWithdrawRequest();            // pays (A1+A2)*recoveryPrice, not A2*recoveryPrice
assertGt(underlying.balanceOf(user), expectedOnlyA2Recovery + a1, "A1 recovered twice");
```

### Citations

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-649)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
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
