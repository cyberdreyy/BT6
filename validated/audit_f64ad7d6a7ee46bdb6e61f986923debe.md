### Title
Zero-priced write-off requests allow any fulfiller to steal escrowed tranche tokens - ([File: contracts/IdleCreditVaultWriteOffEscrow.sol])

### Summary
`createWriteOffRequest` validates that the deposited tranche-token amount is nonzero but accepts a zero `underlyingsRequested` value. [1](#0-0)  Any unprivileged fulfiller can then call `fullfillWriteOffRequest` with `_underlyings == 0`, satisfy the request check, pay no underlying, and receive all escrowed tranche tokens. [2](#0-1) 

### Finding Description
The escrow treats a write-off request as an exchange of lender-supplied tranche tokens for fulfiller-supplied underlying tokens. [3](#0-2)  The creation path rejects a zero tranche amount, demonstrating that a valid request is intended to have meaningful collateral, but does not apply the same validation to the requested underlying leg. [4](#0-3) 

During fulfilment, the only lower-bound check is `_underlyings >= currentRequest.underlyings`; therefore a stored requested amount of zero permits a fulfilment amount of zero. [5](#0-4)  The function deletes the request, executes a zero-value underlying pull and payout, computes a zero exit fee, and unconditionally transfers the escrowed tranche balance to the fulfiller. [6](#0-5) 

The exchange is intentionally open because the NatSpec explicitly says fulfilment can be called by any wallet, so neither borrower status nor Keyring approval prevents this path. [7](#0-6)  The only phase precondition is on request creation, which requires the credit-vault epoch to be running. [8](#0-7) 

### Impact Explanation
This breaks the escrow’s fair-exchange invariant: one side escrows valuable tranche tokens, but the counterparty can fulfill the request without transferring any underlying consideration. [9](#0-8) [10](#0-9) 

For example, a lender that accidentally submits a 10,000-tranche request with a zero underlying ask permanently loses 10,000 tranche tokens to the first caller, while receiving zero underlying. [11](#0-10) [12](#0-11)  Because fulfilment erases the request before transfer, the seller cannot recover the escrowed tokens through `deleteWriteOffRequest`. [6](#0-5) 

### Likelihood Explanation
Exploitation requires a lender to create a request with `underlyingsRequested == 0`, so it depends on malformed input rather than allowing the attacker to modify another user’s order. [1](#0-0)  Once such a request exists, exploitation is permissionless and atomic, and public fulfilment is an intended feature rather than a privileged edge case. [7](#0-6) 

The vulnerability is especially relevant for client or calldata mistakes because both request fields are unrestricted numeric inputs and only the tranche leg has an explicit zero check. [1](#0-0) 

### Recommendation
Reject zero-priced requests in `createWriteOffRequest` with `if (amount == 0 || underlyingsRequested == 0) revert NotAllowed();`. [4](#0-3)  For defense in depth, `fullfillWriteOffRequest` should also revert when either the requested underlying amount or `_underlyings` is zero before deleting the request. [13](#0-12) 

### Proof of Concept
The following Foundry fork test can be added to `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol`; the existing setup forks mainnet at block `23032567`, initializes the escrow against the live credit vault, and grants the existing LP’s tranche-token approval. [14](#0-13) 

```solidity
function testZeroPriceWriteOffRequestCanBeFulfilledForFree() external {
    address attacker = makeAddr("attacker");
    uint256 trancheAmount = 10_000e18;

    uint256 lpTrancheBefore = tranche.balanceOf(LP);
    uint256 attackerUnderlyingBefore = underlying.balanceOf(attacker);

    // Victim accidentally creates a nonzero tranche request with a zero ask.
    vm.prank(LP);
    escrow.createWriteOffRequest(trancheAmount, 0);

    (uint256 tranches, uint256 underlyings) = escrow.userRequests(LP);
    assertEq(tranches, trancheAmount);
    assertEq(underlyings, 0);
    assertEq(tranche.balanceOf(LP), lpTrancheBefore - trancheAmount);

    // Any EOA can fulfil it without holding or approving any underlying.
    vm.prank(attacker);
    escrow.fullfillWriteOffRequest(LP, trancheAmount, 0);

    (tranches, underlyings) = escrow.userRequests(LP);
    assertEq(tranches, 0);
    assertEq(underlyings, 0);

    // Attacker receives every escrowed tranche token and pays zero underlying.
    assertEq(tranche.balanceOf(attacker), trancheAmount);
    assertEq(underlying.balanceOf(attacker), attackerUnderlyingBefore);
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
