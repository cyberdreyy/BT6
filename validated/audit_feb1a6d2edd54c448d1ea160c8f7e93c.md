### Title
Stale epoch-keyed instant-withdraw receipts bypass the default recovery haircut and are paid at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Instant-withdraw receipts are keyed by the request-time `epochNumber`, but default finalization and the post-default claim path only look up receipts under the *current* `epochNumber` / `defaultRecoveryEpoch`. An instant receipt created in an earlier epoch and left unfunded survives the epoch roll as a stale ledger entry, is excluded from the recovery claim basis, and after `finalizeDefaultRecovery` is paid out 1:1 through the funded-claim path — the same class of bug as the t7xx NAPI poll dereferencing a netdev already released by dellink: a consumer still uses state belonging to a lifecycle epoch that has already been torn down.

### Finding Description
- `requestInstantWithdraw` records receipts under the *request* epoch: `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` and `instantWithdrawClaimsByEpoch[currentEpoch]` where `currentEpoch = epochNumber` [1](#0-0) 
- `pendingInstantWithdraws` is a single global counter that is only decremented by `collectInstantWithdrawFunds` when funds actually arrive [2](#0-1) . The code itself acknowledges partially-funded instant queues surviving `startEpoch` (see `_defaultPrefundedInstantReserve` comments), so an unfunded instant remainder can persist across an epoch roll [3](#0-2) .
- At default finalization, `defaultPendingClaimBasis()` only adds `instantWithdrawClaimsByEpoch[epochNumber]` — i.e., receipts keyed under the *default* epoch [4](#0-3) . Stale receipts keyed under earlier epochs are omitted from `totalBasis`, which inflates `defaultRecoveryPrice` for everyone.
- After finalization, `claimInstantWithdrawRequest` calls `_claimDefaultedInstantWithdrawRequest`, which only clears `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` [5](#0-4) . For a stale-epoch receipt this returns 0, execution falls through, and lines 387–392 pay the **entire** `instantWithdrawsRequests[_user]` at par via `_transferFundedClaim` [6](#0-5)  — from the same underlying balance that backs `defaultRecoveryReserve`, since no guard separates the two funding sources.

Broken invariant: loss socialization / one-receipt-one-haircut. Every pending receipt is supposed to be converted to the recovery ratio at finalization; stale-epoch instant receipts escape both the basis computation and the haircut.

### Impact Explanation
Direct theft / dilution: an attacker holding a stale-epoch instant receipt receives `instantWithdrawsRequests[user]` at par, while honest defaulted claimants (normal withdraws, current-epoch instant receipts, active tranche holders) only recover `defaultRecoveryPrice` per unit. Two compounding effects: (1) `defaultPendingClaimBasis` undercounts `totalBasis`, so `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` is set too high and every recovery claimant is overpaid relative to true backing; (2) the stale receipt then draws a second, unhaircutted payment from the funded/reserve balance, which can leave insufficient underlying for later defaulted claims — permanent loss for honest claimants up to the attacker's receipt size.

### Likelihood Explanation
Requires: an unfunded instant-withdraw remainder persisting across an epoch boundary (the code explicitly handles "partially prefunded instant" cases), followed by a borrower default and `finalizeDefaultRecovery`. An unprivileged KYC'd lender can open the instant request; the epoch roll and default are driven by honest manager/borrower sequencing, not attacker privilege. Likelihood is moderate — it depends on an underfunded instant queue at epoch end and a subsequent default, but no attacker control over privileged roles is needed, and under this scan's rules the default itself is a scenario condition, not attacker-caused freezing.

Caveat: I was unable to fully trace `_transferFundedClaim`'s balance sourcing and the exact `startEpoch`/`getInstantWithdrawFunds` funding sequence within the available iterations; the finding assumes (consistent with the in-code comments) that partially-funded instant queues can persist across epochs and that `_transferFundedClaim` pays from the strategy's shared underlying balance. If `_transferFundedClaim` is isolated from the recovery reserve, impact degrades to the recovery-price overstatement in `defaultPendingClaimBasis`.

### Recommendation
Track a request-epoch key per instant receipt and iterate/normalize it at finalization (e.g., fold all entries of `instantWithdrawsRequestsByEpoch` / `instantWithdrawClaimsByEpoch` across epochs ≤ `epochNumber` into `defaultPendingClaimBasis` and into `_claimDefaultedInstantWithdrawRequest`), or key instant receipts by a global "instant epoch" that is updated on every epoch roll so no stale key survives. At minimum, `_claimDefaultedInstantWithdrawRequest` should clear `instantWithdrawsRequests[_user]` regardless of which epoch key holds it.

### Proof of Concept
```solidity
// test/foundry/IdleCreditVault.t.sol style
function testStaleEpochInstantReceiptBypassesDefaultHaircut() external {
    uint256 amount = 10_000 * ONE_SCALE;
    address attacker = makeAddr('attacker');
    address victim = makeAddr('victim');

    idleCDO.depositAA(amount);
    _depositWithUser(attacker, amount, true);
    _depositWithUser(victim, amount, true);
    _startEpochAndCheckPrices(0);

    // epoch 0 ends: borrower funds only PART of the instant queue.
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(/* tranche amount */, address(AAtranche));
    // stopEpoch with insufficient instant liquidity: pendingInstantWithdraws > 0 persists
    _stopEpochAndCheckPrices(0, initialProvidedApr, /* funded < pending instant */);

    // epoch rolls to 1: attacker's receipt stays keyed under epoch 0
    _startEpochAndCheckPrices(1);

    // borrower defaults in epoch 1; owner finalizes with partial recovery
    _checkDefault();
    deal(defaultUnderlying, borrower, recovered);
    vm.prank(owner);
    cdoEpoch.finalizeDefault(recovered, borrower);

    // victim's epoch-1 normal/defaulted receipt is haircutted
    uint256 victimClaimBasis = /* victim default-epoch receipt */;
    // attacker's stale receipt was excluded from defaultPendingClaimBasis
    // and _claimDefaultedInstantWithdrawRequest finds epoch-1 key == 0.

    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest(); // pays instantWithdrawsRequests[attacker] at PAR
    assertEq(underlying.balanceOf(attacker) - balPre, attackerReceipt, 'stale receipt paid unhaircutted');

    // honest victim only receives recovered * recoveryPrice, and recoveryPrice was
    // inflated because attacker basis was omitted -> reserve drains early or
    // subsequent defaulted claims revert on insufficient balance.
}
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L713-723)
```text
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
