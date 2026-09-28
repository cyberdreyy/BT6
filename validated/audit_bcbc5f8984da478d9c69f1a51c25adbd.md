### Title
Claimed instant-withdraw receipts are never removed from `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch`, corrupting default-recovery accounting — (`contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
The bug class is "stale record after burn/claim": just like the Clober order whose `owner` was not zeroed in `_burnToken`, `IdleCreditVault.claimInstantWithdrawRequest` burns the user's receipt tokens and zeroes only the aggregate `instantWithdrawsRequests[_user]`, while leaving the per-epoch ledgers `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` untouched. Those per-epoch ledgers are exactly what `finalizeDefaultRecovery` uses to size the recovery basis and prefunded reserve, so a fully-paid receipt is counted a second time when the same epoch later defaults.

### Finding Description
`requestInstantWithdraw` records three pieces of state: the aggregate `instantWithdrawsRequests[_user]`, the per-user-per-epoch `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, and the per-epoch total `instantWithdrawClaimsByEpoch[currentEpoch]` [1](#0-0) . On a normal (funded) claim, only the aggregate is cleared:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
``` [2](#0-1) 

`instantWithdrawsRequestsByEpoch` and `instantWithdrawClaimsByEpoch` are only decremented on the defaulted-claim path [3](#0-2) . So after a successful funded claim, the receipt tokens are burned but the "order owner" analog — the per-epoch claim basis — still exists.

If the same epoch subsequently defaults while `pendingInstantWithdraws != 0` (i.e., the borrower partially funded the instant queue via `collectInstantWithdrawFunds` but did not fully repay at `stopEpoch`), `finalizeDefaultRecovery` computes:

- `basis += instantWithdrawClaimsByEpoch[epochNumber]` — includes the already-paid claim [4](#0-3) 
- `prefundedReserve = instantBasis - pendingInstant` — inflated by the stale entry, crediting the reserve with underlying that was already transferred out [5](#0-4) 
- `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` where both terms contain the phantom amount [6](#0-5) 

The stale entry therefore books "ghost backing": the recovery price is computed as if the already-paid-out underlying were still held in the strategy. Honest defaulted-epoch claimants then draw `defaultRecoveryReserve` down at an inflated price via `_transferDefaultRecovery` [7](#0-6) , until the real balance is exhausted and every remaining claim reverts on the ERC20 transfer — permanently freezing the tail claimants' recovery.

A secondary effect: if the same stale user is later pushed through `_claimDefaultedInstantWithdrawRequest` (e.g. they hold a second, legitimate defaulted receipt), `instantWithdrawsRequests[_user] -= claimBasis` underflows, freezing that user's own legitimate claim [8](#0-7) .

### Impact Explanation
An unprivileged KYC-passing lender can, in a single epoch: request an instant withdrawal, have it funded and claim it in full, then let the epoch end in default with a partially-unfunded instant queue. Their already-consumed receipt is resurrected inside `instantWithdrawClaimsByEpoch`, which inflates `defaultRecoveryPrice` with nonexistent backing. Earlier claimants (which can include the attacker via a second normal receipt in the same defaulted epoch, claimed through `claimWithdrawRequest`) are overpaid relative to the true recovery ratio; later honest claimants' `_transferDefaultRecovery` calls revert because the reserve is spent. Net effect: direct theft by early claimants and permanent freezing of the last claimants' recovery, quantified as `staleInstantBasis / totalBasis` of the true reserve. No privileged misbehavior is required — only an honest partial borrower repayment followed by a default.

### Likelihood Explanation
Requires the conjunction of: instant withdrawals enabled, an instant claim funded and claimed during a running epoch, the instant queue not fully funded (`pendingInstantWithdraws > 0`), and the epoch ending in default. Partial instant funding followed by borrower default is a designed-for state (the code explicitly handles `instantBasis > pendingInstant` at finalization), so the sequence is reachable through ordinary manager/borrower flows. The user-side steps are fully permissionless for any wallet allowed to request withdrawals.

### Recommendation
Mirror the `withdrawsRequestsByEpoch` clearing used for normal receipts: in `claimInstantWithdrawRequest` (or the CDO's per-user instant claim path), delete the user's per-epoch entries after paying:

```solidity
uint256 currentEpoch = epochNumber;
instantWithdrawsRequestsByEpoch[_user][currentEpoch] = 0;
instantWithdrawClaimsByEpoch[currentEpoch] -= amount;
```

If instant requests can span epochs, iterate or track the request epoch per user (like `lastWithdrawRequest`) so the correct epoch bucket is decremented.

### Proof of Concept
Foundry fork test in `test/foundry/IdleCreditVault.t.sol` style:

```solidity
function testClaimedInstantReceiptInflatesDefaultRecovery() external {
    // Epoch running with instant withdraws enabled; attacker + victim both request instant withdraws.
    _stopCurrentEpochWithApr(1e18);              // low apr -> instant window opens
    _depositWithUser(attacker, 50e6);
    _depositWithUser(victim,   50e6);
    cdoEpoch.requestInstantWithdraw(attackerAmount, tranche);
    cdoEpoch.requestInstantWithdraw(victimAmount,   tranche);
    _startEpoch();

    // Borrower funds only part of the instant queue; attacker claims in full.
    deal(underlying, borrower, attackerAmount);
    borrower.approve(cdoEpoch, attackerAmount);
    cdoEpoch.getInstantWithdrawFunds();          // pendingInstantWithdraws > 0 remains
    cdoEpoch.claimInstantWithdrawRequest();      // attacker paid; tokens burned

    // Stale ledgers still hold attacker's basis:
    assertEq(strategy.instantWithdrawsRequestsByEpoch(attacker, epochNumber), attackerAmount);
    assertEq(strategy.instantWithdrawClaimsByEpoch(epochNumber), attackerAmount + victimAmount);

    // Epoch ends in default; finalize recovery.
    _defaultEpoch();
    strategy.finalizeDefaultRecovery(recovered, recoverySource);

    // recoveryPrice is computed with attacker's ghost basis + ghost prefunded reserve,
    // so victim's claim is overpriced relative to real backing; after attacker/victim claim,
    // the last claimant's _transferDefaultRecovery reverts on insufficient balance.
}
```

Key assertions: after the funded claim, `instantWithdrawsRequestsByEpoch[attacker][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` remain nonzero despite the receipt tokens being burned, and the sum of all post-default recovery payouts exceeds `defaultRecoveryReserve` backing, leaving the final claimant's claim reverting.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L387-392)
```text
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L686-692)
```text
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L716-722)
```text
  function _defaultPrefundedInstantReserve() internal view returns (uint256 prefundedReserve) {
    uint256 pendingInstant = pendingInstantWithdraws;
    if (pendingInstant == 0) return prefundedReserve;
    uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
    if (instantBasis > pendingInstant) {
      prefundedReserve = instantBasis - pendingInstant;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L844-853)
```text
    claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch];
    if (claimBasis == 0) return claimBasis;

    instantWithdrawsRequestsByEpoch[_user][defaultEpoch] = 0;
    instantWithdrawsRequests[_user] -= claimBasis;
    uint256 pending = pendingInstantWithdraws;
    // `pendingInstantWithdraws` is only the unfunded remainder. If this user's claim is larger,
    // the extra amount was already counted as prefunded reserve during default finalization.
    pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis;
    instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L912-916)
```text
  function _transferDefaultRecovery(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    // Every defaulted or post-default claim consumes the isolated recovery reserve.
    defaultRecoveryReserve -= _amount;
    underlyingToken.safeTransfer(_user, _amount);
```
