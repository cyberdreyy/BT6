### Title
Stale instant-withdraw receipts from a prior epoch are excluded from `defaultPendingClaimBasis` but still paid at par via `claimInstantWithdrawRequest`, permanently freezing them after default finalization - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The nanopb bug is a type-confusion: a union slot written as one member type is later read as another (non-pointer data consumed as a pointer), producing an invalid `free()`. The vault analog is an epoch-confusion over the instant-withdraw bucket: `instantWithdrawsRequests[_user]` and `pendingInstantWithdraws` are *aggregate* counters spanning all epochs, but `instantWithdrawsRequestsByEpoch`, `instantWithdrawClaimsByEpoch`, `defaultPendingClaimBasis` and `_claimDefaultedInstantWithdrawRequest` treat the bucket as if it were *current-epoch only*. A receipt written under the "unfunded pending instant" interpretation is later read under the "funded, payable at par" interpretation once a different epoch defaults, so it is never haircut, never reserved for, and never payable — the claim reverts and the receipt is frozen forever.

### Finding Description
`requestInstantWithdraw` increments the aggregate `instantWithdrawsRequests[_user]`, the per-epoch `instantWithdrawsRequestsByEpoch[_user][epochNumber]`, `instantWithdrawClaimsByEpoch[epochNumber]`, and `pendingInstantWithdraws` [1](#0-0) . Nothing prevents a user from holding an unfunded instant receipt in epoch `N` and adding another in a later epoch `M`, and there is no expiry or auto-clearing — `_ensureDefaultRecoveryInitialized` explicitly notes "Pending instant withdrawals are never cleared automatically" [2](#0-1) .

At default finalization, `defaultPendingClaimBasis` only adds `instantWithdrawClaimsByEpoch[epochNumber]` — i.e. claims created in the *defaulted* epoch — even though the `pendingInstantWithdraws != 0` gate is driven by the aggregate that includes older epochs [3](#0-2) . Symmetrically, `_claimDefaultedInstantWithdrawRequest` only clears `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` and only decrements `pendingInstantWithdraws` by that epoch's basis [4](#0-3) . Afterwards `claimInstantWithdrawRequest` burns the *entire remaining aggregate* `instantWithdrawsRequests[_user]` and tries to pay it at par through `_transferFundedClaim` [5](#0-4) , which reverts unless the strategy holds `defaultRecoveryReserve + amount` in underlying [6](#0-5) . After finalization the strategy's balance is exactly the recovery reserve (active NAV was burned/minted to `recoveryPrice`), so `balance - reserve == 0` and the transfer always reverts.

### Impact Explanation
A user whose instant-withdraw request was not funded before the epoch rolled over — a normal occurrence whenever the borrower lacks liquidity at `getInstantWithdrawFunds` time — has their receipt permanently frozen once any later epoch defaults. Two concrete cases:

- **Single stale receipt**: request in epoch `N` stays in `pendingInstantWithdraws`; epoch `N+1` (or any later) defaults. `defaultPendingClaimBasis` contributes `instantWithdrawClaimsByEpoch[defaultEpoch] == 0`, so zero reserve is allocated for the receipt, yet `defaultInstantWithdrawsFinalized` is set true. Every `claimInstantWithdrawRequest` reverts in `_transferFundedClaim` → permanent freeze of the full principal.
- **Mixed-epoch receipt**: old unfunded instant receipt in epoch `N` plus a new one in defaulted epoch `M`. The epoch-`M` portion is haircut-paid from reserve, but the trailing `_transferFundedClaim` of the epoch-`N` aggregate remainder reverts, atomically rolling back even the legitimate haircut claim → both portions frozen.

The same slot being read under two regimes (unfunded-default-basis vs funded-par-basis) is the direct analog of the oneof union confusion: the receipt's "type" (which epoch, hence which price/reserve regime) is lost, so it is dereferenced under the wrong rule.

### Likelihood Explanation
Requires only an unprivileged user: (1) request an instant withdraw during a buffer/running phase when the CDO cannot fully fund it, so `pendingInstantWithdraws > 0` survives past `stopEpoch`; (2) a subsequent epoch defaults and `finalizeDefault` runs. No privileged misbehavior is needed — borrower illiquidity/default is a normal protocol event. The freeze is deterministic: `defaultRecoveryReserve` consumes the entire strategy balance, so `balance - reserve < amount` holds for any nonzero stale amount. Note: `IdleCDOEpochQueue` paths and loss-epoch receipts have dedicated guards (`requestWithdraw` reverts on unclaimed loss-adjusted receipts [7](#0-6) ), but no equivalent guard exists on the instant-request path — that asymmetry is the bug.

### Recommendation
Track and settle instant receipts strictly per-epoch at finalization:

- In `defaultPendingClaimBasis` (and the `pendingInstantWithdraws != 0` gate), include *all* outstanding instant basis, not just `instantWithdrawClaimsByEpoch[epochNumber]` — e.g. maintain a global `instantWithdrawClaimsTotal` or iterate/aggregate so stale-epoch claims are part of `totalBasis` and receive the recovery haircut like every other pending receipt.
- In `_claimDefaultedInstantWithdrawRequest`, clear the user's *entire* `instantWithdrawsRequests[_user]` (all epochs) at `defaultRecoveryPrice`, decrement `pendingInstantWithdraws` and the per-epoch mappings consistently, instead of only `defaultRecoveryEpoch`.
- Alternatively, mirror the `requestWithdraw` loss-receipt guard: revert `requestInstantWithdraw` while the user has a pending instant receipt from an earlier epoch, and always pay stale pending instant claims through `_transferDefaultRecovery` post-finalization rather than the par `_transferFundedClaim` path.

### Proof of Concept
Foundry fork test against `IdleCreditVault` / `IdleCDOEpochVariant`:

```solidity
function testStaleInstantReceiptFrozenOnDefault() external {
    address user = makeAddr("stale-instant-user");
    uint256 amount = 10_000 * ONE_SCALE;

    // Epoch N: deposit AA, request instant withdraw that is only partially
    // (or never) funded -> pendingInstantWithdraws > 0 survives stopEpoch.
    _depositWithUser(user, amount, true);
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(delay, 1000, false);
    vm.prank(user);
    cdoEpoch.requestInstantWithdraw(amount / 2, address(AAtranche));
    // borrower has no liquidity: do NOT call getInstantWithdrawFunds
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, 0); // epoch N ends unfunded

    // Epoch M (=N+1): borrower defaults.
    _startEpochAndCheckPrices(1);
    deal(address(underlying), strategy.borrower(), 0, true);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(cdoEpoch.owner());
    cdoEpoch.stopEpoch(0, 0);
    assertTrue(cdoEpoch.defaulted());

    // Finalize with a partial recovery sized on a basis that excludes the
    // stale epoch-N instant claim (instantWithdrawClaimsByEpoch[M] == 0).
    uint256 activeBasis = cdoEpoch.getContractValue()
        + cdoEpoch.expectedEpochInterest() - cdoEpoch.pendingWithdrawFees();
    uint256 recovered = activeBasis * 7e17 / 1e18;
    deal(address(underlying), manager, recovered);
    vm.startPrank(manager);
    underlying.approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();

    // The stale receipt is neither haircut nor payable: claim always reverts
    // inside _transferFundedClaim because balance == defaultRecoveryReserve.
    vm.prank(user);
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.claimInstantWithdrawRequest();
    // user's `amount/2` principal is permanently frozen.
}
```

Key assertion variants: with a second epoch-`M` instant request from the same user, the whole `claimInstantWithdrawRequest` call (including the legitimate haircut portion) reverts, demonstrating the atomic freeze of both claims.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L263-271)
```text
    if (
      lossRecoveryPrice != 0 &&
      (withdrawsRequestsByEpoch[_user][lossEpoch] != 0 ||
      (apr0Users[_user].principal != 0 && apr0Users[_user].principalEpoch == lossEpoch))
    ) {
      // A loss-adjusted receipt must be claimed before opening a later request, otherwise
      // `lastWithdrawRequest` would stop pointing to the epoch that stores its haircut.
      revert NotAllowed();
    }
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L919-934)
```text
  /// @notice Lazily initialize recovery accounting for an upgraded strategy.
  /// @dev Legacy pending receipts must first be fully funded because their per-epoch ownership
  /// cannot be reconstructed after an implementation upgrade. A successfully closed vault has
  /// already recalled all funds, so stale normal/APR0 aggregate counters can be normalized there.
  /// Pending instant withdrawals are never cleared automatically.
  function _ensureDefaultRecoveryInitialized() internal {
    if (defaultRecoveryInitialized) return;
    if (pendingInstantWithdraws != 0) revert NotAllowed();
    if (pendingWithdraws != 0) {
      IIdleCDOEpochVariant cdo = IIdleCDOEpochVariant(idleCDO);
      if (cdo.epochEndDate() != 0 || cdo.defaulted()) revert NotAllowed();
      pendingWithdraws = 0;
      apr0TotalPrincipal = 0;
    }
    defaultRecoveryInitialized = true;
    canTransfer = false;
```
