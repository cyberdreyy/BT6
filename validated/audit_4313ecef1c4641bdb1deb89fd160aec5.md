### Title
`claimInstantWithdrawRequest()` never clears `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch`, inflating default recovery basis and permanently stranding recovery funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The bug class is "a revoke/cancel/claim path deletes a position but fails to decrement the aggregate accounting counter, leaving the global value artificially high." In `IdleCreditVault`, the funded instant-withdraw claim path zeroes only `instantWithdrawsRequests[_user]` but leaves `instantWithdrawsRequestsByEpoch[_user][epoch]` and the aggregate `instantWithdrawClaimsByEpoch[epoch]` untouched. If the borrower defaults later in that same epoch while other instant receipts remain unfunded, `defaultPendingClaimBasis()` re-counts already-paid receipts, inflating `totalBasis` in `finalizeDefaultRecovery`, diluting `defaultRecoveryPrice`, and permanently locking the undistributed residue in `defaultRecoveryReserve`.

### Finding Description
`claimInstantWithdrawRequest` clears the funded receipt but does not touch the per-epoch ledgers:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:380-393
function claimInstantWithdrawRequest(address _user) external {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
      _claimDefaultedInstantWithdrawRequest(_user);
    }
    uint256 amount = instantWithdrawsRequests[_user];
    _burn(_user, amount);
    instantWithdrawsRequests[_user] = 0;   // only aggregate cleared
    _transferFundedClaim(_user, amount);
}
```

`instantWithdrawsRequestsByEpoch[_user][epoch]` (written at `IdleCreditVault.sol:371`) and `instantWithdrawClaimsByEpoch[epoch]` (`IdleCreditVault.sol:372`) stay non-zero. The only path that decrements them is `_claimDefaultedInstantWithdrawRequest` (`IdleCreditVault.sol:847-853`), which runs only after default finalization.

At default, `defaultPendingClaimBasis()` adds the whole current-epoch instant bucket whenever any unfunded remainder exists:

```solidity
// IdleCreditVault.sol:644-649
basis = pendingWithdraws;
if (pendingInstantWithdraws != 0) {
  basis += instantWithdrawClaimsByEpoch[epochNumber]; // includes already-paid receipts
}
```

`finalizeDefaultRecovery` then computes `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` on this inflated basis (`IdleCreditVault.sol:679-692`). The already-paid user's claim is not double-spendable — `instantWithdrawsRequests[_user] -= claimBasis` underflows at line 848 — so the phantom basis is never paid out; it just lowers `defaultRecoveryPrice` for every legitimate claimant and leaves `defaultRecoveryReserve` permanently holding the undistributed excess (no sweep exists; `_transferDefaultRecovery`/`reserveDefaultRecovery` only move reserve inward).

### Impact Explanation
Direct loss to defaulted-epoch claimants and permanent freezing of recovery funds: with a stale paid claim `S` inside `instantWithdrawClaimsByEpoch`, `totalBasis` grows by `S`, so each real claimant receives `claimBasis * reserve / (realBasis + S)` instead of `claimBasis * reserve / realBasis`, and `reserve * S / (realBasis + S)` is stranded in the strategy forever. Broken invariant: one receipt one payout + accurate recovery waterfall (analog of `categoryUsed` staying high after `emergencyRevoke`).

### Likelihood Explanation
Requires a specific but unextraordinary sequence in one epoch: (1) an instant-withdraw receipt is fully funded via `collectInstantWithdrawFunds`/instant funding and claimed while the epoch still runs; (2) a second instant request in the same epoch stays partially unfunded so `pendingInstantWithdraws != 0`; (3) the borrower defaults at that epoch's stop and `finalizeDefault` runs. All actors are honest (claimants, manager, borrower default is an allowed scenario), so this needs only market conditions, not malicious privilege. Mixed funded/unfunded instant buckets in the default epoch are explicitly contemplated by the code (`defaultInstantWithdrawsFinalized`, `_defaultPrefundedInstantReserve`).

### Recommendation
In `claimInstantWithdrawRequest` (and any funded-claim path), clear the per-epoch entries and decrement `instantWithdrawClaimsByEpoch[requestEpoch]` for each receipt paid, e.g. iterate the user's per-epoch instant receipts or track the open epoch per user, mirroring how `_clearWithdrawClaimForEpoch` cleans `withdrawsRequestsByEpoch` for normal receipts.

### Proof of Concept
Foundry fork test sketch (extend `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testStaleInstantBasisDilutesDefaultRecovery() external {
    // 1. deposit with userA and userB, run epoch 0, stop it.
    // 2. buffer: userA and userB both requestWithdraw -> instant requests in epoch 1.
    // 3. startEpoch; fund only userA's share via getInstantWithdrawFunds
    //    (partial funding => pendingInstantWithdraws > 0 for userB).
    // 4. userA calls cdoEpoch.claimInstantWithdrawRequest(); paid in full.
    //    assert strategy.instantWithdrawClaimsByEpoch(1) still includes userA's amount -> BUG.
    // 5. borrower defaults at stopEpoch (getInstantWithdrawFunds shortfall -> _checkDefault()).
    // 6. manager calls finalizeDefault(recovered, manager) where recovered is computed
    //    against the TRUE basis (activeBasis + pendingWithdraws + userB instant claim).
    // 7. assert creditVault.defaultRecoveryPrice() < expected price (basis inflated by userA).
    // 8. userB claims; receives less than pro-rata; assert residual
    //    defaultRecoveryReserve > 0 that can never be distributed.
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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
