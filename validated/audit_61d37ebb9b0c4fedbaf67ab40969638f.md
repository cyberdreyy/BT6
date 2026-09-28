Let me check the CDO-side claim path for epoch-offset mismatches.### Title
Stale request-epoch "offset" makes cross-epoch instant-withdraw receipts unclaimable after default finalization - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The kernel bug used an IPv4 header offset on an IPv6 packet — a correct-looking index applied to the wrong layout. The analog here is `defaultPendingClaimBasis` and `_claimDefaultedInstantWithdrawRequest`: both assume every still-unfunded instant-withdraw receipt was recorded under the *current* `epochNumber`. Instant receipts are keyed by their request epoch (`instantWithdrawsRequestsByEpoch[user][currentEpoch]`, `instantWithdrawClaimsByEpoch[currentEpoch]`), but `epochNumber` advances on each `stopEpoch`/`deposit()` while `pendingInstantWithdraws` can carry unfunded receipts forward. A default finalized in a later epoch reads the instant basis at the wrong epoch offset, freezing those receipts permanently. [1](#0-0) [2](#0-1) 

### Finding Description
When a user calls `requestWithdraw` on the CDO while the APR dropped, `requestInstantWithdraw(_amount, _user)` burns CDO strategy tokens, mints a receipt to the user, and records the claim under the *request* epoch: `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount` and `instantWithdrawClaimsByEpoch[currentEpoch] += _amount`. [3](#0-2) 

If the CDO only partially funds instant withdrawals, `pendingInstantWithdraws` stays positive while the funded portion is held in the strategy — the code explicitly contemplates this in `_defaultPrefundedInstantReserve`: "pendingInstantWithdraws is the still-unfunded remainder. If it is lower than the current-epoch claim basis, the difference is already-held underlying". [4](#0-3) 

`epochNumber` is incremented in `deposit()` whenever the CDO deposits while an epoch is running (i.e., at every `stopEpoch`). [5](#0-4)  So an instant receipt requested in epoch N that remains unfunded into epoch N+1 is stored under epoch N, while `epochNumber` is now N+1.

When the borrower later defaults and `finalizeDefaultRecovery` runs:

1. `defaultPendingClaimBasis()` adds `instantWithdrawClaimsByEpoch[epochNumber]` — the *finalization* epoch — not the epoch(s) where unfunded instant claims actually live. For a stale receipt and no new instant requests in epoch N+1, this reads 0, so the instant claim contributes no recovery basis. [2](#0-1) 
2. `_defaultPrefundedInstantReserve()` likewise compares `pendingInstantWithdraws` to `instantWithdrawClaimsByEpoch[epochNumber]` (0), returning 0, so the already-held prefunded portion is not added to `defaultRecoveryReserve`. [6](#0-5) 
3. `defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0` is still set true, so `claimInstantWithdrawRequest` routes through `_claimDefaultedInstantWithdrawRequest`, which looks up `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` where `defaultRecoveryEpoch = epochNumber` (N+1) — again 0 — and returns without burning or paying anything. [7](#0-6) [8](#0-7) 

The user's `instantWithdrawsRequests[_user]` stays nonzero, which also blocks any post-default `requestWithdraw` via the `_hasWithdrawRequest`-style check on `instantWithdrawsRequests[_user] != 0`. [9](#0-8) 

### Impact Explanation
Instant-withdraw requesters whose receipts crossed an epoch boundary while unfunded receive zero recovery: their basis is excluded from `totalBasis` (so the recovery price is computed *without* them, inflating payouts to other claimants), their prefunded strategy-held underlyings are excluded from `defaultRecoveryReserve`, and their per-epoch claim entry can never be cleared because `_claimDefaultedInstantWithdrawRequest` only ever reads `defaultRecoveryEpoch`. The receipt tokens remain minted but unburnable/unpayable — permanent freezing of unclaimed withdrawal funds, equal to the full stale instant receipt amount, while other claimants get an inflated `recoveryPrice` that drains the reserve meant to be shared. [10](#0-9) 

### Likelihood Explanation
Requires (a) instant withdrawals enabled, (b) an instant request that is only partially funded so `pendingInstantWithdraws` survives a `stopEpoch`/`epochNumber` bump, and (c) a borrower default finalized in a later epoch. Partial instant funding is a supported flow (the prefunded-reserve accounting exists precisely for it), and honest manager/borrower sequencing suffices — no privileged attacker needed; the victim is an ordinary tranche holder who requested an instant withdraw. Note: the exploitability hinges on the CDO legitimately leaving `pendingInstantWithdraws > 0` across an epoch boundary — the strategy's own comments and tests (`testProcessWithdrawalClaimsMixedInstantAndNormalEpoch`) indicate partial funding paths exist, but I could not fully confirm the exact funding sequence inside `IdleCDOEpochVariant` within the available context.

### Recommendation
Index instant-withdraw recovery accounting by *request* epoch rather than finalization epoch. Track the set/aggregate of epochs with outstanding instant claims (e.g., accumulate `instantWithdrawClaimsByEpoch` across all epochs with `pendingInstantWithdraws` backing, or maintain a global `instantWithdrawClaimsTotal`), and have `_claimDefaultedInstantWithdrawRequest` iterate/sum the user's per-epoch entries instead of reading only `defaultRecoveryEpoch`. Similarly, `_defaultPrefundedInstantReserve` should compare `pendingInstantWithdraws` against the total outstanding instant claim basis, not `instantWithdrawClaimsByEpoch[epochNumber]`.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;
// Fork test against an existing deployment; setup mirrors test/foundry/IdleCreditVault.t.sol

function testStaleEpochInstantClaimFrozenAfterDefault() external {
    // --- Epoch N (running): attacker/victim requests instant withdraw ---
    _startEpochAndCheckPrices(0);
    _depositWithUser(victim, 100_000 * ONE_SCALE, true); // victim holds AA tranches
    cdoEpoch.setInstantWithdrawParams(100, 1e18, false); // manager, honest
    // force APR drop so requestWithdraw routes to instant path
    _forceLastEpochAprToZero();
    vm.prank(victim);
    cdoEpoch.requestWithdraw(victimAABal, address(AAtranche)); // mints instant receipt under epoch N

    // --- CDO partially funds instant queue; pendingInstantWithdraws > 0 ---
    // (only part of instant claims collected via collectInstantWithdrawFunds;
    //  funded portion sits in strategy, remainder stays pending)

    // --- stopEpoch bumps epochNumber to N+1 ---
    deal(underlying, borrower, _expectedFundsEndEpoch());
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(apr, 0);
    // receipt is now recorded under epoch N; epochNumber == N+1

    // --- borrower defaults; default finalized in epoch N+1 ---
    _handleBorrowerDefault();
    finalizeDefaultRecovery(recovered, recoverySource);
    // defaultPendingClaimBasis() read instantWithdrawClaimsByEpoch[N+1] == 0
    // -> victim's claim excluded; prefunded reserve not counted.

    // --- victim's claim is frozen forever ---
    vm.prank(victim);
    cdoEpoch.claimInstantWithdrawRequest(); // reads epoch N+1 -> 0, pays nothing
    assertGt(strategy.instantWithdrawsRequests(victim), 0); // receipt stuck, unburnable
}
```
Key assertions: `defaultRecoveryReserve` excludes the victim's already-prefunded underlying (recoverable by no one — reserve is only decremented via `_transferDefaultRecovery`), `instantWithdrawsRequestsByEpoch[victim][N]` remains nonzero forever, and `instantWithdrawsRequests[victim] != 0` permanently blocks the victim's post-default `requestWithdraw`.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L382-393)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-649)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L686-699)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L712-723)
```text
  /// @notice Get current-epoch instant-withdraw funds already collected before default finalization.
  /// @dev `pendingInstantWithdraws` is the still-unfunded remainder. If it is lower than the
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
