### Title
Attacker can keep `pendingInstantWithdraws` non-zero to indefinitely block `stopEpoch` - ([File: contracts/IdleCDOEpochVariant.sol])

### Summary
`_stopEpoch` reverts whenever `_pendingInstant() != 0` (`contracts/IdleCDOEpochVariant.sol:345`), and the only way to drain that counter is the manager-only `getInstantWithdrawFunds()` (`IdleCDOEpochVariant.sol:558-574`), which collects the *current* `pendingInstantWithdraws` from the borrower. Any tranche holder can re-inflate `pendingInstantWithdraws` via `requestWithdraw` → `IdleCreditVault.requestInstantWithdraw`, which does `pendingInstantWithdraws += _amount` with no minimum (`contracts/strategies/idle/IdleCreditVault.sol:356-375`). This is the exact analog of the Polynomial `_placeDelayedOrder`/`queuedPerpSize` grief: the privileged execution only drains the present queue, and an unprivileged user can post-run it with a dust-sized action to keep the queue permanently non-empty.

### Finding Description
In an epoch where instant withdrawals are enabled (new APR sufficiently lower), the sequence is:

1. Instant withdraw requests accrue during the epoch; `pendingInstantWithdraws > 0`.
2. After `instantWithdrawDeadline`, the manager calls `getInstantWithdrawFunds()`, which pulls `_pendingInstant()` from the borrower and calls `collectInstantWithdrawFunds`, setting `pendingInstantWithdraws` to 0 (`IdleCreditVault.sol:398-403`).
3. The manager must wait until `epochEndDate` to call `stopEpoch`. During that window (or by frontrunning a bundled `getInstantWithdrawFunds` + `stopEpoch` at `epochEndDate`), the attacker — any KYC-passing lender holding even a dust amount of tranche tokens — calls `cdoEpoch.requestWithdraw(0, tranche)` (instant request for their whole balance, per the `requestWithdraw(0, ...)` pattern in `test/foundry/IdleCreditVault.t.sol:2784`).
4. `requestInstantWithdraw` mints the receipt and increments `pendingInstantWithdraws` (`IdleCreditVault.sol:366-374`), so `stopEpoch`'s `_pendingInstant() != 0` check reverts (`IdleCDOEpochVariant.sol:345`).
5. The attacker repeats step 3 after every `getInstantWithdrawFunds` call with negligible cost (dust tranche balance, which is returned as a claimable receipt anyway).

There is no atomic fix available to the honest manager: the funding call and the epoch stop are separated by the `epochEndDate` gate, so the attacker always has a window to re-inflate the queue.

### Impact Explanation
Temporary freezing of all user funds. While `pendingInstantWithdraws != 0`, the epoch cannot be stopped: no interest accrual settlement, no buffer period, no withdraw-request processing through `IdleCDOEpochQueue.processWithdrawRequests`/`claimWithdrawRequest`, and the pool cannot transition epochs or close (`_interest == 1` close is also blocked by the same check). Loss is bounded by the duration of the griefing, but it is renewable indefinitely at near-zero cost, and it delays every queued withdrawal in `IdleCDOEpochQueue`.

### Likelihood Explanation
Requires (a) an epoch operating in instant-withdraw mode and (b) an attacker holding any amount of tranche tokens (any KYC'd lender or tranche holder qualifies; if secondary transfer isn't KYC-gated the barrier is even lower). The attack is cheap and repeatable; whether it is sustained depends on attacker motivation (e.g., delaying a competitor's exit or the epoch transition).

### Recommendation
Two options, mirroring the upstream fix of folding the queue into the next submission:
- Allow `stopEpoch` to first collect any residual `pendingInstantWithdraws` in the same transaction (pull the delta from the borrower inside `_stopEpoch` before the check), so there is no gap in which the queue can be re-inflated; or
- Disallow new instant withdraw requests once `instantWithdrawDeadline` has passed (or in the same block as / after `getInstantWithdrawFunds`), since such requests can no longer be funded within the epoch anyway.

### Proof of Concept
```solidity
// test/foundry/InstantWithdrawGrief.t.sol — fork of the existing IdleCreditVault setup
function testGriefStopEpochWithDustInstantRequest() external {
    uint256 amount = 10_000 * ONE_SCALE;
    idleCDO.depositAA(amount);
    idleCDO.depositBB(amount);
    _startEpochAndCheckPrices(0);

    // stop epoch with lower APR so instant withdrawals are enabled next epoch
    _stopEpochAndCheckPrices(0, initialProvidedApr / 4, _expectedFundsEndEpoch());

    // victim requests a normal-sized instant withdraw
    uint256 victimAA = IERC20(AAtranche).balanceOf(address(this));
    cdoEpoch.requestWithdraw(victimAA / 2, address(AAtranche));

    _startEpochAndCheckPrices(1);
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    _getInstantFunds(); // pendingInstantWithdraws == 0 now

    // attacker (dust tranche holder) re-inflates the queue
    address attacker = makeAddr('attacker');
    _depositWithUser(attacker, 1e6, true); // dust deposit -> dust tranche balance
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(AAtranche)); // instant request of full dust bal

    // manager cannot stop the epoch anymore
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    // manager retries funding; attacker frontruns/post-runs again — repeat ad infinitum
}
```

Relevant code: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

Caveat: I could not fully verify the exact gating inside `IdleCDOEpochVariant.requestWithdraw` (allow/flag checks for instant requests during the running epoch) before the iteration limit, but the existing tests (`testGetInstantWithdrawFunds`, `testClaimInstantWithdrawRequest`) confirm instant requests are permissionless for tranche holders while the epoch is running.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L338-351)
```text
    _checkNotAllowed(
      // Check that epoch is running
      !isEpochRunning || 
      // Check that end date is passed
      block.timestamp < epochEndDate || 
      // Check that there are no pending instant withdraws, ie `getInstantWithdrawFunds` was called
      // before closing the epoch
      _pendingInstant() != 0 ||
      // Check that overridden interest, if passed (ie > 1), is greater than pending withdraw fees and the apr is 0 
      // otherwise withdrawal requests may not be fullfilled as they consider also the interest gained in the next epoch 
      (_interest > 1 && (_interest < _pendingWithdrawFees || _newApr != 0)) ||
      // Closing already recalls all principal, so applying a separate loss burn would strand returned cash.
      (_isRequestingAllFunds && _lossAmount != 0)
    );
```

**File:** contracts/IdleCDOEpochVariant.sol (L558-574)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-403)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
  }
```
