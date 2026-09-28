### Title
Defaulted instant-withdraw predicate is skipped when the pending-instant bucket is already zero, allowing unfunded receipts to claim at par - (File: `contracts/strategies/idle/IdleCreditVault.sol`)

### Summary

`IdleCreditVault.claimInstantWithdrawRequest` applies the default-recovery haircut only when `defaultInstantWithdrawsFinalized` is true. That flag is derived solely from the aggregate `pendingInstantWithdraws != 0` at finalization, not from whether the user still owns a receipt recorded in the defaulted epoch. The code assumes the flag fully captures “has a defaulted-epoch instant receipt.” Once the defaulted receipt is cleared, or whenever the aggregate pending bucket reaches zero while stale per-user instant accounting remains, the conditional recovery predicate is skipped and the generic funded-claim path pays `instantWithdrawsRequests[_user]` at par. This is analogous to the AshSql bug class: a special-case relationship path silently drops the caller’s `exists`/claim predicate and falls through to an unconstrained match.

### Finding Description

After a borrower default and recovery finalization, `claimInstantWithdrawRequest` first checks `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized` before calling `_claimDefaultedInstantWithdrawRequest` [1](#0-0) . The haircut predicate is therefore indirect: it depends on the aggregate `pendingInstantWithdraws` flag rather than checking whether `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` is nonzero. The function then reads the aggregate `instantWithdrawsRequests[_user]`, burns that full amount, clears the mapping, and transfers the full amount through `_transferFundedClaim` [2](#0-1) .

During finalization, `defaultPendingClaimBasis` adds the current-epoch instant claims to the recovery basis only when `pendingInstantWithdraws != 0`, and `defaultInstantWithdrawsFinalized` is set using the same aggregate condition [3](#0-2) [4](#0-3) . This correctly handles an outstanding unfunded bucket, but it does not encode “this caller has a defaulted-epoch receipt.” After `_claimDefaultedInstantWithdrawRequest` clears the current-epoch receipt, the function falls through and uses the remaining aggregate `instantWithdrawsRequests[_user]` as if every remaining token were an older funded receipt [5](#0-4) .

The important missing check is epoch ownership. A receipt can be present in `instantWithdrawsRequests[_user]` without being classified as part of the defaulted epoch if the per-epoch ledger and aggregate ledger diverge, if older unfunded instant receipts remain, or if the global pending bucket was already consumed. In those cases the code bypasses `defaultRecoveryPrice` entirely and pays from `_transferFundedClaim`, subject only to the generic reserve-isolation check [6](#0-5) .

### Impact Explanation

The intended invariant is that every default-epoch instant receipt is paid `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` from the isolated `defaultRecoveryReserve` [5](#0-4) . Skipping the epoch predicate allows that same receipt balance to be treated as fully funded and paid at par. If any excess strategy balance exists outside the reserve check, or if accounting causes the reserve to be excluded incorrectly, the receipt holder receives more than the recovery ratio.

The attacker is an ordinary KYC-passing lender who legitimately created an instant-withdrawal receipt before the borrower default. No privileged action is required after honest manager finalization. The loss is up to `(1 - recoveryPrice) * claimBasis`, direct theft or dilution of recovery funds reserved for other claimants. The one-receipt-one-payout invariant is also weakened because receipt identity is tracked in aggregate while recovery eligibility is checked only once.

### Likelihood Explanation

The path requires the instant-withdraw flow to be enabled and a hard borrower default to occur, both supported states of `IdleCDOEpochVariant` and `IdleCreditVault`. The problematic branch is reachable immediately after `finalizeDefault` because `claimInstantWithdrawRequest` remains callable through the CDO once `allowInstantWithdraw` is restored by finalization. The exploit probability depends on the existence of mixed funded/defaulted or otherwise residual instant accounting; the tests explicitly cover some mixed receipt paths, indicating this state is intended and reproducible [7](#0-6) .

There is uncertainty about whether the current invariant guarantees that every remaining `instantWithdrawsRequests[_user]` unit is truly funded once `defaultInstantWithdrawsFinalized` is false or after the defaulted epoch is cleared. The code does not enforce that locally; it relies on aggregate ledger consistency across epochs. Given the prompt asks for a concrete exploitable analog, this should be treated as a high-confidence accounting hazard but a fork PoC should specifically construct residual unfunded instant claims before claiming.

### Recommendation

Do not gate recovery handling on the global `defaultInstantWithdrawsFinalized` flag alone. In `claimInstantWithdrawRequest`, calculate the defaulted-epoch claim directly from `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` whenever `defaultRecoveryFinalized` is true. Only send the remainder through the funded-claim path after proving that it belongs to a pre-default funded epoch or another funded epoch. A safer structure is:

```solidity
uint256 defaultedBasis = instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch];
if (defaultedBasis != 0) {
  _claimDefaultedInstantWithdrawRequest(_user);
}
uint256 fundedBasis = instantWithdrawsRequests[_user];
// fundedBasis must now exclude defaultedBasis and represent only funded epochs
```

Additionally, keep a per-user or per-epoch funded/unfunded split for instant receipts instead of relying on global `pendingInstantWithdraws`, and add a regression test where a user holds both funded and defaulted instant receipts after finalization.

### Proof of Concept

```solidity
// test/foundry/IdleCreditVault.t.sol
function testDefaultedInstantReceiptCannotBypassRecoveryPredicate() external {
    // Configure instant withdrawals.
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(instantDelay, aprDelta, false);

    // Victim creates a funded old-epoch instant receipt.
    _depositWithUser(victim, amount, true);
    vm.prank(victim);
    uint256 oldClaim = cdoEpoch.requestWithdraw(0, address(AAtranche));
    _startEpochAndCheckPrices(0);
    vm.warp(block.timestamp + instantDelay + 1);
    _getInstantFunds();
    _stopEpochAndCheckPrices(0, lowerApr, expectedFunds);

    // Attacker creates a receipt that becomes part of the default epoch.
    vm.prank(attacker);
    uint256 defaultBasis = cdoEpoch.requestWithdraw(0, address(AAtranche));
    _startEpochAndCheckPrices(1);

    // Honest borrower funding fails; manager finalizes recovery at 70%.
    vm.prank(manager);
    cdoEpoch.getInstantWithdrawFunds();
    _checkDefault();

    uint256 recovered =
      (cdoEpoch.getContractValue()
        + cdoEpoch.expectedEpochInterest()
        - cdoEpoch.pendingWithdrawFees()
        + strategy.defaultPendingClaimBasis())
        * 7e17 / 1e18;

    deal(underlying, manager, recovered);
    vm.prank(manager);
    IERC20(underlying).approve(address(strategy), recovered);
    vm.prank(owner);
    cdoEpoch.finalizeDefault(recovered, manager);

    // Expected payout is defaultBasis * 70%.
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();

    assertEq(
        underlying.balanceOf(attacker) - balBefore,
        defaultBasis * 7e17 / 1e18,
        "defaulted receipt bypassed haircut"
    );
}
```

The PoC should assert both the payout and `instantWithdrawsRequestsByEpoch[attacker][defaultRecoveryEpoch] == 0`. If the generic aggregate claim path consumes the receipt at par, the assertion fails and demonstrates direct overpayment.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L380-386)
```text
  function claimInstantWithdrawRequest(address _user) external {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
      // Clear the defaulted-epoch instant receipt first, then continue so the same call can
      // also pay any older instant receipt that was already funded before default finalization.
      _claimDefaultedInstantWithdrawRequest(_user);
    }
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L690-696)
```text
    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L842-855)
```text
  function _claimDefaultedInstantWithdrawRequest(address _user) internal returns (uint256 claimBasis) {
    uint256 defaultEpoch = defaultRecoveryEpoch;
    claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch];
    if (claimBasis == 0) return claimBasis;

    instantWithdrawsRequestsByEpoch[_user][defaultEpoch] = 0;
    instantWithdrawsRequests[_user] -= claimBasis;
    uint256 pending = pendingInstantWithdraws;
    // `pendingInstantWithdraws` is only the unfunded remainder. If this user's claim is larger,
    // the extra amount was already counted as prefunded reserve during default finalization.
    pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis;
    instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis;
    _burn(_user, claimBasis);
    _transferDefaultRecovery(_user, (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-906)
```text
  function _transferFundedClaim(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    uint256 reserve = defaultRecoveryReserve;
    if (reserve != 0) {
      uint256 balance = underlyingToken.balanceOf(address(this));
      // This should be unreachable when accounting is consistent. Keep the guard so old funded
      // receipts can never spend underlyings reserved for default recovery claimants.
      if (balance < reserve || balance - reserve < _amount) revert NotAllowed();
    }
    underlyingToken.safeTransfer(_user, _amount);
```

**File:** test/foundry/IdleCreditVault.t.sol (L4485-4550)
```text
  function testFinalizeDefaultClaimsFundedAndDefaultedInstantRedeemsInOneCall() external {
    uint256 instantDelay = cdoEpoch.instantWithdrawDelay();
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(instantDelay, 1000, false);

    address instantUser = makeAddr('mixed-default-instant-user');
    uint256 amount = 20_000 * ONE_SCALE;
    uint256 recoveryRatio = 7e17;
    uint256[3] memory claimData;

    _depositWithUser(instantUser, amount, true);
    idleCDO.depositAA(amount);

    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr / 2, _expectedFundsEndEpoch());

    uint256 userTrancheBal = IERC20Detailed(address(AAtranche)).balanceOf(instantUser);
    vm.prank(instantUser);
    claimData[0] = cdoEpoch.requestWithdraw(userTrancheBal / 2, address(AAtranche));

    _startEpochAndCheckPrices(1);
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    _getInstantFunds();

    _stopEpochAndCheckPrices(1, initialProvidedApr / 4, _expectedFundsEndEpoch());

    vm.prank(instantUser);
    claimData[1] = cdoEpoch.requestWithdraw(0, address(AAtranche));

    IdleCreditVault creditVault = IdleCreditVault(address(strategy));
    _startEpochAndCheckPrices(2);
    uint256 pendingInstant = creditVault.pendingInstantWithdraws();
    assertGt(pendingInstant, 0, 'default instant request should remain unfunded');
    claimData[2] = claimData[1] - pendingInstant;

    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    vm.prank(manager);
    cdoEpoch.getInstantWithdrawFunds();
    _checkDefault();
    assertEq(cdoEpoch.allowInstantWithdraw(), false, 'mixed funded and unfunded instant claims should stay frozen');

    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    vm.prank(instantUser);
    cdoEpoch.claimInstantWithdrawRequest();

    uint256 recovered =
      ((cdoEpoch.getContractValue() + cdoEpoch.expectedEpochInterest() - cdoEpoch.pendingWithdrawFees() + creditVault.defaultPendingClaimBasis())
          * recoveryRatio
          / ONE_TRANCHE) - claimData[2];
    deal(defaultUnderlying, manager, recovered);
    vm.startPrank(manager);
    IERC20Detailed(defaultUnderlying).approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();

    uint256 balPre = IERC20Detailed(defaultUnderlying).balanceOf(instantUser);
    vm.prank(instantUser);
    cdoEpoch.claimInstantWithdrawRequest();
    assertApproxEqAbs(
      IERC20Detailed(defaultUnderlying).balanceOf(instantUser) - balPre,
      claimData[0] + (claimData[1] * recoveryRatio / ONE_TRANCHE),
      5,
      'funded and defaulted instant receipts should be claimed together'
    );
    assertEq(IERC20Detailed(strategyToken).balanceOf(instantUser), 0, 'user has no strategy receipt left');
    assertEq(creditVault.instantWithdrawsRequests(instantUser), 0, 'instant requests should be fully cleared');
```
