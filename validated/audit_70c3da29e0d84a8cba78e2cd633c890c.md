### Title
Stale funded instant-withdraw basis inflates default recovery denominator and permanently strands recovery funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` clears the user's aggregate `instantWithdrawsRequests` but never clears `instantWithdrawsRequestsByEpoch[user][epoch]` or `instantWithdrawClaimsByEpoch[epoch]`. If the borrower defaults later in the same epoch while another instant receipt remains unfunded, `defaultPendingClaimBasis()` re-counts the already-paid receipt in `instantWithdrawClaimsByEpoch[epochNumber]`, inflating the recovery denominator and diluting `defaultRecoveryPrice`. The corresponding share of `defaultRecoveryReserve` can never be claimed and is stranded forever, and the already-paid user's `claimInstantWithdrawRequest` permanently reverts on underflow.

### Finding Description
- `claimInstantWithdrawRequest` (lines 380-393) zeroes only `instantWithdrawsRequests[_user]`; the per-epoch mappings `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` and `instantWithdrawClaimsByEpoch[currentEpoch]` (written in `requestInstantWithdraw`, lines 371-372) are left untouched. [1](#0-0) 
- `defaultPendingClaimBasis()` adds `instantWithdrawClaimsByEpoch[epochNumber]` wholesale whenever `pendingInstantWithdraws != 0` at finalization (lines 644-649). [2](#0-1) 
- `finalizeDefaultRecovery` computes `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` using that inflated `totalBasis` (lines 679-692), and sets `defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0` (line 696). [3](#0-2) 
- Post-finalization, `claimInstantWithdrawRequest` calls `_claimDefaultedInstantWithdrawRequest`, which reads the stale `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]` and executes `instantWithdrawsRequests[_user] -= claimBasis` (lines 844-848). Since the aggregate was already zeroed by the funded claim, this underflows and reverts permanently; `instantWithdrawClaimsByEpoch[defaultEpoch]` (line 853) is therefore never decremented for that stale basis. [4](#0-3) 

### Impact Explanation
This maps the crash/OOB bug class to an accounting OOB on epoch state: reading a stale epoch bucket that was logically, but not physically, cleared.

- **Permanent freezing of recovery funds**: `totalBasis` includes the already-claimed instant amount `S`, so `reserveAmount` (fixed real recovered tokens) is divided by `activeBasis + pendingBasis + S`. The reserve portion notionally allocated to the ghost claim `S` (`S * recoveryPrice / 1e18`) has no claimant — the paid user's claim reverts — and `defaultRecoveryReserve` accounting has no sweep path, so those underlying tokens are locked in the strategy forever.
- **Diluted payouts**: every other defaulted-epoch claimant (normal withdrawers, unfunded instant requesters, and active AA/BB holders via `activeFinalNAV`/`defaultBBNav`) receives a strictly lower recovery multiplier than entitled.
- **Permanent per-user DoS with fund impact**: the already-paid user's `claimInstantWithdrawRequest` reverts forever, also blocking any legitimately remaining balance path.

Quantified loss: stranded reserve ≈ `S * reserveAmount / totalBasis`, plus the aggregate haircut applied to all other claimants, proportional to the funded instant amount `S`.

### Likelihood Explanation
Requires: (1) two instant withdraw requests in the same epoch; (2) partial borrower funding so one is claimed (`collectInstantWithdrawFunds` reduces `pendingInstantWithdraws` below the epoch basis); (3) a borrower default in that epoch before the remainder is funded, with `finalizeDefaultRecovery` run while `pendingInstantWithdraws != 0`. No privileged misbehavior is needed — the borrower defaulting is a normal protocol event, and partial instant funding is explicitly contemplated by `_defaultPrefundedInstantReserve`. The triggering user is an ordinary KYC'd tranche holder.

### Recommendation
In `claimInstantWithdrawRequest` (funded path), clear the requester's per-epoch entries in addition to the aggregate: iterate/zero `instantWithdrawsRequestsByEpoch[_user][lastKnownEpoch]` and decrement `instantWithdrawClaimsByEpoch` by the claimed amount — or store each user's latest instant-request epoch (like `lastWithdrawRequest`) so the funded claim can decrement `instantWithdrawClaimsByEpoch[epoch]` and zero the per-epoch entry, keeping `defaultPendingClaimBasis()` consistent with `pendingInstantWithdraws`.

### Proof of Concept
Foundry fork scenario (USDC, 6 decimals), standard epoch variant with instant withdrawals enabled:

```solidity
function testStaleInstantBasisDilutesRecovery() external {
    // setup: deposits AA/BB, epoch 1 running, instant withdraws enabled
    // (instantDelay elapsed, standard _depositWithUser helpers)

    // users A and B request instant withdraws in epoch N
    vm.prank(A); cdoEpoch.requestInstantWithdraw(1_000e6, address(AAtranche)); // S = 1000
    vm.prank(B); cdoEpoch.requestInstantWithdraw(1_000e6, address(AAtranche));

    // borrower funds only A's claim during the epoch (partial funding)
    // -> collectInstantWithdrawFunds(1000e6); pendingInstantWithdraws = 1000e6
    vm.prank(A); cdoEpoch.claimInstantWithdrawRequest(); // A paid; aggregate=0,
    // but instantWithdrawsRequestsByEpoch[A][N] and instantWithdrawClaimsByEpoch[N]
    // still hold A's 1000e6

    // borrower defaults in epoch N; manager triggers default, finalizeDefaultRecovery runs
    // pendingInstantWithdraws != 0  => defaultPendingClaimBasis includes
    // instantWithdrawClaimsByEpoch[N] = 2000e6 instead of the true 1000e6
    // => defaultRecoveryPrice is diluted by factor totalBasis/(totalBasis+1000e6)

    // B claims at diluted price: receives less than reserveAmount * B / trueBasis
    vm.prank(B); cdoEpoch.claimInstantWithdrawRequest();

    // A's claim now reverts permanently on underflow
    vm.prank(A);
    vm.expectRevert(); // instantWithdrawsRequests[A] -= staleBasis underflows
    cdoEpoch.claimInstantWithdrawRequest();

    // assertion: ~S * defaultRecoveryPrice / 1e18 underlying remains stuck in
    // IdleCreditVault.defaultRecoveryReserve with no claim path
}
```

Run against the existing `test/foundry/IdleCreditVault.t.sol` harness (`_useStandardEpochVariant`, `setInstantWithdrawParams`, borrower default helpers) with mainnet USDC fork per `foundry.toml`.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L371-392)
```text
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
  }

  /// @notice claim the instant withdraw request
  /// @dev we transfer the underlying tokens
  /// @param _user address of the user
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L679-696)
```text
    uint256 pendingBasis = defaultPendingClaimBasis();
    uint256 totalBasis = activeBasis + pendingBasis;
    if (totalBasis == 0) revert NotAllowed();

    // Some recovery funds may already be in this strategy: partially prefunded instant requests
    // and borrower-send funds that failed at epoch start. Count both without pulling them again.
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
