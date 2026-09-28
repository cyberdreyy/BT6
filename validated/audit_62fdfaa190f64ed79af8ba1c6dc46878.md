### Title
Carried management fees are permanently stranded when the credit pool closes - ([contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol#L451))

### Summary
`IdleCDOEpochVariant._stopEpoch` carries forward management fees when accrued `unclaimedFees` exceed gross epoch interest. On a normal epoch stop this is safe because a later stop can pay the remainder. When the pool is closed with `_interest == 1`, however, the contract recalls gross principal that includes the fee backing, but calculates `_grossInterest` as zero. The fee payout is therefore clamped to zero, while `unclaimedFees` remains nonzero. Because closing sets `epochDuration` and `epochEndDate` to zero, no later epoch can pay the carried fees, and no separate fee-recipient claim exists.

### Finding Description
`_accrueManagementFee` adds elapsed management fees to `unclaimedFees`, and `_updateAccounting` also adds performance fees while reducing `getContractValue` by that liability. [1](#0-0) [2](#0-1) 

During a normal `stopEpoch`, fees are paid only from `grossInterest - pendingWithdrawFees`; any excess remains in `unclaimedFees`. [3](#0-2) 

When `_interest == 1`, the function adds all strategy-token principal to the amount recalled from the borrower. That explicitly includes principal backing carried fees. [4](#0-3) 

The recalled principal is then transferred to `IdleCreditVault`, but `_grossInterest` remains zero. Consequently, `_availableForFees` is zero, `_transferFeeUnderlyings(0)` is called, and the carried liability remains in `unclaimedFees`. [5](#0-4) [6](#0-5) 

Finally, pool closure sets `epochDuration = 0` and `epochEndDate = 0`. `startEpoch` rejects any future epoch when `epochDuration == 0`, so no subsequent `_stopEpoch` can settle the carried fees. [7](#0-6) [8](#0-7) 

There is no external fee-claim path. `_transferFeeUnderlyings` is internal and only sends accrued fees when invoked by epoch settlement. [9](#0-8) 

The repository's own test establishes the vulnerable state: after a low-interest epoch, `carriedFee` remains, a new epoch is started, and close-pool mode recalls gross principal while leaving `unclaimedFees` unchanged. [10](#0-9) 

### Impact Explanation
All carried management fees present at pool closure are permanently frozen.

For example, with `10,000` underlying under management and a `2%` annual management fee, one epoch accrues approximately `200` underlying. If gross epoch interest is only `100`, the stop pays `100` and carries `100`. A subsequent close recalls all `10,000` strategy-token principal, including the `100` excluded from user NAV, but pays zero additional fees because `_grossInterest` is zero.

After users withdraw their net NAV, approximately `100` underlying remains represented by CDO-held strategy tokens or underlying held by `IdleCreditVault`, while `unclaimedFees == 100`. Neither `feeReceiver` nor the owner can withdraw it through a fee-claim path.

This is permanent freezing of accrued protocol yield, not merely delayed payment.

### Likelihood Explanation
The sequence uses only intended protocol behavior:

1. A permitted depositor supplies funds.
2. Management fees accrue while an epoch runs.
3. An epoch ends with gross interest below accrued management fees.
4. The owner or manager later opens another epoch and closes the pool through the intended `_interest == 1` path.

No privileged role needs to act maliciously. Pool closure is a supported terminal state, and the code deliberately recalls fee-backed gross principal but fails to reserve or distribute the carried fee portion.

The likelihood depends on a pool closing while `unclaimedFees > 0`. This is most likely when management fees exceed the final gross interest, after a low-APR epoch, or after fees were deliberately allowed to carry forward.

### Recommendation
On pool closure, settle carried fees from the recalled gross principal before transferring the remainder to `IdleCreditVault`.

Specifically:

- In close-pool mode, calculate `feesToPay = min(unclaimedFees, _totBorrowed)` or the exact portion of recalled principal reserved for fees.
- Transfer `feesToPay` through `_transferFeeUnderlyings`.
- Reduce `unclaimedFees` by the same amount.
- Transfer only the remaining net principal to `IdleCreditVault` for user withdrawal receipts.
- Add a regression test asserting that closing a pool with carried fees clears `unclaimedFees`, pays `feeReceiver` and the owner according to `feeSplit`, and leaves exactly user NAV backing in the strategy.

Alternatively, retain an immutable fee liability and implement an access-controlled claim function that releases only the recorded `unclaimedFees` amount. That function must not be able to consume user withdrawal reserves.

### Proof of Concept
The following regression test follows the existing `IdleCreditVault.t.sol` fixture. It creates a carried fee, performs a fully funded pool close, and demonstrates that the returned fee backing remains stranded while `unclaimedFees` is unchanged.

```solidity
// test/foundry/IdleCreditVault.t.sol
function testClosePoolStrandsCarriedManagementFees() external {
    uint256 amount = 10_000 * ONE_SCALE;
    uint256 managementFeeRate = 2_000; // 2%
    uint256 lowGrossInterest = 100 * ONE_SCALE;

    // Unprivileged user deposits into AA.
    idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);

    // 100% of fees go to the configured receiver for easy accounting.
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, managementFeeRate);

    vm.startPrank(manager);
    cdoEpoch.setEpochParams(365 days, 0);
    IdleCreditVault(address(strategy)).setApr(initialProvidedApr);
    cdoEpoch.startEpoch();
    vm.stopPrank();

    // The epoch earns less than the accrued management fee.
    deal(defaultUnderlying, borrower, lowGrossInterest);
    vm.warp(cdoEpoch.epochEndDate());
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, lowGrossInterest);

    uint256 carriedFee = cdoEpoch.unclaimedFees();
    assertGt(carriedFee, 0, "setup must create carried fees");

    // Prevent additional fee accrual in the terminal epoch.
    _setManagementFee(0);

    vm.prank(manager);
    cdoEpoch.startEpoch();

    // Gross principal includes backing for the carried fee.
    uint256 grossPrincipal = strategyToken.balanceOf(address(cdoEpoch));
    assertEq(
        grossPrincipal,
        cdoEpoch.getContractValue() + carriedFee,
        "principal must include fee backing"
    );

    uint256 feeReceiverBefore = underlying.balanceOf(TL_MULTISIG);
    uint256 ownerBefore = underlying.balanceOf(owner);

    deal(defaultUnderlying, borrower, grossPrincipal);
    vm.warp(cdoEpoch.epochEndDate() + 1);

    // Close the pool through the intended stopEpoch interest sentinel.
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 1);

    // The close recalls gross principal, but pays no carried fee because
    // _grossInterest == 0 and therefore _availableForFees == 0.
    assertEq(underlying.balanceOf(TL_MULTISIG), feeReceiverBefore);
    assertEq(underlying.balanceOf(owner), ownerBefore);
    assertEq(cdoEpoch.unclaimedFees(), carriedFee);

    // Closing is terminal, so no subsequent epoch can pay the liability.
    assertEq(cdoEpoch.epochDuration(), 0);
    assertEq(cdoEpoch.epochEndDate(), 0);

    vm.prank(manager);
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.startEpoch();
}
```

A fork deployment can reproduce the same state with the production deployment's owner/manager accounts impersonated by Foundry and an unprivileged lender supplying the initial deposit. The decisive assertion is that `unclaimedFees` remains nonzero after `epochEndDate` is set to zero, proving that the fee-backed assets have been recalled into vault-controlled balances but can no longer be distributed through the only settlement path.

### Citations

**File:** contracts/IdleCDOCreditVault.sol (L227-232)
```text
    uint256 nav = getContractValue();
    uint256 _aprSplitRatio = trancheAPRSplitRatio;
    // If gain is > 0, then collect some fees in `unclaimedFees`
    if (nav > _lastNAV) {
      unclaimedFees += (nav - _lastNAV) * fee / FULL_ALLOC;
    }
```

**File:** contracts/IdleCDOCreditVault.sol (L552-555)
```text
  function _accrueManagementFee() internal {
    unclaimedFees += _calculateManagementFee(_managedContractValue(), block.timestamp - latestHarvestBlock);
    latestHarvestBlock = block.timestamp;
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L585-590)
```text
  /// @notice transfer fee to feeReceiver and owner according to feeSplit
  /// @param _amount total fee amount to split and transfer (in underlyings)
  function _transferFeeUnderlyings(uint256 _amount) internal {
    uint256 feeReceiverAmount = _feeReceiverAmount(_amount);
    _transferUnderlyings(feeReceiver, feeReceiverAmount);
    _transferUnderlyings(owner(), _amount - feeReceiverAmount);
```

**File:** contracts/IdleCDOEpochVariant.sol (L236-240)
```text
    // Check that buffer period passed (and epoch is not running as epochEndDate is set)
    // and that the pool is not closed (ie epochDuration == 0)
    uint256 _epochDuration = epochDuration; 
    _checkNotAllowed(defaulted || block.timestamp < (epochEndDate + bufferPeriod) || _epochDuration == 0);
    _checkProgrammableBorrowerMode();
```

**File:** contracts/IdleCDOEpochVariant.sol (L367-373)
```text
    if (_isRequestingAllFunds) {
      // Recall gross strategy-token principal, including fee backing excluded from net CDO NAV.
      // Prefunded variants also add queue deposits already sent directly to the borrower.
      _totBorrowed += _contractTokenBalance(strategyToken);
      _expectedInterest += _totBorrowed;
    }
    uint256 _grossInterest = _isRequestingAllFunds ? _expectedInterest - _totBorrowed : _expectedInterest;
```

**File:** contracts/IdleCDOEpochVariant.sol (L422-425)
```text
      if (_isRequestingAllFunds) {
        // we already have strategyTokens equal to _totBorrowed in this contract
        // so we transfer _totBorrowed to the strategy to avoid double counting for getContractValue
        _transferUnderlyings(address(_strategy), _totBorrowed);
```

**File:** contracts/IdleCDOEpochVariant.sol (L439-459)
```text
      uint256 _fees = unclaimedFees;
      if (_mintInterest) {
        // If interest is minted then we mint new shares for fee receivers instead of transferring underlyings
        if (_fees != 0) {
          uint256 feeReceiverAmount = _feeReceiverAmount(_fees);
          if (feeReceiverAmount != 0) {
            _mintSharesAtCurrPrice(feeReceiverAmount, feeReceiver, AATranche);
          }
          _mintSharesAtCurrPrice(_fees - feeReceiverAmount, owner(), AATranche);
          _updateSplitRatio(_getAARatio(true));
        }
      } else {
        // Cash-funded fees can only use gross interest not already owed to pending withdrawals.
        uint256 _availableForFees = _grossInterest > _pendingWithdrawFees ? _grossInterest - _pendingWithdrawFees : 0;
        if (_fees > _availableForFees) {
          _fees = _availableForFees;
        }
        _transferFeeUnderlyings(_fees);
      }
      // Any fee that cannot be paid in cash remains accrued and continues reducing NAV.
      unclaimedFees -= _fees;
```

**File:** contracts/IdleCDOEpochVariant.sol (L488-492)
```text
      if (_isRequestingAllFunds) {
        // user will request only normal withdraw and can claim right after
        disableInstantWithdraw = true;
        epochDuration = 0;
        epochEndDate = 0;
```

**File:** test/foundry/IdleCreditVault.t.sol (L1154-1172)
```text
    uint256 carriedFee = cdoEpoch.unclaimedFees();
    assertGt(carriedFee, 0, 'test requires a carried fee');
    _setManagementFee(0);

    vm.prank(manager);
    cdoEpoch.startEpoch();
    uint256 grossPrincipal = strategyToken.balanceOf(address(cdoEpoch));
    assertEq(grossPrincipal, cdoEpoch.getContractValue() + carriedFee, 'gross principal should include fee backing');
    assertEq(cdoEpoch.expectedEpochInterest(), 0, 'second epoch should have zero interest');

    deal(defaultUnderlying, borrower, grossPrincipal);
    uint256 borrowerBalancePre = underlying.balanceOf(borrower);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 1);

    assertEq(borrowerBalancePre - underlying.balanceOf(borrower), grossPrincipal, 'close did not recall gross principal');
    assertEq(cdoEpoch.unclaimedFees(), carriedFee, 'unfunded fee debt should remain backed');
    assertFalse(cdoEpoch.defaulted(), 'grossly funded close should not default');
```
