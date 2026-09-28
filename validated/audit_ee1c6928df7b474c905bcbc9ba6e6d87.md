### Title
Zero-priced write-off requests allow unprivileged theft of escrowed tranche tokens - (contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
`createWriteOffRequest` validates only that the deposited tranche amount is nonzero, but it permits `underlyingsRequested == 0`; any wallet can then fulfill the request for zero underlying and receive the full escrowed tranche balance. [1](#0-0) [2](#0-1) 

### Finding Description
During a running epoch, a tranche holder can escrow tranche tokens by calling `createWriteOffRequest(amount, underlyingsRequested)`. The function rejects `amount == 0`, transfers `amount` tranche tokens into the escrow, and stores the caller-supplied requested underlying amount without requiring it to be nonzero or economically sane. [1](#0-0) 

If a user mistakenly submits `underlyingsRequested = 0`, `fullfillWriteOffRequest` accepts `_underlyings = 0` because the check requires only `_underlyings >= currentRequest.underlyings`. The function is explicitly callable by any wallet, calculates a zero exit fee, transfers zero underlying to the seller, and transfers all `_tranches` to the fulfiller. [2](#0-1) 

The only recovery mechanism is `deleteWriteOffRequest`, which returns the escrowed tranche tokens to the requester. Once the zero-priced request is publicly visible, however, an unprivileged observer can front-run the deletion and permanently acquire the tranches for no consideration. [3](#0-2) [2](#0-1) 

### Impact Explanation
A mistaken seller can lose the entire escrowed tranche position for zero underlying, less only the attacker’s gas cost. For example, the existing test fixture escrows `10000e18` tranche tokens when requesting `10000e6` underlying, so setting the request to `0` instead exposes that full position to uncompensated seizure. [4](#0-3) 

This breaks fair escrow settlement because one deposited tranche receipt can be claimed by a fulfiller without the corresponding asset payment intended by the write-off mechanism. [5](#0-4) 

### Likelihood Explanation
Exploitation requires a seller to submit a zero or dust-priced request, so it is conditional on user error rather than being universally exploitable. The attack surface is nevertheless public: requests are stored under `userRequests`, fulfillment is permissionless, and the fulfill transaction can be executed before the seller can delete the mistaken request. [6](#0-5) [7](#0-6) 

### Recommendation
Reject zero and dust-valued `underlyingsRequested` values in `createWriteOffRequest`, and consider enforcing a minimum recovery amount derived from the current tranche price or a configured minimum recovery percentage. The fulfillment path should additionally require `_underlyings > 0` so an accidental request cannot transfer tranche tokens for no consideration. [1](#0-0) [8](#0-7) 

### Proof of Concept
The following Foundry test can be added to `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol`, whose setup already initializes the escrow on a mainnet fork and gives `LP` escrow approval. [9](#0-8) 

```solidity
function testZeroPricedWriteOffRequestCanBeSeized() external {
    uint256 tranches = 10_000e18;
    address attacker = makeAddr("attacker");

    uint256 sellerTranchesBefore = tranche.balanceOf(LP);
    uint256 sellerUnderlyingBefore = underlying.balanceOf(LP);

    vm.prank(LP);
    escrow.createWriteOffRequest(tranches, 0);

    uint256 attackerTranchesBefore = tranche.balanceOf(attacker);

    vm.prank(attacker);
    escrow.fullfillWriteOffRequest(LP, tranches, 0);

    assertEq(tranche.balanceOf(attacker) - attackerTranchesBefore, tranches);
    assertEq(tranche.balanceOf(LP), sellerTranchesBefore - tranches);
    assertEq(underlying.balanceOf(LP), sellerUnderlyingBefore);
    assertEq(escrow.pendingUnderlyings(), 0);
}
```

The zero-payment fulfillment passes the existing request check because `0 < 0` is false, produces a zero exit fee, sends zero underlying to `LP`, and transfers `tranches` to `attacker`. [8](#0-7)

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

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L105-115)
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
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L122-151)
```text
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

**File:** test/foundry/IdleCreditVaultWriteOffEscrow.t.sol (L120-132)
```text
  function testCreateWriteOffRequest() external {
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    escrow.createWriteOffRequest(0, 1e6);

    uint256 trancheBalancePre = tranche.balanceOf(LP);
    vm.prank(LP);
    escrow.createWriteOffRequest(10000e18, 10000e6); // 10k tranche tokens and 10k USDC requested
    uint256 trancheBalancePost = tranche.balanceOf(LP);
    assertEq(trancheBalancePre - trancheBalancePost, 10000e18, 'tranche balance of LP is wrong after write-off request');
    (uint256 tranches, uint256 underlyings) = escrow.userRequests(LP);
    assertEq(tranches, 10000e18, 'write-off request tranches is wrong');
    assertEq(underlyings, 10000e6, 'write-off request underlyings is wrong');
    assertEq(escrow.pendingUnderlyings(), 10000e6, 'pending underlyings is wrong after first request');
```
