### Title
Dust `requestDeposit` to the AA prefunded queue permanently bricks `stopEpochWithDuration`, freezing all pool funds - (File: `contracts/IdleCDOEpochQueue.sol`)

### Summary
The Samba CVE is an authenticated user repeatedly crashing a shared service. The closest analog on idle-tranches is an unprivileged (KYC-passing) lender crashing the shared epoch-stop path: a single dust deposit left in `epochPendingDeposits` makes `IdleCDOEpochVariantPrefunded._afterStopEpochWithDuration` revert every time via `prefundedDepositsToProcess()`, permanently preventing `stopEpochWithDuration` from completing.

### Finding Description
`IdleCDOEpochQueue.prefundedDepositsToProcess()` hard-reverts whenever the epoch being settled still has raw pending deposits: [1](#0-0) 

This view is called unconditionally from `IdleCDOEpochVariantPrefunded._afterStopEpochWithDuration()` on every stop while a queue is configured: [2](#0-1) 

The only way to move `epochPendingDeposits` into `epochPrefundedDeposits` is `processDepositsToBorrower()`, which requires `prefundedDepositWindow != 0` and the cutoff already reached — it reverts with `NotAllowed` when the window is zero: [3](#0-2) 

Meanwhile `requestDeposit()` accepts deposits from any allowed wallet while the epoch is running, and when `prefundedDepositWindow == 0` no cutoff ever blocks new deposits (the `_prefundedWindow != 0` clause fails): [4](#0-3) 

So on a prefunded deployment where the owner never sets `prefundedDepositWindow` (its default is 0), an attacker deposits 1 wei (or 1 unit of USDC) into the AA queue. No privileged call can ever clear `epochPendingDeposits` — `processDepositsToBorrower` always reverts with window 0, `processDeposits` reverts on prefunded-enabled queues, and the attacker's own `deleteRequest` is voluntary. Every subsequent `stopEpochWithDuration` (and `stopEpoch`, which is blocked anyway by `_beforeStopEpoch` when a queue is set) reverts, so the epoch can never be stopped.

### Impact Explanation
Permanent freezing of funds. The epoch can never be stopped, so the borrower's repayment can never be accounted, `epochEndDate` passes with `isEpochRunning` stuck true, and deposits/withdrawals remain paused (`_pause()` in `startEpoch`, `_beforeUnpause` blocks unpause while running). The only recovery is `setEpochQueue(0)`, which abandons the queue: every queued depositor's underlyings are stranded forever because `claimDepositRequest` requires `epochPrice[_epoch] != 0`, which can only be set by `processDeposits`/`processPrefundedDeposits` — both unreachable. Quantified loss: all honest queued deposits plus continued freeze of the entire pool NAV until emergency default finalization crystallizes a full loss.

### Likelihood Explanation
Requires only: prefunded variant deployed, `setEpochQueue` called, and `prefundedDepositWindow` left at 0 — a plausible configuration since the window is optional ("0 = no cutoff" semantics) and deposit acceptance works fine without it. Attacker cost is a dust deposit; KYC-passing lenders are in-scope attackers.

### Recommendation
Allow `processDepositsToBorrower` when `prefundedDepositWindow == 0` (e.g., treat window 0 as "prefundable at any time"), or make `prefundedDepositsToProcess` treat leftover pending deposits as forfeitable/skimmable rather than reverting, so a stray queued deposit cannot veto the epoch state machine.

### Proof of Concept
```solidity
// Prefunded variant deployed; manager calls setEpochQueue(queue);
// prefundedDepositWindow is left at 0. Epoch is running.

// Attacker: KYC-passed wallet deposits dust into AA queue
deal(address(underlying), attacker, 1);
vm.startPrank(attacker);
underlying.approve(address(queue), 1);
queue.requestDeposit(1);              // epochPendingDeposits[nextEpoch] = 1
vm.stopPrank();

// Manager tries to prefund it to the borrower -> always reverts (window == 0)
vm.prank(manager);
vm.expectRevert(NotAllowed.selector);
queue.processDepositsToBorrower();

// Epoch ends; manager tries to stop -> _afterStopEpochWithDuration ->
// prefundedDepositsToProcess reverts because epochPendingDeposits != 0
vm.warp(cdoEpoch.epochEndDate() + 1);
vm.prank(manager);
vm.expectRevert(NotAllowed.selector);
cdoEpoch.stopEpochWithDuration(apr, 0, duration, 0);
// Repeatable forever: epoch never stops, LP funds frozen.
```

### Citations

**File:** contracts/IdleCDOEpochQueue.sol (L103-126)
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
```

**File:** contracts/IdleCDOEpochQueue.sol (L162-179)
```text
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

**File:** contracts/IdleCDOEpochQueue.sol (L277-282)
```text
  function prefundedDepositsToProcess() external view returns (uint256 _prefunded) {
    uint256 _epoch = _prefundedEpochToProcess(IdleCDOEpochVariant(idleCDOEpoch));
    _prefunded = epochPrefundedDeposits[_epoch];
    // Prefunded queues must not reach stopEpochWithDuration with raw underlyings still sitting in the queue.
    _checkNotAllowed(epochPendingDeposits[_epoch] != 0);
  }
```

**File:** contracts/IdleCDOEpochVariantPrefunded.sol (L72-80)
```text
  function _afterStopEpochWithDuration() internal override {
    address _queue = epochQueue;
    if (_queue == address(0)) return;

    IIdleCDOEpochQueuePrefunded _epochQueue = IIdleCDOEpochQueuePrefunded(_queue);
    uint256 _prefunded = _epochQueue.prefundedDepositsToProcess();
    if (_prefunded == 0) return;
    // A zero post-loss AA price cannot safely mint new shares into the same tranche token.
    _checkNotAllowed(priceAA == 0);
```
