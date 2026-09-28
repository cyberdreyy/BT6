### Title
Unprivileged queued dust deposit permanently bricks `stopEpochWithDuration` on the prefunded variant, freezing all pool funds - ([File: contracts/IdleCDOEpochQueue.sol](contracts/IdleCDOEpochQueue.sol))

### Summary
`IdleCDOEpochVariantPrefunded` disables the direct `stopEpoch` selector when a queue is configured (`_beforeStopEpoch` reverts on `msg.sig == this.stopEpoch.selector`), so `stopEpochWithDuration` is the only way to stop a running epoch [1](#0-0) . At the end of that flow, `_afterStopEpochWithDuration` calls `prefundedDepositsToProcess()` on the queue, which reverts if `epochPendingDeposits[_epoch] != 0` [2](#0-1) [3](#0-2) . A KYC-passing attacker can permanently guarantee that condition with a 1-wei `requestDeposit`, mirroring the Xen bug where an unprivileged guest triggers a never-implemented path that hits a fatal `BUG()` — here a guaranteed `NotAllowed` revert in the only epoch-settlement path.

### Finding Description
The analog to CVE-2018-15469 (unprivileged request to an unsupported/unhandled state causes a fatal check) is the prefunded-queue settlement invariant: "stop must not run while raw pending deposits sit in the queue".

Attack sequence, epoch running, prefunded AA queue enabled, `prefundedDepositWindow = W`:

1. Attacker (any `isWalletAllowed`/KYC-passing EOA) calls `queue.requestDeposit(1)` at time `T` chosen so `T + W < epochEndDate` but after the manager's last feasible prefunding window — i.e., in the last valid block(s) before the cutoff [4](#0-3) .
2. `epochPendingDeposits[epochNumber+1] = 1` is now set. Clearing it requires either `processDepositsToBorrower()` — which reverts once `block.timestamp + W >= epochEndDate` [5](#0-4)  — or the attacker's own `deleteRequest`, which never comes [6](#0-5) . `processDeposits()` (the buffer-period path) is disabled on prefunded queues [7](#0-6) .
3. After `epochEndDate`, the manager calls `stopEpochWithDuration`. `_stopEpoch` may fully succeed — borrower repays, accounting updates, `isEpochRunning = false` — but `_afterStopEpochWithDuration()` then calls `prefundedDepositsToProcess()`, which reverts on `epochPendingDeposits != 0`, reverting the entire transaction [8](#0-7) .
4. This revert is permanent and unrepeatable: every subsequent `stopEpochWithDuration` hits `_checkNotAllowed(!isEpochRunning ...)` or the same queue revert, direct `stopEpoch` is selector-blocked, and no honest actor can remove the dust pending deposit.

Because the epoch can never be stopped, `isEpochRunning` stays true: withdrawal requests stay disabled (`allowAAWithdrawRequest`/`allowBBWithdrawRequest` were set false in `startEpoch`), deposits stay paused, and the borrower is never asked to return principal [9](#0-8) .

### Impact Explanation
Permanent freezing of all LP funds in the pool (AA and BB tranches and any queued withdrawal claims), triggered by an unprivileged user with a dust deposit costing ~1 wei of underlying plus gas. The broken invariant is liveness of the epoch state machine: a single pending-queue dust amount converts a settlement-time consistency check into an irreversible `BUG()`-style abort of the only stop path.

### Likelihood Explanation
Requires the prefunded-queue configuration and a non-zero `prefundedDepositWindow`, plus a deposit landing in the tail of the window so the manager cannot react (atomic same-block deposit at the cutoff boundary is sufficient since manager prefunding must satisfy the same strict `<` cutoff check). Attacker is an ordinary KYC'd depositor, which is explicitly in scope. Even absent malice, the same bricking occurs accidentally whenever a queued deposit is never prefunded — the guard at `prefundedDepositsToProcess` turns an operational edge case into permanent fund freeze.

### Recommendation
Don't revert settlement on leftover pending deposits. In `prefundedDepositsToProcess`/`_afterStopEpochWithDuration`, treat residual `epochPendingDeposits[_epoch]` as refundable queue-held funds: leave them in the queue, mark the epoch so affected users can `deleteRequest`, and proceed with stop. Alternatively, let the manager sweep leftover pending deposits back to users (a `sweepPendingDeposits(epoch)` that iterates nothing on-chain but flags the epoch deletable), or revert `requestDeposit` once `block.timestamp + W` is within a margin large enough to guarantee a final prefunding call. The key fix: queue dust must never block `stopEpochWithDuration`.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

// Fork-style test against the deployed prefunded CDO + AA queue, or reuse
// the harness in test/foundry/IdleCDOEpochVariantPrefunded.t.sol.
function testDustDepositBricksStopEpoch() external {
    uint256 W = queue.prefundedDepositWindow();        // e.g. 5 days
    address attacker = makeAddr('attacker');

    // --- epoch is running (startEpoch already called by manager) ---
    // Attacker passes KYC and deposits 1 wei in the last valid block
    // before the prefunding cutoff: block.timestamp + W < epochEndDate
    vm.warp(cdoEpoch.epochEndDate() - W - 1);
    _kyc(attacker);
    deal(address(underlying), attacker, 1);
    vm.startPrank(attacker);
    underlying.approve(address(queue), 1);
    queue.requestDeposit(1);                            // succeeds
    vm.stopPrank();

    uint256 nextEpoch = strategy.epochNumber() + 1;
    assertEq(queue.epochPendingDeposits(nextEpoch), 1);

    // --- cutoff now passed: nobody can clear the pending deposit ---
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(EpochNotRunning_or_NotAllowed);     // processDepositsToBorrower fails
    queue.processDepositsToBorrower();

    // Borrower is honest and has approved full repayment
    uint256 toRepay = cdoEpoch.expectedEpochInterest() + strategy.pendingWithdraws();
    deal(address(underlying), strategy.borrower(), toRepay);
    vm.prank(strategy.borrower());
    underlying.approve(address(cdoEpoch), toRepay);

    // The ONLY stop path reverts forever on the queue invariant
    uint256 duration = cdoEpoch.epochDuration();
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector)); // from prefundedDepositsToProcess
    cdoEpoch.stopEpochWithDuration(10e18, 0, duration, 0);

    // Retryable? No: pending dust persists, direct stopEpoch is selector-blocked
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(10e18, 0);

    // Funds frozen: epoch running forever, withdraw requests disabled
    assertTrue(cdoEpoch.isEpochRunning());
    vm.prank(user);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.requestWithdraw(0, address(AAtranche));
}
```

### Citations

**File:** contracts/IdleCDOEpochVariantPrefunded.sol (L39-48)
```text
  function _beforeStopEpoch(bool _isClosing) internal view override returns (uint256 additionalPrincipal) {
    // `stopEpochWithDuration` calls `stopEpoch` internally, so only block the direct selector path.
    address _queue = epochQueue;
    if (_queue == address(0)) return additionalPrincipal;
    _checkNotAllowed(msg.sig == this.stopEpoch.selector);
    if (_isClosing) {
      uint256 nextEpoch = IdleCreditVault(strategy).epochNumber() + 1;
      additionalPrincipal = IIdleCDOEpochQueuePrefunded(_queue).epochPrefundedDeposits(nextEpoch);
    }
  }
```

**File:** contracts/IdleCDOEpochVariantPrefunded.sol (L72-89)
```text
  function _afterStopEpochWithDuration() internal override {
    address _queue = epochQueue;
    if (_queue == address(0)) return;

    IIdleCDOEpochQueuePrefunded _epochQueue = IIdleCDOEpochQueuePrefunded(_queue);
    uint256 _prefunded = _epochQueue.prefundedDepositsToProcess();
    if (_prefunded == 0) return;
    // A zero post-loss AA price cannot safely mint new shares into the same tranche token.
    _checkNotAllowed(priceAA == 0);

    // Prefunded deposits already reached the borrower, so they must join AA even if stop defaulted.
    // Mint tranche shares at the post-stop price and mirror the same amount in strategy tokens,
    // so the queue can later distribute shares to users at the epoch price.
    uint256 _prefundedMinted = _mintSharesAtCurrPrice(_prefunded, _queue, AATranche);
    IdleCreditVault(strategy).mintStrategyTokens(_prefunded);
    // Finalize the prefunded epoch in the queue by storing the epoch price and clearing state.
    _epochQueue.processPrefundedDeposits(_prefundedMinted);
  }
```

**File:** contracts/IdleCDOEpochQueue.sol (L103-127)
```text
  function requestDeposit(uint256 amount) external nonReentrant {
    // check if the wallet is allowed to deposit (ie epoch is running and keyring KYC completed)
    _checkAllowed(msg.sender);

    IdleCDOEpochVariant _cdo = IdleCDOEpochVariant(idleCDOEpoch);
    uint256 nextEpoch = IdleCreditVault(strategy).epochNumber() + 1;
    uint256 _prefundedWindow = prefundedDepositWindow;
    // Only the AA prefunded queue enforces a deposit cutoff for the next epoch.
    if (tranche == _cdo.AATranche() && _isPrefundedQueueEnabled()) {
      IdleCDOEpochVariantPrefunded(idleCDOEpoch).checkPrefunding(epochPendingDeposits[nextEpoch] + amount);
      // Once funds are prefunded, or once the subscription window is reached, the next epoch is closed.
      _checkNotAllowed(
        epochPrefundedDeposits[nextEpoch] != 0 || (
        _prefundedWindow != 0 && block.timestamp + _prefundedWindow >= _cdo.epochEndDate()
      ));
    }

    // get underlying tokens from user
    IERC20Detailed(underlying).safeTransferFrom(msg.sender, address(this), amount);
    // deposit will be made in the next buffer period (ie next epoch)
    // updated user queued amount for the next epoch
    userDepositsEpochs[msg.sender][nextEpoch] += amount;
    // update pending deposits
    epochPendingDeposits[nextEpoch] += amount;
  }
```

**File:** contracts/IdleCDOEpochQueue.sol (L158-179)
```text
    // prefunding can be done only while epoch is running
    if (!_cdo.isEpochRunning()) {
      revert EpochNotRunning();
    }
    // Keep prefunding aligned with the same cutoff enforced for new queued deposits.
    uint256 _prefundedWindow = prefundedDepositWindow;
    _checkNotAllowed(_prefundedWindow == 0 || block.timestamp + _prefundedWindow < _cdo.epochEndDate());

    uint256 _epoch = _strategy.epochNumber() + 1;
    // prefunding can happen only once for an epoch
    _checkNotAllowed(epochPrefundedDeposits[_epoch] != 0);
    uint256 _pending = epochPendingDeposits[_epoch];
    if (_pending == 0) {
      return;
    }
    // Recheck after the cutoff because emergency state or the TVL limit can change while queued.
    IdleCDOEpochVariantPrefunded(idleCDOEpoch).checkPrefunding(_pending);

    // Switch the epoch from "queue-held" to "already at borrower" before transferring funds.
    epochPendingDeposits[_epoch] = 0;
    epochPrefundedDeposits[_epoch] = _pending;
    IERC20Detailed(underlying).safeTransfer(_strategy.borrower(), _pending);
```

**File:** contracts/IdleCDOEpochQueue.sol (L184-199)
```text
  function deleteRequest(uint256 _requestEpoch) external {
    // if the epoch price is already set, deposits were already processed so
    // the deposit request can't be deleted.
    _checkNotAllowed(epochPrice[_requestEpoch] != 0 || epochPrefundedDeposits[_requestEpoch] != 0);

    uint256 amount = userDepositsEpochs[msg.sender][_requestEpoch];
    if (amount == 0) {
      return;
    }
    // reset user deposit for the epoch
    userDepositsEpochs[msg.sender][_requestEpoch] = 0;
    // update pending deposits
    epochPendingDeposits[_requestEpoch] -= amount;
    // transfer underlyings back to the user
    IERC20Detailed(underlying).safeTransfer(msg.sender, amount);
  }
```

**File:** contracts/IdleCDOEpochQueue.sol (L222-226)
```text
  function processDeposits() external {
    // only owner or strategy manager can process deposits
    _checkOnlyOwnerOrManager();
    // prefunded-enabled queues do not use the old buffer-period processing path
    _checkNotAllowed(_isPrefundedQueueEnabled());
```

**File:** contracts/IdleCDOEpochQueue.sol (L277-282)
```text
  function prefundedDepositsToProcess() external view returns (uint256 _prefunded) {
    uint256 _epoch = _prefundedEpochToProcess(IdleCDOEpochVariant(idleCDOEpoch));
    _prefunded = epochPrefundedDeposits[_epoch];
    // Prefunded queues must not reach stopEpochWithDuration with raw underlyings still sitting in the queue.
    _checkNotAllowed(epochPendingDeposits[_epoch] != 0);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L244-250)
```text
    isEpochRunning = true;
    // prevent deposits
    _pause();

    // prevent withdrawals requests
    allowAAWithdrawRequest = false;
    allowBBWithdrawRequest = false;
```

**File:** contracts/IdleCDOEpochVariant.sol (L520-530)
```text
  function stopEpochWithDuration(uint256 _newApr, uint256 _interest, uint256 _duration, uint256 _lossAmount) public {
    // stop epoch checks that msg.sender is allowed
    _stopEpoch(_newApr, _interest, _lossAmount);
    if (_interest != 1 && !defaulted) {
      // buffer period is not changed
      setEpochParams(_duration, bufferPeriod);
      // scale the apr with the new duration and buffer
      _setScaledApr(_newApr);
    }
    _afterStopEpochWithDuration();
  }
```
