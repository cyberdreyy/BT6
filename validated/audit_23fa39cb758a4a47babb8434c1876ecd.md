### Title

Zero-share queued deposit causes division-by-zero and blocks epoch settlement - (`contracts/IdleCDOEpochQueue.sol`)

### Summary

The non-prefunded `IdleCDOEpochQueue.processDeposits()` path does not handle a queued deposit that mints zero tranche shares. An allowed depositor can leave a dust request while an epoch is running; when the manager processes that epoch during the buffer period, the CDO mints zero shares and the subsequent division by `_trancheMinted` reverts. [1](#0-0) 

### Finding Description

`requestDeposit()` accepts any nonzero amount from a wallet allowed by the epoch vault, with no minimum queued amount. [2](#0-1)  During the buffer period, `processDeposits()` deposits the aggregate pending amount and unconditionally calculates `epochPrice` as `_pending * ONE_TRANCHE / _trancheMinted`. [3](#0-2) 

The CDO calculates minted shares as `receivedAmount * ONE_TRANCHE_TOKEN / tranchePrice`, so a sufficiently small aggregate queued amount rounds to zero when `receivedAmount < tranchePrice / ONE_TRANCHE_TOKEN`. [4](#0-3)  This is reachable for underlying tokens with at least 18 decimals, or generally whenever `tranchePrice` exceeds `ONE_TRANCHE_TOKEN`.

The prefunded queue path already recognizes this edge case and falls back to `virtualPrice(tranche)` when `_prefundedMinted == 0`, but the ordinary path lacks the same guard. [5](#0-4) 

### Impact Explanation

The whole `processDeposits()` transaction reverts after `depositAA()` or `depositBB()` executes, so `epochPrice` remains unset and `epochPendingDeposits` remains nonzero. [1](#0-0)  Consequently, `claimDepositRequest()` cannot pay tranche shares for that queued epoch because it requires a nonzero `epochPrice` and zero pending deposits. [6](#0-5) 

A KYC-passing attacker can keep the dust request in place and repeatedly block manager settlement until depositors remove their own requests. The frozen amount is bounded by the largest aggregate queue deposit still rounding to zero: `pending < tranchePrice / ONE_TRANCHE_TOKEN`. This is temporary freezing rather than permanent loss because affected users can call `deleteRequest()` before `epochPrice` is set. [7](#0-6) 

### Likelihood Explanation

Likelihood is conditional because tranche price must be high enough, or the underlying token must have enough decimals, for the entire aggregate queued deposit to mint zero shares. The attacker needs only an allowed wallet and a dust deposit; no owner, manager, borrower, or other privileged action is required to create the blocking request. [2](#0-1) 

The explicit zero-mint fallback in the prefunded implementation shows that this condition is a recognized possible rounding outcome. [8](#0-7) 

### Recommendation

Handle `_trancheMinted == 0` in `processDeposits()` before calculating `epochPrice`.

The safest equivalent behavior is to mirror `processPrefundedDeposits()`:

```solidity
epochPrice[_epoch] = _trancheMinted == 0
  ? _cdo.virtualPrice(tranche)
  : _pending * ONE_TRANCHE / _trancheMinted;
```

Alternatively, revert inside the CDO deposit path when a nonzero underlying amount mints zero shares, or enforce a minimum queued deposit that cannot round to zero at the maximum supported tranche price. The fallback approach preserves the existing ability to refund or claim dust requests without leaving an unprocessed epoch.

### Proof of Concept

Insert this test into an existing `IdleCDOEpochQueue.t.sol`-style fixture where `cdoEpoch`, `queue`, `strategy`, `underlying`, `tranche`, `manager`, and an allowed depositor are initialized for an 18-decimal underlying token.

```solidity
function testProcessDepositsDivisionByZeroOnZeroMint() external {
    address attacker = allowedUser;
    address borrower = strategy.borrower();

    // Make tranchePrice exceed ONE_TRANCHE so a 1-wei deposit mints zero shares.
    uint256 contractValue = cdoEpoch.getContractValue();
    uint256 interestOverride = contractValue * 2;

    deal(address(underlying), borrower, interestOverride);
    vm.prank(borrower);
    underlying.approve(address(cdoEpoch), interestOverride);

    // Queue a dust deposit while the epoch is running.
    deal(address(underlying), attacker, 1);
    vm.startPrank(attacker);
    underlying.approve(address(queue), 1);
    queue.requestDeposit(1);
    vm.stopPrank();

    // Realize the override interest and enter the buffer period.
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, interestOverride);

    assertGt(cdoEpoch.virtualPrice(address(tranche)), cdoEpoch.ONE_TRANCHE_TOKEN());

    // depositAA(1) mints zero shares, then processDeposits divides by it.
    vm.expectRevert(stdError.divisionError);
    vm.prank(manager);
    queue.processDeposits();

    uint256 epoch = strategy.epochNumber();
    assertEq(queue.epochPrice(epoch), 0);
    assertEq(queue.epochPendingDeposits(epoch), 1);
}
```

This reproduces the invariant break: one queued receipt cannot be priced or claimed because aggregate minted shares are zero, while the unset `epochPrice` prevents settlement through the normal queue path. [1](#0-0)

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

**File:** contracts/IdleCDOEpochQueue.sol (L228-245)
```text
    IdleCDOEpochVariant _cdo = IdleCDOEpochVariant(idleCDOEpoch);
    uint256 _epoch = IdleCreditVault(strategy).epochNumber();
    uint256 _pending = epochPendingDeposits[_epoch];

    if (_pending == 0) {
      return;
    }

    // deposit underlyings in the CDO contract, if the epoch is running it will revert
    uint256 _trancheMinted;
    if (tranche == _cdo.AATranche()) {
      _trancheMinted = _cdo.depositAA(_pending);
    } else {
      _trancheMinted = _cdo.depositBB(_pending);
    }
    // save current implied tranche price for this epoch based on underlyings deposited and tranche tokens minted
    epochPrice[_epoch] = _pending * ONE_TRANCHE / _trancheMinted;
    epochPendingDeposits[_epoch] = 0;
```

**File:** contracts/IdleCDOEpochQueue.sol (L267-271)
```text
    // Save epoch price for the prefunded deposits based on underlyings deposited and tranche tokens minted
    // In case of very small prefunded deposits, it's possible that the tranche minting results in 0 shares due to rounding.
    // This should not block epoch finalization, as the borrower already has the funds and the epoch can be priced at the current virtual price.
    epochPrice[_epoch] = _prefundedMinted == 0 ? _cdo.virtualPrice(tranche) : _prefunded * ONE_TRANCHE / _prefundedMinted;
    epochPrefundedDeposits[_epoch] = 0;
```

**File:** contracts/IdleCDOEpochQueue.sol (L373-389)
```text
  function claimDepositRequest(uint256 _epoch) external {
    // Deposits can be claimed only after the epoch has been finalized and priced.
    _checkNotAllowed(
      epochPrice[_epoch] == 0 ||
      epochPendingDeposits[_epoch] != 0 ||
      epochPrefundedDeposits[_epoch] != 0
    );

    uint256 amount = userDepositsEpochs[msg.sender][_epoch];
    if (amount == 0) {
      return;
    }
    // reset user deposit for the epoch
    userDepositsEpochs[msg.sender][_epoch] = 0;
    // transfer tranche tokens to user based on the price of that epoch
    IERC20Detailed(tranche).safeTransfer(msg.sender, amount * ONE_TRANCHE / epochPrice[_epoch]);
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L344-348)
```text
  function _mintSharesAtCurrPrice(uint256 _amount, address _to, address _tranche) internal virtual returns (uint256 _minted) {
    // calculate # of tranche token to mint based on current tranche price: _amount / tranchePrice
    _minted = _amount * ONE_TRANCHE_TOKEN / _tranchePrice(_tranche);
    _mintShares(_tranche, _to, _minted, _amount);
  }
```
