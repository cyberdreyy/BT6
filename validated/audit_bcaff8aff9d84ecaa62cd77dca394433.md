### Title
Unvalidated zero-underlying write-off request allows theft of escrowed tranches - (File: contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
`createWriteOffRequest` validates only that the deposited tranche amount is nonzero, while allowing `underlyingsRequested == 0`. [1](#0-0)  Because fulfillment is permissionless and accepts any payment greater than or equal to the requested underlying amount, anyone can satisfy that request by paying zero and receive all escrowed tranche tokens. [2](#0-1) 

### Finding Description
During a running epoch, a lender calls `createWriteOffRequest(amount, underlyingsRequested)` to escrow tranche tokens and advertise the amount of underlying requested in exchange. [3](#0-2)  The function rejects `amount == 0` but does not reject `underlyingsRequested == 0`. [4](#0-3) 

`fullfillWriteOffRequest` can be called by any wallet, explicitly including wallets other than the borrower. [5](#0-4)  Its only economic check is `_underlyings >= currentRequest.underlyings`, so a request storing zero underlyings can be fulfilled with `_underlyings == 0`. [6](#0-5)  The function then clears the seller’s request and transfers the full `_tranches` amount to the fulfiller. [7](#0-6) 

### Impact Explanation
An unprivileged observer can monitor a pending or confirmed zero-price request and fulfill it before the seller deletes it, receiving the seller’s escrowed tranche tokens without paying any underlying. [8](#0-7)  For example, a request for `10_000e18` tranche tokens and zero USDC pays the seller zero and transfers all `10_000e18` tranche tokens to the fulfiller. [9](#0-8) 

This breaks the escrow’s intended asset-exchange invariant: deposited tranche tokens should only leave the escrow in exchange for at least the seller’s requested underlying payment. [10](#0-9) 

### Likelihood Explanation
The exploit requires only a lender to submit a zero-underlying request through a parameter-entry error and an unprivileged fulfiller to call the public fulfillment function. [1](#0-0) [11](#0-10)  No privileged role, borrower action, default, oracle, KYC status, or contract withdrawal is required. [12](#0-11) 

### Recommendation
Reject zero-value sale requests in `createWriteOffRequest` with `if (underlyingsRequested == 0) revert NotAllowed();`. [4](#0-3)  Consider additionally requiring a fulfiller-specific offer or a seller-signed fulfillment authorization so a mistakenly low advertised price cannot be executed by an arbitrary observer. [13](#0-12) 

### Proof of Concept
Add this test to `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol`; the existing fork setup initializes the escrow against the running mainnet epoch. [14](#0-13) 

```solidity
function testStealZeroUnderlyingWriteOffRequest() external {
    uint256 requestedTranches = 10000e18;
    address buyer = makeAddr("buyer");

    uint256 sellerTranchesBefore = tranche.balanceOf(LP);
    uint256 sellerUnderlyingBefore = underlying.balanceOf(LP);
    uint256 buyerTranchesBefore = tranche.balanceOf(buyer);

    // Victim accidentally requests zero underlying.
    vm.prank(LP);
    escrow.createWriteOffRequest(requestedTranches, 0);

    // Any unprivileged fulfiller can satisfy the request with zero payment.
    vm.prank(buyer);
    escrow.fullfillWriteOffRequest(LP, requestedTranches, 0);

    (uint256 tranchesLeft, uint256 underlyingsLeft) = escrow.userRequests(LP);

    assertEq(tranchesLeft, 0);
    assertEq(underlyingsLeft, 0);
    assertEq(sellerTranchesBefore - tranche.balanceOf(LP), requestedTranches);
    assertEq(underlying.balanceOf(LP), sellerUnderlyingBefore);
    assertEq(tranche.balanceOf(buyer) - buyerTranchesBefore, requestedTranches);
}
```

### Citations

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L17-20)
```text
/// @title IdleCreditVaultWriteOffEscrow
/// @dev Contract that collects write off requests of a credit vault deposit from a lender and underlyings from a fulfiller
/// and allow them to trustlessly exchange debt between lender and buyer. If the borrower fulfills the request, the borrower
/// will then be able to write off the debt by burning tranche tokens (via IdleCDOEpochVariant writeOffDeposit method)
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

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L105-151)
```text
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
  }

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

**File:** test/foundry/IdleCreditVaultWriteOffEscrow.t.sol (L32-52)
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
```

**File:** test/foundry/IdleCreditVaultWriteOffEscrow.t.sol (L205-228)
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
```
