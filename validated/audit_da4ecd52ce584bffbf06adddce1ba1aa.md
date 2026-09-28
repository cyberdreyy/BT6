### Title
Write-off escrow allows theft of escrowed tranche tokens via a zero-underlyings request - ([File: contracts/IdleCreditVaultWriteOffEscrow.sol](contracts/IdleCreditVaultWriteOffEscrow.sol))

### Summary
The external report (CWE-89, blacklist bypass via crafted input) maps to an input-validation gap in `IdleCreditVaultWriteOffEscrow`: `createWriteOffRequest` blacklists only `amount == 0` but places no constraint on `underlyingsRequested`, and `fullfillWriteOffRequest` only rejects *underpayment* (`_underlyings < currentRequest.underlyings`). A request created with `underlyings == 0` therefore accepts a fulfillment paying **zero** underlying tokens, letting any EOA fulfiller take the lender's escrowed tranche tokens for free.

### Finding Description
In `createWriteOffRequest`, the only validation is `amount == 0` → revert; `underlyingsRequested` may be `0` (or anything else) [1](#0-0) . The lender's tranche tokens are transferred into escrow immediately, and `userRequests[msg.sender]` records `underlyings == 0`.

`fullfillWriteOffRequest` enforces only:
```solidity
if (currentRequest.tranches != _tranches || _underlyings < currentRequest.underlyings) {
  revert WrongRequest();
}
```
With `currentRequest.underlyings == 0`, passing `_underlyings == 0` satisfies the check [2](#0-1) . The function then:
- pulls `0` underlyings from the fulfiller (`safeTransferFrom` of 0 succeeds on standard ERC20s),
- skips the exit fee (`0 * exitFee / FULL_VALUE == 0`),
- sends `0` underlyings to the lender,
- transfers **all** escrowed tranche tokens to the fulfiller [3](#0-2) .

`fullfillWriteOffRequest` is explicitly callable by any wallet ("this function can be called by any wallet"), so no privileged role or Keyring check is required — a non-KYC fulfiller can execute it [4](#0-3) . Tests confirm fulfillers need not pass `isWalletAllowed` [5](#0-4) .

### Impact Explanation
Direct theft of escrowed assets: any unprivileged fulfiller permanently extracts a lender's tranche tokens (redeemable for pool underlyings at `virtualPrice`) while paying nothing. Loss equals the full underlying value of the escrowed tranche tokens in the affected request.

### Likelihood Explanation
Exploitation requires a `userRequests` entry with `underlyings == 0`. This can arise from lender error (intending to accept any offer / forgetting the parameter — it is not guarded as nonzero) or from a deliberately "negotiable" request. While conditional on that state existing, the escrow imposes no defense: there is no minimum-price floor, no `underlyingsRequested != 0` check, and fulfillment is permissionless and atomic. The overpay path is documented ("borrower can choose to overpay"), but nothing documents or intends that a fulfiller may pay zero. Likelihood is conditional; severity of the gap is real.

### Recommendation
In `createWriteOffRequest`, revert when `underlyingsRequested == 0` (mirroring the `amount == 0` check), and/or in `fullfillWriteOffRequest` require `_underlyings > 0` / enforce a minimum acceptable price. Optionally let lenders update a request's price rather than only accumulate, so requests never sit at a zero price.

### Proof of Concept
Foundry-style fork PoC (mirroring `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol` setup with a running epoch):

```solidity
// epoch running; LP holds tranche tokens
uint256 trancheAmt = 10_000e18;

// 1) Lender creates a request whose underlyingsRequested == 0 (no guard on it)
vm.prank(LP);
escrow.createWriteOffRequest(trancheAmt, 0);

// 2) Any EOA fulfiller pays 0 underlying and receives all escrowed tranche tokens
address attacker = makeAddr("attacker");
uint256 balPre = tranche.balanceOf(attacker);
vm.prank(attacker);
escrow.fullfillWriteOffRequest(LP, trancheAmt, 0); // does not revert

// 3) Theft confirmed
assertEq(tranche.balanceOf(attacker) - balPre, trancheAmt); // attacker got tranches
assertEq(underlying.balanceOf(LP), balPreLP);               // lender received 0
```

Caveat: I could not fully inspect `IdleCreditVault.sol` internals (164 matches were not enumerated) for a stronger analog, but the escrow finding stands independently on the code shown above.

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

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L122-131)
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
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L135-151)
```text
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

**File:** test/foundry/IdleCreditVaultWriteOffEscrow.t.sol (L247-293)
```text
  function testNonKeyringBuyerCanFulfillExistingRequestButCannotWithdraw() external {
    uint256 requestedTranches = 10000e18;
    uint256 requestedUnderlyings = 10000e6;
    address buyer = makeAddr("buyer");
    address keyring = address(1);

    vm.prank(LP);
    escrow.createWriteOffRequest(requestedTranches, requestedUnderlyings);

    vm.prank(cdoEpoch.owner());
    cdoEpoch.setKeyringParams(keyring, 1);

    vm.mockCall(
      keyring,
      abi.encodeWithSelector(IKeyring.checkCredential.selector),
      abi.encode(false)
    );

    deal(address(underlying), buyer, requestedUnderlyings);
    uint256 balPreBuyer = underlying.balanceOf(buyer);
    uint256 balPreLP = underlying.balanceOf(LP);
    uint256 balPreBuyerTranche = tranche.balanceOf(buyer);
    uint256 balPreFeeReceiver = underlying.balanceOf(TL_MULTISIG);

    vm.startPrank(buyer);
    underlying.approve(address(escrow), requestedUnderlyings);
    escrow.fullfillWriteOffRequest(LP, requestedTranches, requestedUnderlyings);
    vm.stopPrank();

    (uint256 tranches, uint256 underlyings) = escrow.userRequests(LP);
    assertEq(tranches, 0, 'write-off request tranches is not 0 after non-keyring fulfill');
    assertEq(underlyings, 0, 'write-off request underlyings is not 0 after non-keyring fulfill');
    assertEq(escrow.pendingUnderlyings(), 0, 'pending underlyings is not 0 after non-keyring fulfill');

    uint256 fee = requestedUnderlyings * escrow.exitFee() / escrow.FULL_VALUE();
    assertEq(balPreBuyer - underlying.balanceOf(buyer), requestedUnderlyings, 'buyer balance is wrong after non-keyring fulfill');
    assertEq(underlying.balanceOf(LP) - balPreLP, requestedUnderlyings - fee, 'LP balance is wrong after non-keyring fulfill');
    assertEq(tranche.balanceOf(buyer) - balPreBuyerTranche, requestedTranches, 'buyer tranche balance is wrong after non-keyring fulfill');
    assertEq(underlying.balanceOf(TL_MULTISIG) - balPreFeeReceiver, fee, 'fee receiver balance is wrong after non-keyring fulfill');

    _stopCurrentEpoch();
    assertEq(cdoEpoch.isWalletAllowed(buyer), false, 'buyer should not be wallet allowed');
    vm.prank(buyer);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.requestWithdraw(requestedTranches, address(tranche));

    vm.clearMockedCalls();
```
