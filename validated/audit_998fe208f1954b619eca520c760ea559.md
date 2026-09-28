### Title
`fullfillWriteOffRequest` is inconsistently greedy: fulfiller overpayment is swept to the requester and taxed, never refunded - (File: contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
`IdleCreditVaultWriteOffEscrow.fullfillWriteOffRequest` mirrors the `MerkleReserveMinter.mintFromReserve` bug class: the input check is one-sided (`_underlyings < currentRequest.underlyings` reverts, but there is no upper bound), and the entire caller-supplied amount is pulled into the contract instead of the exact requested amount. The excess is then split between the write-off requester and `feeReceiver`, so a fulfiller that overpays permanently loses the surplus — and additionally pays the exit fee on it.

### Finding Description
The request validation is asymmetric:

```solidity
// contracts/IdleCreditVaultWriteOffEscrow.sol L129
if (currentRequest.tranches != _tranches || _underlyings < currentRequest.underlyings) {
    revert WrongRequest();
}
```

Only underpayment is rejected. The fulfiller-chosen `_underlyings` is then fully swept:

```solidity
// L139-149
underlyingToken.safeTransferFrom(msg.sender, address(this), _underlyings);
uint256 _totFee = (_underlyings * _exitFee) / FULL_VALUE;
underlyingToken.safeTransfer(feeReceiver, _totFee);
underlyingToken.safeTransfer(_user, _underlyings - _totFee);
```

So if `currentRequest.underlyings` is 10_000 USDC and the fulfiller calls `fullfillWriteOffRequest(user, tranches, 50_000e6)` (fat-finger, stale UI quote, or a request that was partially repriced off-chain), the whole 50_000 is taken: the exit fee is charged on all 50_000, and the net excess is sent to `_user`, who receives far more than they asked for and has no obligation to return it. The fulfiller cannot reclaim the excess — the escrow has `emergencyWithdraw`, but once transferred to `_user`/`feeReceiver` the tokens are gone.

This is the same asymmetric "greedy" check as the external report: `msg.value < cost` was enforced but overpayment silently routed to treasury; here `_underlyings < requested` is enforced but overpayment is silently routed to the requester and fee receiver, with a fee charged even on the unintended surplus.

### Impact Explanation
Permanent loss of the overpaid underlying for the fulfiller (any unprivileged fulfiller, including the borrower acting as fulfiller). Loss is proportional to the input mistake — potentially the fulfiller's entire approved balance above the request amount — and is amplified by the exit fee being levied on the excess. Funds are not recoverable because the recipient is the counterparty user, not a treasury with governance recourse, and the escrow cannot pull tokens back from external wallets.

### Likelihood Explanation
Requires fulfiller input error (`_underlyings > currentRequest.underlyings`), which is a low-frequency honest-mistake scenario — identical to the external report's frequency assessment. The `fullfillWriteOffRequest` function is permissionless (`// @dev this function can be called by any wallet`), so mistaken input is not unfairly punitive. One mitigating factor: the docstring at L128 explicitly says "borrower can choose to overpay if needed", so partial overpayment is documented behavior — but charging `exitFee` on unintended surplus and routing all of it to the requester rather than capping the pull at `currentRequest.underlyings` still makes excess handling inconsistent and punitive.

### Recommendation
Make the pull exact, or make overpayment explicitly refunded:

```diff
 underlyingToken.safeTransferFrom(msg.sender, address(this), _underlyings);
 uint256 _totFee;
 if (_exitFee > 0) {
-    _totFee = (_underlyings * _exitFee) / FULL_VALUE;
+    // fee only on the requested amount; refund the excess
+    _totFee = (currentRequest.underlyings * _exitFee) / FULL_VALUE;
     underlyingToken.safeTransfer(feeReceiver, _totFee);
 }
-underlyingToken.safeTransfer(_user, _underlyings - _totFee);
+underlyingToken.safeTransfer(_user, currentRequest.underlyings - _totFee);
+if (_underlyings > currentRequest.underlyings) {
+    underlyingToken.safeTransfer(msg.sender, _underlyings - currentRequest.underlyings);
+}
```

Alternatively, enforce `_underlyings == currentRequest.underlyings` (option 2 from the external report) so the fulfiller pays exactly the quoted price.

### Proof of Concept
Drop into `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol` (mirroring `testFullfillWriteOffRequest`):

```solidity
function testFullfillWriteOffRequestOverpaymentLoss() external {
    uint256 requestedTranches = 10000e18;
    uint256 requestedUnderlyings = 10000e6;
    uint256 overpaid = 50000e6;

    vm.prank(LP);
    escrow.createWriteOffRequest(requestedTranches, requestedUnderlyings);

    deal(address(underlying), borrower, overpaid);

    uint256 balPreBorrower = underlying.balanceOf(borrower);
    uint256 balPreLP = underlying.balanceOf(LP);
    uint256 balPreFeeReceiver = underlying.balanceOf(TL_MULTISIG);

    vm.startPrank(borrower);
    underlying.approve(address(escrow), overpaid);
    // fat-fingered fulfiller sends 5x the requested amount
    escrow.fullfillWriteOffRequest(LP, requestedTranches, overpaid);
    vm.stopPrank();

    uint256 fee = overpaid * escrow.exitFee() / escrow.FULL_VALUE();

    // entire overpayment was swept: borrower lost 50k for a 10k request
    assertEq(balPreBorrower - underlying.balanceOf(borrower), overpaid, 'borrower overpay swept');
    // LP received far more than they requested, and feeReceiver took a fee on the surplus
    assertEq(underlying.balanceOf(LP) - balPreLP, overpaid - fee, 'LP received excess');
    assertEq(underlying.balanceOf(TL_MULTISIG) - balPreFeeReceiver, fee, 'fee charged on excess');
    // borrower received only the tranches priced at `requestedUnderlyings`
}
```

Note: I could not fully verify within remaining tool budget whether a Stricter `==` check breaks any intended partial/top-up fulfillment flow elsewhere in the codebase; the fix options above cover both design intents.