### Title
Overpayment in write-off fulfillment is not returned - (File: `contracts/IdleCreditVaultWriteOffEscrow.sol`)

### Summary
`fullfillWriteOffRequest` permits a fulfiller to provide more underlying than the lender requested, but transfers the entire supplied amount rather than the stored request amount. The excess is split between the lender and fee receiver instead of being returned or rejected. [1](#0-0) 

### Finding Description
A write-off request stores the exact number of tranche tokens and the exact amount of underlying requested by the lender. [2](#0-1) 

During fulfillment, the function requires `_tranches` to equal the request, but only checks that `_underlyings` is greater than or equal to `currentRequest.underlyings`. [3](#0-2) 

It then pulls the full attacker-supplied `_underlyings` amount, calculates the exit fee on that full amount, pays the remainder to the lender, and gives the fulfiller only the requested tranche amount. [4](#0-3) 

This can occur while an epoch is running because `createWriteOffRequest` requires `IdleCDOEpochVariant(idleCDOEpoch).isEpochRunning()`. [5](#0-4) 

### Impact Explanation
An unprivileged write-off fulfiller that supplies `requested + X` permanently loses the excess `X`, plus the incremental exit fee charged on `X`.

For a requested amount `R` and supplied amount `R + X`:

- The fulfiller’s transferred amount is `R + X`.
- The lender receives approximately `R + X - fee`.
- The fee receiver receives `(R + X) * exitFee / FULL_VALUE`.
- The fulfiller receives only the fixed `_tranches` amount.

The intended economic exchange is `R` underlying for the requested tranches, so the excess payment is not refunded and becomes a permanent transfer.

### Likelihood Explanation
The function can be called by any wallet and does not require the caller to be the borrower. [6](#0-5) 

The likelihood depends on a fulfiller passing a larger `_underlyings` value than requested, for example through an interface bug, decimal conversion mistake, stale quote, or manual transaction error. No privileged role or malicious contract behavior is required.

The code comment explicitly allows overpayment, but that design exposes the fulfiller to accidental permanent loss rather than capping the pull at the agreed request amount. [7](#0-6) 

### Recommendation
Require `_underlyings == currentRequest.underlyings`, or ignore the caller-provided amount and transfer only `currentRequest.underlyings`.

```solidity
// contracts/IdleCreditVaultWriteOffEscrow.sol
uint256 payment = currentRequest.underlyings;
underlyingToken.safeTransferFrom(msg.sender, address(this), payment);
```

If intentional overpayment must remain supported, calculate the exit fee only on `currentRequest.underlyings` and immediately return `_underlyings - currentRequest.underlyings` to the fulfiller.

### Proof of Concept

```solidity
// test/foundry/IdleCreditVaultWriteOffEscrow.t.sol
function testOverpaymentIsNotReturned() external {
    uint256 trancheAmount = 100e18;
    uint256 requested = 1_000e6;
    uint256 excess = 500e6;
    uint256 supplied = requested + excess;

    vm.prank(lender);
    escrow.createWriteOffRequest(trancheAmount, requested);

    deal(address(underlyingToken), fulfiller, supplied);

    vm.startPrank(fulfiller);
    underlyingToken.approve(address(escrow), supplied);
    escrow.fullfillWriteOffRequest(lender, trancheAmount, supplied);
    vm.stopPrank();

    uint256 fee = supplied * escrow.exitFee() / escrow.FULL_VALUE();

    assertEq(underlyingToken.balanceOf(fulfiller), 0);
    assertEq(underlyingToken.balanceOf(lender), supplied - fee);
    assertEq(underlyingToken.balanceOf(escrow.feeReceiver()), fee);
    assertEq(trancheToken.balanceOf(fulfiller), trancheAmount);

    // The extra 500e6 was not returned: it was distributed to the lender
    // and feeReceiver even though the request was only for 1_000e6.
}
```

### Citations

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L24-29)
```text
  struct WriteOffRequest {
    /// @notice tranche tokens provided by the lender
    uint256 tranches;
    /// @notice underlyings requested by the lender
    uint256 underlyings;
  }
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L86-93)
```text
  function createWriteOffRequest(uint256 amount, uint256 underlyingsRequested) external nonReentrant {
    // can request write off only when epoch is running
    if (!IdleCDOEpochVariant(idleCDOEpoch).isEpochRunning()) revert EpochNotRunning();
    // cannot request write off with 0 tranche tokens
    if (amount == 0) revert NotAllowed();

    // get tranche tokens from user
    IERC20Detailed(tranche).safeTransferFrom(msg.sender, address(this), amount);
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
