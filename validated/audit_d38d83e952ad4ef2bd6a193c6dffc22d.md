### Title
Unfunded instant-withdraw receipts are paid out and never cleared per-epoch, enabling double-spend of pool liquidity and frozen recovery claims - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
Analog of CVE-2023-32248 (missing validation before accessing state → crash/DoS): `claimInstantWithdrawRequest` pays a receipt without checking that the request was actually funded via `collectInstantWithdrawFunds`, and (unlike the normal and defaulted claim paths) never clears `instantWithdrawsRequestsByEpoch`/`instantWithdrawClaimsByEpoch`. An unprivileged lender can claim an unfunded instant receipt early — draining underlying that backs active LPs — while the same receipt remains counted in `pendingInstantWithdraws` and in per-epoch records, producing a double payout and, on a later default, inflated recovery accounting that permanently freezes claims.

### Finding Description
`IdleCreditVault.requestInstantWithdraw` mints the user a strategy-token receipt and records the request in three places: `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][epoch]`, and `instantWithdrawClaimsByEpoch[epoch]`, plus the global `pendingInstantWithdraws` unfunded counter [1](#0-0) .

The funds that actually back the receipt only arrive later, when the manager calls `getInstantWithdrawFunds` on the CDO after `instantWithdrawDelay`, which invokes `collectInstantWithdrawFunds` to pull underlying from the CDO and decrement `pendingInstantWithdraws` [2](#0-1) .

The non-default claim path performs no funded check at all:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:380-393
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

`_transferFundedClaim` only guards the `defaultRecoveryReserve`; it does not verify the receipt's underlying was collected [3](#0-2) . The CDO-side entry point only checks `allowInstantWithdraw`, not funding status [4](#0-3) .

Two invariant breaks follow:

1. **Double payout / LP drain.** The attacker calls `requestInstantWithdraw`, then `claimInstantWithdrawRequest` in the same epoch *before* `getInstantWithdrawFunds`. The strategy pays the full `amount` out of underlying it holds (prefunded amounts for other requests, returned borrower funds, reserve), while `pendingInstantWithdraws` still contains the receipt. When the manager later calls `getInstantWithdrawFunds`, `collectInstantWithdrawFunds` pulls the same amount from the CDO a second time. Net effect: the attacker is paid once from other users' backing and the receipt is still treated as pending — the equivalent of accessing an uninitialized/invalid pointer and being charged twice for one obligation.

2. **Stale per-epoch records corrupt default recovery.** The non-default claim only zeroes `instantWithdrawsRequests[_user]`; `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` remain populated (compare with `_claimDefaultedInstantWithdrawRequest`, which clears all three [5](#0-4) ). If the borrower subsequently defaults in that epoch and `finalizeDefaultRecovery` runs, `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0` [6](#0-5) , and `_defaultPrefundedInstantReserve` counts `instantBasis - pendingInstant` as already-held reserve even though that underlying was already paid to the attacker [7](#0-6) . The reserve and `defaultRecoveryPrice` are therefore overstated [8](#0-7) , and when the attacker's stale per-epoch entry is re-read by `_claimDefaultedInstantWithdrawRequest`, `instantWithdrawsRequests[_user] -= claimBasis` underflows and reverts — while `_transferDefaultRecovery` decrements a reserve whose underlying does not exist, so later legitimate claimants' transactions revert on the token transfer.

### Impact Explanation
- Direct theft/insolvency: one instant receipt is paid out twice (once from strategy-held underlying belonging to active LPs and other claimants, once via `collectInstantWithdrawFunds`), up to the attacker's full deposit size.
- Permanent freezing of funds: after default finalization, the inflated `defaultRecoveryPrice`/`defaultRecoveryReserve` and the underflowing stale per-epoch entry make `claimInstantWithdrawRequest`/`claimWithdrawRequest` revert for the affected epoch, permanently locking recovery funds owed to honest users — the "one receipt one payout" and "default recovery reserve" invariants are both broken.

### Likelihood Explanation
- Attacker is any KYC-passed tranche holder; instant withdrawals are a normal user flow (`requestInstantWithdraw` requires no role).
- The only precondition is an underlying balance in the strategy at claim time (prefunded instant cash, borrower-send returns, or collected funds), which is the normal state while `instantWithdrawDelay` has not elapsed and `getInstantWithdrawFunds` has not run.
- Existing guards do not stop it: `_onlyIdleCDO` is satisfied via `IdleCDOEpochVariant.claimInstantWithdrawRequest`; the `defaultRecoveryReserve` check is vacuous pre-default; no flag ties a receipt to its funding status; the epoch-gating used for normal withdraws (`epochNumber <= lastWithdrawRequest`) is absent for instant claims.
- Uncertainty: whether a deployed CDO configuration leaves non-trivial underlying in the strategy during the delay window; the loss size is bounded by claimable instant receipts and strategy-held cash, not total TVL.

### Recommendation
- Gate the non-default claim on funding: track a funded-instant counter alongside `pendingInstantWithdraws` (or reuse it) and in `claimInstantWithdrawRequest` only pay `min(instantWithdrawsRequests[_user], fundedAmount)`, reverting or skipping the unfunded remainder instead of paying from shared balance.
- In the non-default claim path, clear `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and decrement `instantWithdrawClaimsByEpoch[epochNumber]` symmetrically with `_claimDefaultedInstantWithdrawRequest`, so per-epoch and aggregate records cannot diverge.
- Alternatively, move the funding check into `_transferFundedClaim` (e.g., a `claimableInstantBalance` reserve bucket credited by `collectInstantWithdrawFunds`), mirroring how `defaultRecoveryReserve` is isolated.

### Proof of Concept
Foundry fork sketch (modeled on `test/foundry/IdleCreditVault.t.sol` patterns, e.g. `testFinalizeDefaultHaircutsPendingInstantRedeems` [9](#0-8) ):

```solidity
// Epoch 0: attacker and victim deposit AA; instant withdraws enabled with delay.
_startEpochAndCheckPrices(0);
_stopEpochAndCheckPrices(0, apr, expectedFunds);

// Attacker requests instant withdraw of full position.
vm.prank(attacker);
uint256 basis = cdoEpoch.requestWithdraw(0, address(AAtranche));

// Epoch 1 runs; manager has NOT yet called getInstantWithdrawFunds.
_startEpochAndCheckPrices(1);
assertGt(strategy.pendingInstantWithdraws(), 0);      // still unfunded
assertGt(underlying.balanceOf(address(strategy)), 0); // e.g. other prefunded cash

// BUG 1: claim succeeds despite zero funding for this receipt.
uint256 balPre = underlying.balanceOf(attacker);
vm.prank(attacker);
cdoEpoch.claimInstantWithdrawRequest();
assertEq(underlying.balanceOf(attacker) - balPre, basis); // paid from LP backing
assertGt(strategy.pendingInstantWithdraws(), 0);          // still counted pending

// Manager later collects the same amount again -> receipt paid twice.
vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
vm.prank(manager);
cdoEpoch.getInstantWithdrawFunds(); // pulls `basis` from CDO a second time

// BUG 2: borrower defaults; stale per-epoch record poisons recovery.
_checkDefault();
vm.prank(manager);
cdoEpoch.finalizeDefault(recovered, manager);
// defaultRecoveryReserve inflated by instantBasis - pendingInstant for funds
// already paid to attacker; attacker's own claim now reverts on
// instantWithdrawsRequests[user] -= claimBasis underflow, and honest users'
// recovery claims revert on safeTransfer when reserve exceeds real balance.
vm.prank(victim);
vm.expectRevert();
cdoEpoch.claimInstantWithdrawRequest();
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L685-692)
```text
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
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

**File:** contracts/IdleCDOEpochVariant.sol (L975-979)
```text
  function claimInstantWithdrawRequest() external {
    // Check that instant withdraws are available
    _checkNotAllowed(!allowInstantWithdraw);
    IdleCreditVault(strategy).claimInstantWithdrawRequest(msg.sender);
  }
```

**File:** test/foundry/IdleCreditVault.t.sol (L4435-4483)
```text
  function testFinalizeDefaultHaircutsPendingInstantRedeems() external {
    uint256 instantDelay = cdoEpoch.instantWithdrawDelay();
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(instantDelay, 1000, false);

    address instantUser = makeAddr('default-instant-user');
    uint256 amount = 10_000 * ONE_SCALE;
    uint256 recoveryRatio = 7e17;

    _depositWithUser(instantUser, amount, true);

    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr / 2, _expectedFundsEndEpoch());

    vm.prank(instantUser);
    uint256 instantClaimBasis = cdoEpoch.requestWithdraw(0, address(AAtranche));

    IdleCreditVault creditVault = IdleCreditVault(address(strategy));
    _startEpochAndCheckPrices(1);
    uint256 pendingInstant = creditVault.pendingInstantWithdraws();
    assertLt(pendingInstant, instantClaimBasis, 'instant request should be partially prefunded before default');
    uint256 prefundedInstant = instantClaimBasis - pendingInstant;

    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    vm.prank(manager);
    cdoEpoch.getInstantWithdrawFunds();
    _checkDefault();

    uint256 activeBasis = cdoEpoch.getContractValue() + cdoEpoch.expectedEpochInterest() - cdoEpoch.pendingWithdrawFees();
    uint256 totalBasis = activeBasis + creditVault.defaultPendingClaimBasis();
    uint256 targetReserve = totalBasis * recoveryRatio / ONE_TRANCHE;
    uint256 recovered = targetReserve - prefundedInstant;
    deal(defaultUnderlying, manager, recovered);
    vm.startPrank(manager);
    IERC20Detailed(defaultUnderlying).approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();
    assertApproxEqAbs(creditVault.defaultRecoveryReserve(), targetReserve, 5, 'prefunded instant not reserved');

    uint256 balPre = IERC20Detailed(defaultUnderlying).balanceOf(instantUser);
    vm.prank(instantUser);
    cdoEpoch.claimInstantWithdrawRequest();
    assertApproxEqAbs(
      IERC20Detailed(defaultUnderlying).balanceOf(instantUser) - balPre,
      instantClaimBasis * recoveryRatio / ONE_TRANCHE,
      5,
      'instant request was not haircut by recovery ratio'
    );
  }
```
