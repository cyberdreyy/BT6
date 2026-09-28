### Title
Dust queued withdrawals trigger zero-price assertion and block epoch processing - (File: `contracts/IdleCDOEpochQueue.sol`)

### Summary
A KYC-passing queue user can submit a tranche withdrawal whose underlying value rounds to zero, causing `processWithdrawRequests()` to revert before it records or clears the epoch's aggregate withdrawal request. [1](#0-0) 

### Finding Description
`IdleCDOEpochQueue.processWithdrawRequests()` aggregates all tranche tokens queued for the current strategy epoch and submits them through `IdleCDOEpochVariant.requestWithdraw()`. [2](#0-1) 

The CDO converts tranche tokens with `_amount * _tranchePrice(_tranche) / ONE_TRANCHE_TOKEN`, so sufficiently small tranche amounts can round the requested underlying amount to zero. [3](#0-2) 

After the CDO call returns, the queue calculates `epochWithdrawPrice` as `_underlyingsRequested * ONE_TRANCHE / _pending` and reverts when the result is zero. [4](#0-3) 

Because the revert rolls back the entire processing call, `epochPendingWithdrawals[_epoch]` remains nonzero and no requester in that epoch can be processed as a batch. [5](#0-4) 

The production code has no minimum withdrawal amount or separate handling for a zero-value aggregate request. [6](#0-5) 

### Impact Explanation
The broken invariant is fair and reliable queued-withdrawal processing: one dust-sized request can prevent the queue from processing every request assigned to the same epoch. [6](#0-5) 

Affected users retain their queued tranche tokens, but withdrawals are temporarily frozen until each requester individually calls `deleteWithdrawRequest()` and requeues in a later epoch. [7](#0-6) 

The attacker can repeat the attack during each running epoch with a tranche amount small enough to round to zero underlying, causing recurring withdrawal delays at negligible principal cost. [7](#0-6) 

This is analogous to the OpenLDAP assertion failure because malformed low-value input reaches a hard protocol assertion and denies normal processing rather than producing a safely ignorable result. [4](#0-3) 

### Likelihood Explanation
The attacker only needs to pass `isWalletAllowed()` and submit a queued tranche withdrawal while the CDO epoch is running. [8](#0-7) 

No privileged action, borrower default, oracle manipulation, or vulnerable ERC-4626 behavior is required. [9](#0-8) 

The repository's own regression test demonstrates that a request of `100` tranche-token units makes `processWithdrawRequests()` revert with `Is0`. [10](#0-9) 

### Recommendation
Reject zero-underlying withdrawal requests before they enter the epoch aggregate, or treat them as removable zero-value requests rather than letting them poison batch processing. [6](#0-5) 

A practical fix is to calculate the implied underlying amount in `requestWithdraw()` or `processWithdrawRequests()` before transferring/burning tranche tokens and revert only that user's request when it rounds to zero. [11](#0-10) 

### Proof of Concept
This Foundry test runs in the existing `IdleCDOEpochQueue` test fixture and reproduces the assertion failure. [10](#0-9) 

```solidity
function testDustQueuedWithdrawBlocksEpochProcessing() external {
    // Finish the setup epoch and enter the withdrawal queue's running epoch.
    _stopCurrentEpoch();

    address victim = makeAddr("victim");
    uint256 victimTranches = _depositWithUser(victim, 1e6);

    vm.prank(manager);
    cdoEpoch.startEpoch();

    // Attacker queues a dust tranche amount whose underlying value rounds to 0.
    address attacker = makeAddr("attacker");
    _depositWithUser(attacker, 1e6);
    _requestWithdrawWithUser(attacker, 100);

    // A normal user's request is aggregated into the same epoch.
    _requestWithdrawWithUser(victim, victimTranches);

    _stopCurrentEpoch();

    // The entire aggregate processing call reverts, leaving both requests pending.
    vm.expectRevert(abi.encodeWithSelector(Is0.selector));
    vm.prank(manager);
    queue.processWithdrawRequests();

    assertEq(queue.pendingClaims(), false);
    assertGt(
        queue.epochPendingWithdrawals(strategy.epochNumber()),
        0
    );
}
```

### Citations

**File:** contracts/IdleCDOEpochQueue.sol (L203-217)
```text
  function deleteWithdrawRequest(uint256 _requestEpoch) external {
    // if the epoch withdraw price is already set, withdrawal requests were already processed so
    // the withdraw request can't be deleted. Withdraw requests can be deleted even if the epoch is running
    _checkNotAllowed(epochWithdrawPrice[_requestEpoch] != 0 || isEpochWithdrawZero[_requestEpoch]);

    uint256 amount = userWithdrawalsEpochs[msg.sender][_requestEpoch];
    if (amount == 0) {
      return;
    }
    // reset user withdraw request for the epoch
    userWithdrawalsEpochs[msg.sender][_requestEpoch] = 0;
    // update pending withdraw requests
    epochPendingWithdrawals[_requestEpoch] -= amount;
    // transfer tranche tokens back to the user
    IERC20Detailed(tranche).safeTransfer(msg.sender, amount);
```

**File:** contracts/IdleCDOEpochQueue.sol (L298-326)
```text
  function processWithdrawRequests() external {
    IdleCDOEpochVariant _cdo = IdleCDOEpochVariant(idleCDOEpoch);
    IdleCreditVault _strategy = IdleCreditVault(strategy);
    uint256 _epoch = _strategy.epochNumber();
    // only owner or strategy manager can call this
    _checkOnlyOwnerOrManager();
    // we revert if the are claims that needs to be processed
    _checkNotAllowed(pendingClaims);

    uint256 _pending = epochPendingWithdrawals[_epoch];
    if (_pending == 0) {
      return;
    }

    uint256 _instantWithdraws = _strategy.instantWithdrawsRequests(address(this));
    // here we receive strategyTokens for the queue contract, strategyTokens are 1:1 with underlyings
    // Instant requests always increase the queue-specific instant receipt ledger.
    uint256 _underlyingsRequested = _cdo.requestWithdraw(_pending, tranche);
    isEpochInstant[_epoch] = _strategy.instantWithdrawsRequests(address(this)) > _instantWithdraws;
    // save current implied tranche price for this epoch based on underlyings that will be received on claim
    uint256 _epochPrice = _underlyingsRequested * ONE_TRANCHE / _pending;
    if (_epochPrice == 0) {
      revert Is0();
    }
    epochWithdrawPrice[_epoch] = _epochPrice;
    // set pending withdraw requests to 0
    epochPendingWithdrawals[_epoch] = 0;
    // set pending claims to the amount of underlyings requested
    epochPendingClaims[_epoch] = _underlyingsRequested;
```

**File:** contracts/IdleCDOEpochQueue.sol (L413-421)
```text
  /// @notice check if the wallet is allowed to deposit
  /// @param wallet address to check
  function _checkAllowed(address wallet) internal view {
    IdleCDOEpochVariant cdoEpoch = IdleCDOEpochVariant(idleCDOEpoch);
    if (!cdoEpoch.isEpochRunning()) {
      revert EpochNotRunning();
    }
    _checkNotAllowed(!cdoEpoch.isWalletAllowed(wallet));
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L753-757)
```text
    if (_amount == 0) {
      _amount = _userTrancheBal(msg.sender, _tranche);
    }
    _underlyings = _trancheToUnderlyings(_amount, _tranche);

```

**File:** contracts/IdleCDOEpochVariant.sol (L815-817)
```text
  function _trancheToUnderlyings(uint256 _amount, address _tranche) internal view returns (uint256) {
    return _amount * _tranchePrice(_tranche) / ONE_TRANCHE_TOKEN;
  }
```

**File:** test/foundry/IdleCDOEpochQueue.t.sol (L1431-1455)
```text
  function testProcessWithdrawRequestsWith0Price() external {
    // stop epoch #0
    _stopCurrentEpoch();
    // we are now in epoch #1 (epoch starts at the beginning of the buffer period)

    // deposit with user1
    uint256 amount1 = 1e6; // 1 USDC
    address user1 = makeAddr('user1');
    _depositWithUser(user1, amount1);

    // start epoch #1
    vm.prank(manager);
    cdoEpoch.startEpoch();

    // request withdrawals with both users
    _requestWithdrawWithUser(user1, 100);

    // stopEpoch, deposits got some interest
    _stopCurrentEpoch();
    // we are now in epoch #2

    // can't call process deposits with 0 price
    vm.expectRevert(abi.encodeWithSelector(Is0.selector));
    vm.prank(manager);
    queue.processWithdrawRequests();
```
