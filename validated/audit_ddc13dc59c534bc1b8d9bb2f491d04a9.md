### Title
Unprefunded dust deposit permanently freezes `stopEpochWithDuration` on prefunded pools - (File: contracts/IdleCDOEpochQueue.sol)

### Summary

Analogous to the external bug — an expected "nothing queued to settle" state is treated as a fatal revert instead of a graceful no-op — `IdleCDOEpochQueue.prefundedDepositsToProcess()` reverts whenever `epochPendingDeposits` for the settling epoch is non-zero, even when there are zero prefunded deposits to settle. Since `_afterStopEpochWithDuration()` calls it unconditionally on every `stopEpochWithDuration`, a single stranded pending deposit bricks epoch finalization. An unprivileged KYC-passed lender can strand a dust deposit past the prefunding cutoff and permanently freeze the epoch — and with it, every other lender's funds.

### Finding Description

In the prefunded variant, every stop settles the queue via `IdleCDOEpochVariantPrefunded._afterStopEpochWithDuration`, which calls `prefundedDepositsToProcess()` before checking whether there is anything to settle: [1](#0-0) 

`prefundedDepositsToProcess` reverts on *any* leftover pending deposit, even when `_prefunded == 0` and there is nothing to process — the "empty queue → hard error" pattern rather than an EOF-style no-op: [2](#0-1) 

The revert propagates out of `stopEpochWithDuration` (line 529), which runs the hook after `_stopEpoch` and `setEpochParams`, so the whole transaction reverts: [3](#0-2) 

How the attacker strands a deposit:

1. During a running epoch on a prefunded-enabled AA queue, a KYC-passed attacker calls `requestDeposit(dust)` at any time before `epochEndDate - prefundedDepositWindow`. `requestDeposit` accepts it — `checkPrefunding` passes and the cutoff check hasn't tripped yet: [4](#0-3) 

2. Once `block.timestamp + prefundedDepositWindow >= epochEndDate`, `processDepositsToBorrower` can never move that deposit to the borrower — its cutoff check reverts (line 164). `processDeposits` is also permanently disabled for prefunded queues (line 226). The deposit is now structurally stranded in `epochPendingDeposits[nextEpoch]` and can only be removed by the attacker voluntarily calling `deleteRequest` — which they never do.

3. After `epochEndDate`, every `stopEpochWithDuration` call reverts inside `prefundedDepositsToProcess`. Note `stopEpoch` reverts too: `_beforeStopEpoch` only blocks the *direct selector* when a queue is set (line 43), and even an internal stop that succeeds still runs `_afterStopEpochWithDuration` — there is no stop path that skips the queue check.

### Impact Explanation

Permanent freezing of all vault funds. With `isEpochRunning` never clearable, withdraw requests cannot be processed or claimed (`processWithdrawRequests`/`claimWithdrawRequest` depend on epoch finalization), borrower funds cannot be recalled via close-pool (`_interest == 1` still reaches `_afterStopEpochWithDuration`), and no new epoch can start. The entire pool NAV is locked by a dust deposit worth a fraction of a cent. Broken invariant: an unprivileged queue user must not be able to veto epoch finalization.

### Likelihood Explanation

Trivially executable by any KYC-passed wallet with no capital at risk: deposit 1 wei of underlying late in the subscription window, then do nothing. The honest manager cannot remediate — `processDepositsToBorrower` reverts after the cutoff, `processDeposits` is disabled on prefunded queues, and `deleteRequest` only clears the caller's own request. There is no privileged escape hatch, so the freeze is deterministic once the deposit is stranded. Even non-malicious sequencing (a user depositing just before the cutoff while the manager's prefunding call arrives just after) triggers it, making the failure mode reachable by accident as well.

### Recommendation

Apply the report's fix pattern — return instead of erroring on the empty/nothing-to-settle state, and handle leftovers gracefully:

- In `prefundedDepositsToProcess`, do not revert on `epochPendingDeposits[_epoch] != 0` when there is nothing prefunded to settle; treat leftover queue-held deposits as unprocessable for this epoch (e.g., roll them into the next epoch's `epochPendingDeposits`, or leave them claimable via `deleteRequest`) rather than blocking the stop.
- Alternatively, let `stopEpochWithDuration` force-sweep `epochPendingDeposits[_epoch]` into a refundable state (users keep `deleteRequest`) so a stranded deposit degrades to a per-user problem instead of a pool-wide freeze.

### Proof of Concept

Foundry fork PoC sketch (modeled on `test/foundry/IdleCDOEpochQueue.t.sol`):

```solidity
function testStrandedDustDepositFreezesStopEpoch() external {
    address attacker = makeAddr('attacker');
    uint256 dust = 1;

    // enable prefunded queue with a cutoff window
    vm.prank(manager);
    IdleCDOEpochVariantPrefunded(address(cdoEpoch)).setEpochQueue(address(queue));
    vm.prank(manager);
    queue.setPrefundedDepositWindow(PREFUNDED_DEPOSIT_WINDOW);

    // attacker (KYC-passed) queues a dust deposit just before the cutoff
    deal(address(underlying), attacker, dust);
    _enterJustBeforePrefundedWindow(PREFUNDED_DEPOSIT_WINDOW); // ts + window < epochEndDate
    vm.startPrank(attacker);
    underlying.approve(address(queue), dust);
    queue.requestDeposit(dust);
    vm.stopPrank();

    // cutoff passes: manager can no longer prefund the stranded dust
    _enterPrefundedWindow(PREFUNDED_DEPOSIT_WINDOW);
    vm.prank(manager);
    vm.expectRevert(NotAllowed.selector);
    queue.processDepositsToBorrower();

    // epoch ends; every stop path reverts in prefundedDepositsToProcess
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.stopEpochWithDuration(NEW_APR, 0, EPOCH_DURATION, 0);

    // still running — funds frozen
    assertTrue(cdoEpoch.isEpochRunning());
}
```

Key assertions: `queue.epochPendingDeposits(strategy.epochNumber() + 1) == 1` persists, no honest-role call can clear it, and `stopEpochWithDuration` reverts indefinitely — permanent freeze of all lender funds caused by 1 wei of unprivileged input.

### Citations

**File:** contracts/IdleCDOEpochVariantPrefunded.sol (L76-88)
```text
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
```

**File:** contracts/IdleCDOEpochQueue.sol (L103-117)
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
