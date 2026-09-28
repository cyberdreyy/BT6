### Title
`stopEpochWithDuration` permanently reverts when a prefunded AA queue has un-prefunded queued deposits, freezing all vault funds - (File: contracts/IdleCDOEpochVariantPrefunded.sol)

### Summary
When an `epochQueue` is configured on `IdleCDOEpochVariantPrefunded`, the direct `stopEpoch` selector is hard-blocked in `_beforeStopEpoch`, so `stopEpochWithDuration` is the only way to end an epoch. At the end of every `stopEpochWithDuration`, `_afterStopEpochWithDuration` unconditionally calls `prefundedDepositsToProcess()`, which reverts if `epochPendingDeposits` for the target epoch is non-zero — even when nothing was prefunded. Any KYC-passing user can deposit a dust amount into the AA queue during a running epoch and, if it is not prefunded (or cannot be prefunded because `prefundedDepositWindow == 0` makes `processDepositsToBorrower` itself revert), every subsequent `stopEpochWithDuration` reverts. The epoch can never be stopped, borrower repayment cannot be collected, and all LP funds are frozen until the owner intervenes — and disabling the queue mid-flight permanently strands already-prefunded deposits.

### Finding Description
`IdleCDOEpochVariantPrefunded._beforeStopEpoch` reverts whenever `epochQueue != 0` and the caller used the `stopEpoch` selector: [1](#0-0) 

`stopEpochWithDuration` reaches `_stopEpoch` internally and then always runs `_afterStopEpochWithDuration`, which calls `prefundedDepositsToProcess()` before checking whether there is anything to settle: [2](#0-1) 

`prefundedDepositsToProcess()` reverts if raw pending deposits exist for the epoch being settled: [3](#0-2) 

with `_prefundedEpochToProcess` resolving to `epochNumber + (defaulted ? 1 : 0)` — i.e. the same "nextEpoch" bucket that `requestDeposit` writes into while the epoch runs: [4](#0-3) [5](#0-4) 

Two facts make the guard unsatisfiable from inside the system:

1. `processDepositsToBorrower` reverts unconditionally when `prefundedDepositWindow == 0`, because `_checkNotAllowed(_prefundedWindow == 0 || ...)` treats a zero window as "never allowed". Meanwhile `requestDeposit` treats a zero window as "no cutoff" and accepts deposits until epoch end. So a queue deployed with the default window of 0 can accept deposits that can never be prefunded and never be cleared by the manager. [6](#0-5) [7](#0-6) 

2. Even with a nonzero window, once `block.timestamp + window >= epochEndDate`, `processDepositsToBorrower` reverts, so deposits queued late can never be converted to prefunded.

The result: `epochPendingDeposits[nextEpoch] != 0` → `prefundedDepositsToProcess()` reverts → `_afterStopEpochWithDuration` reverts → the whole `stopEpochWithDuration` transaction reverts → and `stopEpoch` is blocked by the selector check. There is no in-protocol path to stop the epoch.

### Impact Explanation
Any KYC-passing wallet (the only gate on `requestDeposit` is `isEpochRunning` and `isWalletAllowed`) deposits 1 wei of underlying into the AA prefunded queue during a running epoch. If the deposit is never prefunded — trivially guaranteed when `prefundedDepositWindow == 0`, or by depositing after the cutoff when the manager prefunded an earlier batch is impossible since prefunding blocks later deposits, so the zero-window path is the reliable vector — then at epoch end `stopEpochWithDuration` reverts on every call. All AA/BB LP principal and the borrower's repayment remain locked: no `stopEpoch`, no withdrawal processing (`requestWithdraw`/`claimWithdrawRequest` depend on epoch rollover), and queued-deposit refunds require each user to call `deleteRequest`, which does not help while the epoch cannot roll. Total frozen value equals the entire pool NAV plus pending repayments, i.e. temporary freezing of all funds for a 1-wei cost.

### Likelihood Explanation
The trigger is a single permissionless (KYC-gated) dust deposit. The `prefundedDepositWindow == 0` configuration is the default storage value and the documented semantic of the cutoff is "deposits accepted until `epochEndDate - window`", so a zero window plausibly means "no cutoff" — yet it silently makes `processDepositsToBorrower` unusable while deposits remain open. The only recovery is the owner calling `setEpochQueue(0)` to disable the queue, which the contract itself warns must not be done after deposits were already prefunded, and which abandons the prefunded settlement path entirely. This is an always-reachable wedge, not a race.

### Recommendation
- In `prefundedDepositsToProcess`/`_afterStopEpochWithDuration`, only revert on pending raw deposits when prefunded deposits for that epoch actually exist (`_prefunded != 0`), or sweep leftover `epochPendingDeposits` back into the claimable/refundable path instead of reverting.
- Make `prefundedDepositWindow == 0` semantics consistent: either treat 0 as "prefunding disabled" and also block `requestDeposit` for the prefunded AA queue, or treat 0 as "no cutoff" in `processDepositsToBorrower` (drop the `_prefundedWindow == 0` revert condition and keep only the timestamp check).
- Allow the direct `stopEpoch` selector when the queue has no unsettled prefunded or pending deposits, so a dusted queue cannot wedge the only epoch-stop entry point.

### Proof of Concept
Foundry fork PoC (IdleCDOEpochVariantPrefunded + AA `IdleCDOEpochQueue` wired via `setEpochQueue`, `prefundedDepositWindow == 0`):

```solidity
function testDustDepositBricksStopEpoch() external {
    // setup: prefunded variant, AA queue enabled, window = 0 (default)
    idleCDO.depositAA(100_000 * ONE_SCALE);
    idleCDO.depositBB(50_000 * ONE_SCALE);
    vm.prank(manager);
    cdoEpoch.startEpoch();

    // attacker: any KYC'd wallet deposits 1 wei into the AA queue mid-epoch
    address attacker = makeAddr('attacker');
    deal(defaultUnderlying, attacker, 1);
    vm.startPrank(attacker);
    underlying.approve(address(aaQueue), 1);
    aaQueue.requestDeposit(1);          // succeeds: window==0 => no cutoff check
    vm.stopPrank();

    // manager cannot prefund: window==0 makes processDepositsToBorrower revert
    vm.prank(manager);
    vm.expectRevert(NotAllowed.selector);
    aaQueue.processDepositsToBorrower();

    // epoch ends; borrower repays in full and approves
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, expectedFunds);
    vm.prank(borrower);
    underlying.approve(address(cdoEpoch), expectedFunds);

    // only available stop path reverts because epochPendingDeposits[nextEpoch] = 1
    vm.prank(manager);
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.stopEpochWithDuration(newApr, 0, newDuration, 0);

    // direct stopEpoch is blocked while a queue is configured
    vm.prank(manager);
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.stopEpoch(newApr, 0);

    assertTrue(cdoEpoch.isEpochRunning()); // epoch permanently unstoppable
}
```

Note on confidence: the revert chain is direct from the cited code. The residual question is whether reverting here is an intended hard-fail forcing operator cleanup via `setEpochQueue(0)`; if so, the impact degrades to a temporary freeze requiring owner intervention (which is itself flagged as unsafe once prefunding has occurred), but the window-0 inconsistency — deposits accepted that can never be prefunded or cleared — is a concrete defect regardless.

### Citations

**File:** contracts/IdleCDOEpochVariantPrefunded.sol (L39-47)
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
```

**File:** contracts/IdleCDOEpochVariantPrefunded.sol (L72-88)
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

**File:** contracts/IdleCDOEpochQueue.sol (L162-180)
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
  }
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

**File:** contracts/IdleCDOEpochQueue.sol (L288-291)
```text
  function _prefundedEpochToProcess(IdleCDOEpochVariant _cdo) internal view returns (uint256 _epoch) {
    // Prefunded deposits still belong to the epoch that just finished and must be settled against that epoch id.
    _epoch = IdleCreditVault(strategy).epochNumber() + (_cdo.defaulted() ? 1 : 0);
  }
```
