### Title
Claimed instant-withdraw receipts remain in epoch accounting and create phantom default recovery - (File: `contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
`claimInstantWithdrawRequest` clears only the aggregate `instantWithdrawsRequests[_user]` balance, but leaves both `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` populated after the user has already received the underlying. If another instant request is opened in the same epoch and the borrower later defaults before that request is funded, default finalization treats the already-paid first receipt as current-epoch claim basis and prefunded reserve. This creates recovery liabilities and `defaultRecoveryReserve` that are not backed by underlying tokens.

### Finding Description
`requestInstantWithdraw` records each request in the aggregate ledger, the per-user/per-epoch ledger, and the epoch-wide claim ledger, while increasing `pendingInstantWithdraws` [1](#0-0) . A successful funded claim burns and clears only the aggregate user balance; it does not clear either per-epoch ledger [2](#0-1) . During default finalization, any nonzero `pendingInstantWithdraws` causes the full `instantWithdrawClaimsByEpoch[epochNumber]` to be included in the claim basis, including receipts already paid during that same epoch [3](#0-2) . The same stale epoch total is then interpreted as already-held prefunded reserve equal to `instantBasis - pendingInstantWithdraws` [4](#0-3) . Finalization persists this phantom amount in `defaultRecoveryReserve` and uses it to calculate `defaultRecoveryPrice` [5](#0-4) .

### Impact Explanation
An unprivileged tranche holder can make an instant-withdraw request, have it funded by the honest manager, claim it, and then allow another user to open a later instant request in the same epoch. If the borrower defaults while the second request is unfunded, the first user's already-paid amount `A` is counted as both defaulted claim basis and prefunded reserve. With zero external recovery, `defaultRecoveryReserve` becomes `A` while the strategy holds no corresponding underlying because `A` was already transferred to the first claimant. Legitimate defaulted claimants then receive an inflated recovery price and their claims revert or remain unpaid until at least `A` is externally replenished. With partial recovery, earlier claimants can consume recovery belonging to later claimants, leaving a quantified reserve shortfall equal to the previously paid receipt amount.

### Likelihood Explanation
The trigger uses only normal user sequencing and honest privileged actions: an APR decrease enables instant withdrawals, the manager funds one request, that user claims, another unprivileged user requests an instant withdrawal in the same epoch, and the borrower later defaults before funding it. No malicious privileged role, direct token donation, reentrancy, or control of the borrower is required. The accounting failure is deterministic once those state transitions occur.

### Recommendation
Track and clear instant-withdraw receipts at the same granularity used by default accounting. A funded claim must remove the corresponding `instantWithdrawsRequestsByEpoch` entries and decrement `instantWithdrawClaimsByEpoch`, which requires retaining the user's request epochs or otherwise maintaining an enumerable per-user receipt set. Alternatively, split funded and unfunded instant claims by epoch so that `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` count only receipts still outstanding at default finalization.

### Proof of Concept
The following Foundry test sketch uses the existing `IdleCreditVault.t.sol` setup and helper flow. It demonstrates that one paid receipt remains in `instantWithdrawClaimsByEpoch`, is counted as prefunded recovery reserve, and prevents a later defaulted claimant from being paid.

```solidity
function testClaimedInstantReceiptCreatesPhantomDefaultReserve() external {
  _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());

  address staleUser = makeAddr("stale-instant-user");
  address victim = makeAddr("defaulted-instant-user");
  uint256 amount = 10_000 * ONE_SCALE;
  uint256 firstRequest;
  uint256 victimRequest;

  _depositWithUser(staleUser, amount, true);
  _depositWithUser(victim, amount, true);

  // Finish epoch 0 with a lower APR so epoch-1 requests enter instant mode.
  _startEpochAndCheckPrices(0);
  _stopEpochAndCheckPrices(0, initialProvidedApr / 2, _expectedFundsEndEpoch());

  vm.prank(staleUser);
  firstRequest = cdoEpoch.requestWithdraw(0, address(AAtranche));

  _startEpochAndCheckPrices(1);
  vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
  _getInstantFunds();

  // This pays the full aggregate receipt but leaves stale epoch ledgers populated.
  vm.prank(staleUser);
  cdoEpoch.claimInstantWithdrawRequest();

  assertEq(
    IdleCreditVault(address(strategy)).instantWithdrawsRequestsByEpoch(
      staleUser,
      IdleCreditVault(address(strategy)).epochNumber()
    ),
    firstRequest,
    "paid receipt remains in per-epoch accounting"
  );

  // A different user opens an unfunded instant request in the same strategy epoch.
  vm.prank(victim);
  victimRequest = cdoEpoch.requestWithdraw(0, address(AAtranche));

  // Borrower cannot repay; the honest manager stops the epoch and defaults.
  deal(defaultUnderlying, borrower, 0);
  vm.warp(cdoEpoch.epochEndDate() + 1);
  vm.prank(manager);
  cdoEpoch.stopEpoch(0, 0);
  assertTrue(cdoEpoch.defaulted());

  IdleCreditVault creditVault = IdleCreditVault(address(strategy));
  uint256 pendingBasis = creditVault.defaultPendingClaimBasis();

  assertEq(
    pendingBasis,
    firstRequest + victimRequest,
    "already-paid receipt is counted again"
  );

  // No external recovery is supplied. The stale receipt is nevertheless recorded
  // as prefunded reserve and produces a positive recovery price.
  vm.prank(manager);
  cdoEpoch.finalizeDefault(0, address(0));

  assertEq(creditVault.defaultRecoveryReserve(), firstRequest);
  assertGt(creditVault.defaultRecoveryPrice(), 0);
  assertEq(underlying.balanceOf(address(creditVault)), 0);

  // The victim's defaulted claim is positive in accounting but cannot be paid.
  vm.prank(victim);
  vm.expectRevert();
  cdoEpoch.claimInstantWithdrawRequest();
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L365-374)
```text
    // increase the instant withdraw requests for the user
    instantWithdrawsRequests[_user] += _amount;
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L387-392)
```text
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-648)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L683-692)
```text
    // Some recovery funds may already be in this strategy: partially prefunded instant requests
    // and borrower-send funds that failed at epoch start. Count both without pulling them again.
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L716-722)
```text
  function _defaultPrefundedInstantReserve() internal view returns (uint256 prefundedReserve) {
    uint256 pendingInstant = pendingInstantWithdraws;
    if (pendingInstant == 0) return prefundedReserve;
    uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
    if (instantBasis > pendingInstant) {
      prefundedReserve = instantBasis - pendingInstant;
    }
```
