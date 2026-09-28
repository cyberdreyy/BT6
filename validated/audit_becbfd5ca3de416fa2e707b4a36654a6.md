### Title
Griefing freeze of `stopEpochWithDuration` via dust queue deposit when prefunded deposit window is zero - ([File: contracts/IdleCDOEpochQueue.sol])

### Summary
The restify-paginate DoS (a missing edge input crashing the whole service) maps onto the prefunded epoch queue: a single stray queued deposit that was never prefunded makes `prefundedDepositsToProcess()` revert inside `_afterStopEpochWithDuration`, which bricks every `stopEpochWithDuration` call and freezes all pool funds.

### Finding Description
`requestDeposit` only enforces the deposit cutoff when `prefundedDepositWindow != 0`: the guard `_prefundedWindow != 0 && block.timestamp + _prefundedWindow >= _cdo.epochEndDate()` is skipped entirely when the window is `0` [1](#0-0) . Symmetrically, `processDepositsToBorrower` reverts whenever `_prefundedWindow == 0`, so with a zero window *no* queued deposit can ever be prefunded [2](#0-1) . The non-prefunded path `processDeposits` is also disabled for prefunded queues [3](#0-2) .

At epoch end, `_afterStopEpochWithDuration` calls `queue.prefundedDepositsToProcess()`, which reverts with `NotAllowed` whenever `epochPendingDeposits[epoch] != 0` [4](#0-3) [5](#0-4) . Since `stopEpoch` propagates that revert atomically, the epoch can never be stopped while any raw pending deposit exists. Only the depositor can clear their entry via `deleteRequest`, so the attacker simply never deletes [6](#0-5) .

### Impact Explanation
A KYC-passing lender deposits 1 wei of underlying into the AA prefunded queue during a running epoch. From that point every `stopEpoch`/`stopEpochWithDuration` reverts: the borrower cannot repay into the pool, withdraw receipts cannot be funded, `pendingWithdraws` stay locked, and all AA/BB TVL plus pending withdraw requests are frozen. Recovery requires the honest owner to call `setEpochQueue(address(0))`, which is a manual remediation path; absent it the freeze is permanent. Total funds at risk equal pool TVL (temporary freezing turning permanent if the queue cannot be safely detached once other epochs hold prefunded state).

### Likelihood Explanation
Requires `prefundedDepositWindow == 0` on a queue-enabled prefunded deployment — a plausible operational state (window optional, treated as "no cutoff"). The attacker's cost is 1 wei plus gas, and `requestDeposit`/`deleteRequest` give them full control over the blocking condition; no privileged cooperation is needed.

### Recommendation
When `_isPrefundedQueueEnabled()` is true and `prefundedDepositWindow == 0`, either revert `requestDeposit` (no queue deposits can ever be processed) or treat window `0` as "deposit until `epochEndDate` is reached" and force `processDepositsToBorrower` to be callable anytime before stop. Alternatively, make `prefundedDepositsToProcess`/`processPrefundedDeposits` tolerate leftover pending deposits (e.g., refund them via `deleteRequest`-style pull) instead of reverting inside the stop path.

### Proof of Concept
```solidity
// test/foundry/PrefundedQueueFreeze.t.sol — fork test, prefunded variant + queue configured
function testDustDepositFreezesStopEpoch() external {
    // prefunded variant with epochQueue set; manager leaves prefundedDepositWindow == 0
    address attacker = makeAddr('attacker'); // KYC-passing lender
    _whitelist(attacker);

    vm.prank(manager);
    cdoEpoch.startEpoch(); // epoch running, epochEndDate > now

    // window == 0 ⇒ requestDeposit cutoff skipped; attacker queues 1 wei
    deal(address(underlying), attacker, 1, true);
    vm.startPrank(attacker);
    underlying.approve(address(queue), 1);
    queue.requestDeposit(1);           // epochPendingDeposits[nextEpoch] = 1
    vm.stopPrank();

    // manager cannot prefund: processDepositsToBorrower reverts when window == 0
    vm.prank(manager);
    vm.expectRevert(NotAllowed.selector);
    queue.processDepositsToBorrower();

    // borrower repaid honestly; epoch end reached
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(address(underlying), borrower, fundsToRepay, true);
    vm.prank(borrower);
    underlying.approve(address(cdoEpoch), fundsToRepay);

    // every stopEpochWithDuration reverts in _afterStopEpochWithDuration ->
    // prefundedDepositsToProcess sees epochPendingDeposits != 0
    vm.prank(manager);
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.stopEpochWithDuration(apr, 0, duration, 0);

    // attacker refuses deleteRequest(1 wei) ⇒ TVL + pendingWithdraws frozen
    assertTrue(cdoEpoch.isEpochRunning()); // still running, epochNumber unchanged
}
```

### Citations

**File:** contracts/IdleCDOEpochQueue.sol (L113-118)
```text
      // Once funds are prefunded, or once the subscription window is reached, the next epoch is closed.
      _checkNotAllowed(
        epochPrefundedDeposits[nextEpoch] != 0 || (
        _prefundedWindow != 0 && block.timestamp + _prefundedWindow >= _cdo.epochEndDate()
      ));
    }
```

**File:** contracts/IdleCDOEpochQueue.sol (L162-165)
```text
    // Keep prefunding aligned with the same cutoff enforced for new queued deposits.
    uint256 _prefundedWindow = prefundedDepositWindow;
    _checkNotAllowed(_prefundedWindow == 0 || block.timestamp + _prefundedWindow < _cdo.epochEndDate());

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

**File:** contracts/IdleCDOEpochQueue.sol (L225-226)
```text
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

**File:** contracts/IdleCDOEpochVariantPrefunded.sol (L72-78)
```text
  function _afterStopEpochWithDuration() internal override {
    address _queue = epochQueue;
    if (_queue == address(0)) return;

    IIdleCDOEpochQueuePrefunded _epochQueue = IIdleCDOEpochQueuePrefunded(_queue);
    uint256 _prefunded = _epochQueue.prefundedDepositsToProcess();
    if (_prefunded == 0) return;
```
