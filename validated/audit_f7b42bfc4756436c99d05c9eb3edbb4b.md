### Title
Overpayment in write-off fulfillment is not refunded to the fulfiller - (File: `contracts/IdleCreditVaultWriteOffEscrow.sol`)

### Summary

`IdleCreditVaultWriteOffEscrow.fullfillWriteOffRequest` accepts an underlying payment greater than the lender’s requested amount, transfers the entire supplied amount into the escrow, and distributes all of it between the lender and `feeReceiver` without refunding the excess. [1](#0-0) 

### Finding Description

During a running epoch, a lender can escrow tranche tokens and specify the amount of underlying requested in `createWriteOffRequest`. [2](#0-1) 

Any wallet can later call `fullfillWriteOffRequest`, but the payment check only rejects `_underlyings < currentRequest.underlyings`, so values strictly greater than the requested amount are accepted. [3](#0-2) 

The function then pulls the full `_underlyings` amount from the fulfiller, calculates the exit fee on that full amount, sends the fee to `feeReceiver`, and forwards the remainder to the lender. [4](#0-3) 

There is no accounting of the agreed price separately from the supplied amount and no refund of `_underlyings - currentRequest.underlyings`. [5](#0-4) 

The repository’s own test suite confirms this behavior by fulfilling a 10,000 USDC request with 12,000 USDC and asserting that the borrower loses the full 12,000 USDC while the lender receives 11,988 USDC and the fee receiver receives 12 USDC. [6](#0-5) 

### Impact Explanation

A fulfiller that supplies more underlying than the lender requested permanently loses the excess payment while receiving only the fixed number of escrowed tranche tokens. [7](#0-6) 

For example, a 12,000 USDC payment against a 10,000 USDC request causes an immediate 2,000 USDC overpayment; with the default 0.1% exit fee, 1,998 USDC of the excess goes to the lender and 2 USDC goes to `feeReceiver`. [8](#0-7) [9](#0-8) 

This does not require privileged misbehavior: an unprivileged lender creates a valid request, and an unprivileged third-party fulfiller can overpay because the contract explicitly permits fulfillment above the requested amount. [10](#0-9) [11](#0-10) 

### Likelihood Explanation

The issue requires a fulfiller or integrating contract to pass an amount larger than `currentRequest.underlyings`, such as through a decimal-scaling error, stale quote, malformed UI transaction, or intentionally bidding above the ask. [12](#0-11) 

Although the caller supplies the amount, the escrow’s exchange semantics define the requested consideration, and accepting arbitrary excess turns a recoverable input error into an irreversible transfer to the requester and fee receiver. [2](#0-1) [4](#0-3) 

### Recommendation

Require exact payment unless intentional overpayment is an explicitly supported feature:

```solidity
if (
    currentRequest.tranches != _tranches ||
    _underlyings != currentRequest.underlyings
) {
    revert WrongRequest();
}
```

Alternatively, keep `>=` but pull only `currentRequest.underlyings` from the fulfiller and calculate the exit fee on that agreed amount, so surplus allowance is never consumed. [13](#0-12) 

### Proof of Concept

The following Foundry fork test can be added to `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol`, whose `setUp` already forks mainnet at block `23032567`, initializes the escrow against the live credit-vault deployment, and approves LP’s tranche tokens. [14](#0-13) 

```solidity
function testFullfillWriteOffRequestOverpayNotRefunded() external {
    address buyer = makeAddr("overpayingBuyer");

    uint256 requestedTranches = 10_000e18;
    uint256 requestedUnderlyings = 10_000e6;
    uint256 paidUnderlyings = 12_000e6;

    // LP creates a write-off request while the epoch is running.
    vm.prank(LP);
    escrow.createWriteOffRequest(requestedTranches, requestedUnderlyings);

    // An unprivileged third-party fulfiller funds and approves too much.
    deal(address(underlying), buyer, paidUnderlyings);

    uint256 buyerUnderlyingBefore = underlying.balanceOf(buyer);
    uint256 buyerTrancheBefore = tranche.balanceOf(buyer);
    uint256 lpUnderlyingBefore = underlying.balanceOf(LP);
    uint256 feeReceiverBefore = underlying.balanceOf(TL_MULTISIG);

    vm.startPrank(buyer);
    underlying.approve(address(escrow), paidUnderlyings);
    escrow.fullfillWriteOffRequest(
        LP,
        requestedTranches,
        paidUnderlyings
    );
    vm.stopPrank();

    uint256 fee = paidUnderlyings * escrow.exitFee() / escrow.FULL_VALUE();

    // The fulfiller loses the full 12,000 USDC despite the request being 10,000 USDC.
    assertEq(
        buyerUnderlyingBefore - underlying.balanceOf(buyer),
        paidUnderlyings
    );

    // The excess is not refunded: LP receives all supplied funds less the fee.
    assertEq(
        underlying.balanceOf(LP) - lpUnderlyingBefore,
        paidUnderlyings - fee
    );

    // The fee is charged on the overpayment too.
    assertEq(
        underlying.balanceOf(TL_MULTISIG) - feeReceiverBefore,
        fee
    );

    // The fulfiller receives only the fixed requested tranche amount.
    assertEq(
        tranche.balanceOf(buyer) - buyerTrancheBefore,
        requestedTranches
    );
}
```

### Citations

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L78-79)
```text
    exitFee = 100; // 0.1%
    feeReceiver = _owner; // set fee receiver to owner
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L84-101)
```text
  /// @notice create a write-off request by depositing tranche tokens and setting the amount of underlyings requested
  /// @param amount of tranche tokens to deposit
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

**File:** test/foundry/IdleCreditVaultWriteOffEscrow.t.sol (L205-241)
```text
  function testFullfillWriteOffRequestAllowsThirdPartyBuyer() external {
    uint256 requestedTranches = 10000e18;
    uint256 requestedUnderlyings = 10000e6;
    address newBorrower = makeAddr("newBorrower");
    address buyer = makeAddr("buyer");

    vm.prank(LP);
    escrow.createWriteOffRequest(requestedTranches, requestedUnderlyings);

    // rotate borrower after escrow initialization to prove fulfillment is independent from the borrower slot
    vm.prank(strategy.owner());
    strategy.setBorrower(newBorrower);

    deal(address(underlying), buyer, requestedUnderlyings);
    uint256 balPreBuyer = underlying.balanceOf(buyer);
    uint256 balPreBuyerTranche = tranche.balanceOf(buyer);
    uint256 balPreLP = underlying.balanceOf(LP);
    uint256 balPreFeeReceiver = underlying.balanceOf(TL_MULTISIG);
    uint256 contractValuePre = cdoEpoch.getContractValue();
    uint256 expectedEpochInterestPre = cdoEpoch.expectedEpochInterest();

    vm.startPrank(buyer);
    underlying.approve(address(escrow), requestedUnderlyings);
    escrow.fullfillWriteOffRequest(LP, requestedTranches, requestedUnderlyings);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.writeOffDeposit(requestedTranches, address(tranche));
    vm.stopPrank();

    (uint256 tranches, uint256 underlyings) = escrow.userRequests(LP);
    assertEq(tranches, 0, 'write-off request tranches is not 0 after third-party fulfill');
    assertEq(underlyings, 0, 'write-off request underlyings is not 0 after third-party fulfill');
    assertEq(escrow.pendingUnderlyings(), 0, 'pending underlyings is not 0 after third-party fulfill');

    uint256 fee = requestedUnderlyings * escrow.exitFee() / escrow.FULL_VALUE();
    assertEq(balPreBuyer - underlying.balanceOf(buyer), requestedUnderlyings, 'buyer balance is wrong');
    assertEq(underlying.balanceOf(LP) - balPreLP, requestedUnderlyings - fee, 'LP balance is wrong after third-party fulfill');
    assertEq(tranche.balanceOf(buyer) - balPreBuyerTranche, requestedTranches, 'buyer tranche balance is wrong');
```

**File:** test/foundry/IdleCreditVaultWriteOffEscrow.t.sol (L296-326)
```text
  function testFullfillWriteOffRequestWithUnderlyingOverpay() external {
    uint256 requestedUnderlyings = 10000e6;
    uint256 overpayUnderlyings = 12000e6;
    uint256 requestedTranches = 10000e18;

    vm.prank(LP);
    escrow.createWriteOffRequest(requestedTranches, requestedUnderlyings);
    assertEq(escrow.pendingUnderlyings(), requestedUnderlyings, 'pending underlyings is wrong after overpay request');

    deal(address(underlying), borrower, overpayUnderlyings);

    uint256 balPreBorrower = underlying.balanceOf(borrower);
    uint256 balPreBorrowerTranche = tranche.balanceOf(borrower);
    uint256 balPreLP = underlying.balanceOf(LP);
    uint256 balPreFeeReceiver = underlying.balanceOf(TL_MULTISIG);

    vm.startPrank(borrower);
    underlying.approve(address(escrow), overpayUnderlyings);
    escrow.fullfillWriteOffRequest(LP, requestedTranches, overpayUnderlyings);
    vm.stopPrank();

    (uint256 tranches, uint256 underlyings) = escrow.userRequests(LP);
    assertEq(tranches, 0, 'write-off request tranches is not 0 after overpay fulfill');
    assertEq(underlyings, 0, 'write-off request underlyings is not 0 after overpay fulfill');
    assertEq(escrow.pendingUnderlyings(), 0, 'pending underlyings is not 0 after overpay fulfill');

    uint256 fee = overpayUnderlyings * escrow.exitFee() / escrow.FULL_VALUE();
    assertEq(balPreBorrower - underlying.balanceOf(borrower), overpayUnderlyings, 'borrower balance is wrong after overpay fulfill');
    assertEq(underlying.balanceOf(LP) - balPreLP, overpayUnderlyings - fee, 'LP balance is wrong after overpay fulfill');
    assertEq(tranche.balanceOf(borrower) - balPreBorrowerTranche, requestedTranches, 'borrower tranche balance is wrong after overpay fulfill');
    assertEq(underlying.balanceOf(TL_MULTISIG) - balPreFeeReceiver, fee, 'fee receiver balance is wrong after overpay fulfill');
```
