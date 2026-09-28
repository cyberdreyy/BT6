### Title
Stale write-off requests remain fulfillable after borrower default - ([File: contracts/IdleCreditVaultWriteOffEscrow.sol](contracts/IdleCreditVaultWriteOffEscrow.sol))

### Summary
`IdleCreditVaultWriteOffEscrow` validates that the pool epoch is running only when a write-off request is created, but not when it is fulfilled. A lender can therefore leave escrowed tranche tokens for sale, wait until the borrower defaults or the epoch otherwise stops, and still let a buyer fulfill the stale request. The buyer pays the full requested underlying amount and receives tranche tokens whose normal withdrawal path has been disabled and whose recovery value can be zero.

### Finding Description
`createWriteOffRequest` rejects requests unless `IdleCDOEpochVariant.isEpochRunning()` is true and escrows the seller's tranche tokens. [1](#0-0) 

`fullfillWriteOffRequest` only checks that the request exists and that the supplied parameters match it; it does not check `isEpochRunning()`, `defaulted`, `epochEndDate`, pause state, tranche price, or whether the tranche tokens remain redeemable. [2](#0-1) 

Once those checks pass, the function atomically pulls underlying from the fulfiller, pays the seller, and transfers the escrowed tranche tokens to the fulfiller. [3](#0-2) 

The state can change between creation and fulfillment. On borrower default, `IdleCDOEpochVariant._handleBorrowerDefault` marks the pool defaulted, pauses it, sets `isEpochRunning` to false, and disables both AA and BB withdrawal requests. [4](#0-3) 

Despite that state transition, the previously created escrow request remains executable because fulfillment never revalidates the pool state. [5](#0-4) 

This is analogous to transferring a loan NFT after the underlying loan has already been repaid or become worthless: the transfer settles against a stale representation without checking the current credit state.

### Impact Explanation
A buyer can permanently lose the full underlying amount paid to fulfill a stale request. For example, a seller can escrow `10_000` AA tranche tokens and request `10_000` USDC while an epoch is running. If the borrower defaults before fulfillment, the buyer can still call `fullfillWriteOffRequest`, pay `10_000` USDC, and receive the tranche tokens.

Those tranche tokens no longer have the ordinary withdrawal-request path because default disables `allowAAWithdrawRequest` and `allowBBWithdrawRequest`. [6](#0-5) 

If default recovery is finalized with zero or subprecision recovery, the CDO writes the final tranche NAVs and recovery accounting through `finalizeDefault`, leaving the purchased position with no practical redemption value. [7](#0-6) 

The direct loss is the amount paid minus any later recovery or redemption value; with zero recovery, the loss is the full `10_000` USDC in the example.

### Likelihood Explanation
The seller does not need a privileged role. Any tranche holder can create a request while the epoch is running and simply leave it open. The borrower, owner, manager, and other privileged actors can remain honest; the attack only sequences the already-public fulfillment around an epoch stop or borrower default.

A buyer may fulfill the request through an off-chain interface, bot, aggregator, or stale quote that assumes the request remains valid. The escrow itself gives no protection at fulfillment time, even though creation explicitly treats a non-running epoch as invalid for new requests. [8](#0-7) 

The issue is especially reachable because `fullfillWriteOffRequest` is intentionally callable by any wallet. [9](#0-8) 

### Recommendation
Revalidate the vault state inside `fullfillWriteOffRequest`, not just inside `createWriteOffRequest`.

At minimum, fulfillment should revert when:

- `IdleCDOEpochVariant(idleCDOEpoch).isEpochRunning()` is false;
- `IdleCDOEpochVariant(idleCDOEpoch).defaulted()` is true;
- the epoch has passed `epochEndDate`;
- the CDO is paused, in emergency shutdown, closed, or otherwise no longer permits normal tranche exits.

If write-off sales are intended to remain valid after an epoch ends, fulfillment should instead price the tranche tokens using current recovery or tranche value rather than the seller's stale requested amount. Existing requests should also be removable by the seller after a state transition, since `deleteWriteOffRequest` currently remains available and returns the escrowed tranches. [10](#0-9) 

### Proof of Concept
The following Foundry fork test extends the existing test setup in `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol`.

```solidity
function testFulfillAfterDefaultBuysDefaultedTranches() external {
    uint256 requestedTranches = 10_000e18;
    uint256 requestedUnderlyings = 10_000e6;
    address buyer = makeAddr("buyer");

    // Seller creates the request while the epoch is running.
    vm.prank(LP);
    escrow.createWriteOffRequest(requestedTranches, requestedUnderlyings);

    // Move past the epoch and make the borrower unable to repay.
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(address(underlying), borrower, 0);

    // Honest manager/owner stop detects the missing repayment and defaults.
    vm.prank(cdoEpoch.owner());
    cdoEpoch.stopEpoch(0, 1_000e6);
    assertTrue(cdoEpoch.defaulted());
    assertFalse(cdoEpoch.isEpochRunning());

    // The stale order is still executable.
    deal(address(underlying), buyer, requestedUnderlyings);
    vm.startPrank(buyer);
    underlying.approve(address(escrow), requestedUnderlyings);
    escrow.fullfillWriteOffRequest(
        LP,
        requestedTranches,
        requestedUnderlyings
    );
    vm.stopPrank();

    // Buyer paid the full amount and received tranche tokens.
    assertEq(underlying.balanceOf(buyer), 0);
    assertEq(tranche.balanceOf(buyer), requestedTranches);

    // The default disables normal withdrawal requests.
    vm.prank(buyer);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.requestWithdraw(requestedTranches, address(tranche));

    // With zero recovery, the purchased tranche position is worthless.
    vm.prank(manager);
    cdoEpoch.finalizeDefault(0, address(0));
}
```

The key assertion is that `fullfillWriteOffRequest` succeeds after `defaulted == true` and `isEpochRunning == false`, even though `createWriteOffRequest` would reject that same state. This demonstrates a direct transfer of `requestedUnderlyings` from the buyer to the seller in exchange for tranche tokens whose normal redemption path has been disabled.

### Citations

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L86-93)
```text
  function createWriteOffRequest(uint256 amount, uint256 underlyingsRequested) external nonReentrant {
    // can request write off only when epoch is running
    if (!IdleCDOEpochVariant(idleCDOEpoch).isEpochRunning()) revert EpochNotRunning();
    // cannot request write off with 0 tranche tokens
    if (amount == 0) revert NotAllowed();

    // get tranche tokens from user
    IERC20Detailed(tranche).safeTransferFrom(msg.sender, address(this), amount);
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L104-115)
```text
  /// @notice delete the write-off request and transfer tranche tokens back to the user
  function deleteWriteOffRequest() external nonReentrant {
    // get current write-off request
    WriteOffRequest memory currentRequest = userRequests[msg.sender];
    // check if the user has a write-off request
    if (currentRequest.tranches == 0) revert Is0();

    // Existing upgraded escrows can have legacy requests that were never added to pendingUnderlyings.
    pendingUnderlyings -= pendingUnderlyings >= currentRequest.underlyings ? currentRequest.underlyings : pendingUnderlyings;
    delete userRequests[msg.sender];
    // transfer tranche tokens back to the user
    IERC20Detailed(tranche).safeTransfer(msg.sender, currentRequest.tranches);
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L118-151)
```text
  /// @notice fulfill the write-off request by buying the escrowed tranche tokens
  /// @param _user address of the user that made the write-off request
  /// @param _tranches amount of tranche tokens to transfer
  /// @param _underlyings amount of underlyings to transfer
  /// @dev this function can be called by any wallet
  function fullfillWriteOffRequest(address _user, uint256 _tranches, uint256 _underlyings) external nonReentrant {
    // get current write-off request
    WriteOffRequest memory currentRequest = userRequests[_user];
    // check if the user has a write-off request
    if (currentRequest.tranches == 0) revert Is0();
    // check if the request matches at least the expected values (borrower can choose to overpay if needed, but not underpay)
    if (currentRequest.tranches != _tranches || _underlyings < currentRequest.underlyings) {
      revert WrongRequest();
    }

    // Existing upgraded escrows can have legacy requests that were never added to pendingUnderlyings.
    pendingUnderlyings -= pendingUnderlyings >= currentRequest.underlyings ? currentRequest.underlyings : pendingUnderlyings;
    delete userRequests[_user];

    IERC20Detailed underlyingToken = IERC20Detailed(underlying);
    // transfer underlyings requested from the fulfiller to this contract
    underlyingToken.safeTransferFrom(msg.sender, address(this), _underlyings);
    // check if the exit fee is set and if so, apply it
    uint256 _exitFee = exitFee;
    uint256 _totFee;
    if (_exitFee > 0) {
      _totFee = (_underlyings * _exitFee) / FULL_VALUE;
      // transfer exit fee to the feeReceiver
      underlyingToken.safeTransfer(feeReceiver, _totFee);
    }
    // transfer the remaining underlyings to the user
    underlyingToken.safeTransfer(_user, _underlyings - _totFee);
    // transfer tranche tokens to fulfiller
    IERC20Detailed(tranche).safeTransfer(msg.sender, _tranches);
```

**File:** contracts/IdleCDOEpochVariant.sol (L194-210)
```text
  function finalizeDefault(uint256 _recoveredAmount, address _recoverySource) external {
    _checkOnlyOwnerOrManager();
    // Send raw CDO underlying to feeReceiver as donated assets; recovery must enter through the strategy.
    _skimDonatedAssets();
    // Recovery math and reserve accounting live in the strategy where receipt claims are paid.
    uint256 defaultBBNav = IdleCreditVault(strategy).finalizeDefaultRecovery(_recoveredAmount, _recoverySource);

    // Default recovery should not keep accruing fees or leave old fee claims senior to LP recovery.
    fee = 0;
    managementFee = 0;
    unclaimedFees = 0;
    latestHarvestBlock = block.timestamp;

    // The strategy returns BB's final recovered active NAV. Writing both final NAVs directly
    // makes hard default the sole exception to the ordinary BB-first loss waterfall.
    lastNAVBB = defaultBBNav;
    lastNAVAA = _contractTokenBalance(strategyToken) - defaultBBNav;
```

**File:** contracts/IdleCDOEpochVariant.sol (L576-598)
```text
  /// @notice Handle borrower default
  function _handleBorrowerDefault(uint256 funds) internal {
    defaulted = true;
    // Do not reopen instant claims here. They remain disabled when funding is pending;
    // successful full funding is the only path that enables them before finalization.

    if (isProgrammableBorrower) {
      IProgrammableBorrower(_borrower()).onDefault();
    }

    // deposits should be already prevented
    if (!paused()) {
      _pause();
    }

    // stop the current epoch
    isEpochRunning = false;

    // prevent withdrawals requests
    allowAAWithdrawRequest = false;
    allowBBWithdrawRequest = false;

    emit BorrowerDefault(funds);
```
