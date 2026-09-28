### Title
Stale instant-withdraw epoch ledger after a funded claim inflates default-recovery accounting, leaving the recovery reserve insolvent and freezing the last claimants' payouts — (`contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
`claimInstantWithdrawRequest` pays out a funded instant-withdraw receipt but never clears `instantWithdrawsRequestsByEpoch[_user][epoch]` or decrements `instantWithdrawClaimsByEpoch[epoch]`. If the pool later defaults and is finalized **in the same strategy epoch** while some other instant receipt is still unfunded, `defaultPendingClaimBasis()` and `_defaultPrefundedInstantReserve()` read those stale entries and count already-paid underlying as both claim basis and held reserve. The finalized `defaultRecoveryReserve`/`defaultRecoveryPrice` are therefore backed by phantom funds; once the real balance is exhausted, `_transferDefaultRecovery` reverts and remaining recovery claimants are permanently frozen.

### Finding Description
- `requestInstantWithdraw` records `instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount` and `instantWithdrawClaimsByEpoch[currentEpoch] += _amount` [1](#0-0) .
- The funded claim path burns the receipt and zeroes only the aggregate `instantWithdrawsRequests[_user]`; the per-epoch ledgers are left intact — a "freed" object still referenced by epoch-keyed state [2](#0-1) .
- The only place that clears those per-epoch entries is the defaulted-claim path `_claimDefaultedInstantWithdrawRequest` [3](#0-2) .
- On finalization, `defaultPendingClaimBasis()` adds `instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0` [4](#0-3) , and `_defaultPrefundedInstantReserve()` treats `instantBasis - pendingInstant` as underlying already held by the strategy [5](#0-4) . Both sums include the already-claimed receipt.
- `finalizeDefaultRecovery` then computes `reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve` and `recoveryPrice = reserveAmount / totalBasis` [6](#0-5) , so the reserve is credited with underlying that was already transferred to the first claimant.
- Payouts decrement `defaultRecoveryReserve` and transfer real tokens [7](#0-6) ; when the phantom portion is reached, `safeTransfer` reverts on insufficient balance.

### Impact Explanation
Insolvency / permanent freezing of unclaimed recovery. The stale entry `A` (an already-paid instant claim) inflates `prefundedReserve` and `totalBasis` symmetrically, so `defaultRecoveryPrice` looks consistent while `defaultRecoveryReserve` exceeds actual holdings by exactly `A`. Defaulted-epoch and post-default claimants are paid pro-rata until the real ERC20 balance is exhausted; the last claimants' recovery (up to `A` underlying in aggregate) is unpayable forever — `claimWithdrawRequest`/`claimInstantWithdrawRequest` revert at the transfer, with no owner-free path that can restore the missing backing. Loss magnitude equals the sum of instant receipts claimed between `collectInstantWithdrawFunds` and a default finalized in the same epoch.

### Likelihood Explanation
Requires an epoch where at least one instant request is funded and claimed and another instant request stays unfunded (`pendingInstantWithdraws != 0`), followed by a borrower default finalized before the strategy epoch number advances past that epoch. That is a narrow but reachable sequence in instant-withdraw-enabled pools (partial instant funding is explicitly supported by `_defaultPrefundedInstantReserve`). Any unprivileged tranche holder triggers the stale state simply by claiming a funded instant withdrawal; no privilege or timing manipulation is needed beyond the ordinary claim. Note: I could not fully verify whether `epochNumber` can still equal the request epoch at `finalizeDefaultRecovery` in every configuration (it is bumped by `deposit()` during a running epoch), but the prefunded-queue code itself treats `epochNumber` as the default epoch for pending claims, supporting the scenario.

### Recommendation
In `claimInstantWithdrawRequest`, clear the per-epoch ledger exactly as the default path does: locate the request's epoch entry (e.g., track the latest instant-request epoch per user, or have the CDO pass it) and set `instantWithdrawsRequestsByEpoch[_user][epoch] -= amount` / `instantWithdrawClaimsByEpoch[epoch] -= amount` when paying a funded claim. Alternatively, decrement `instantWithdrawClaimsByEpoch` inside `collectInstantWithdrawFunds`/`_transferFundedClaim` whenever the corresponding receipt basis is extinguished, so `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` never observe paid receipts.

### Proof of Concept
Foundry fork sketch (epoch-N instant-withdraw enabled pool):

```solidity
// 1) Epoch N running: userA requests instant withdraw of amountA
vm.prank(userA); cdoEpoch.requestInstantWithdraw(amountA, address(AAtranche));

// 2) Instant delay passes; manager funds instant queue via getInstantWithdrawFunds/collectInstantWithdrawFunds
//    userA claims: instantWithdrawsRequests[userA] -> 0
vm.prank(userA); cdoEpoch.claimInstantWithdrawRequest();
// BUG: instantWithdrawsRequestsByEpoch[userA][N] and instantWithdrawClaimsByEpoch[N]
//      still equal amountA (assert they are non-zero to demonstrate staleness)
assertEq(strategy.instantWithdrawsRequestsByEpoch(userA, N), amountA); // stale
assertEq(strategy.instantWithdrawClaimsByEpoch(N), amountA);          // stale

// 3) Same epoch N: userB requests instant withdraw amountB, left unfunded
vm.prank(userB); cdoEpoch.requestInstantWithdraw(amountB, address(AAtranche));
assertEq(strategy.pendingInstantWithdraws(), amountB);

// 4) Borrower defaults; manager finalizes recovery in epoch N
//    defaultPendingClaimBasis() = pendingWithdraws + amountA + amountB (amountA phantom)
//    _defaultPrefundedInstantReserve() = (amountA + amountB) - amountB = amountA (not held)
vm.prank(manager); cdoEpoch.finalizeDefault(recovered, recoverySource);
assertGt(strategy.defaultRecoveryReserve(),
         underlying.balanceOf(address(strategy)) /* minus other reserves */);

// 5) Claimants drain until phantom amountA is reached; the final claim reverts
vm.prank(userB); cdoEpoch.claimInstantWithdrawRequest(); // pays amountB * price
// userC / remaining defaulted-epoch claim:
vm.expectRevert(); // ERC20: transfer amount exceeds balance
cdoEpoch.claimWithdrawRequest();
```

The broken invariant is "one receipt, one payout / solvent recovery reserve": `defaultRecoveryReserve` is credited with `amountA` that no longer exists in the contract, making the tail claims unpayable.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L685-693)
```text
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
    defaultRecoveryEpoch = epochNumber;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L912-917)
```text
  function _transferDefaultRecovery(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    // Every defaulted or post-default claim consumes the isolated recovery reserve.
    defaultRecoveryReserve -= _amount;
    underlyingToken.safeTransfer(_user, _amount);
  }
```
