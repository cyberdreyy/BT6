### Title
`writeOffDeposit` skips management/performance fee accounting, so early exits via write-off avoid the fees charged to `requestWithdraw` - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
The external bug class is "a value-generating path collects value but omits the protocol fee that all sibling paths charge". In `IdleCDOEpochVariant`, a lender exiting through `requestWithdraw` pays upfront management + performance fees computed by `_totalWithdrawFees`, which are tracked in `pendingWithdrawFees` and paid to `feeReceiver`/`owner` at `stopEpoch`. The analogous early-exit path `writeOffDeposit` removes the same principal + projected interest from live NAV and `expectedEpochInterest`, but never computes or accrues any fee, so the write-off route is systematically cheaper than a withdrawal and fee receivers lose the fee income on that principal.

### Finding Description
`requestWithdraw` computes `totalFees = _totalWithdrawFees(principal, interest)` — an upfront management fee for `_withdrawRequestManagementFeeDuration()` (epoch duration + remaining buffer) plus a performance fee on projected interest — reduces the receipt by that amount, and accrues it into `pendingWithdrawFees`, which `stopEpoch` later distributes via `_transferFeeUnderlyings` / `unclaimedFees` [1](#0-0) [2](#0-1) [3](#0-2) .

`writeOffDeposit` performs the same economic operation — it converts tranche tokens to their underlying value, burns them, burns the corresponding strategy tokens, and removes the projected epoch interest from `expectedEpochInterest` — but contains no call to `_totalWithdrawFees` and never increments `pendingWithdrawFees` or `unclaimedFees` [4](#0-3) . The comment even says "remove the interest + fee from expectedEpochInterest" while only `interest` is subtracted and no fee is ever accrued anywhere.

A lender who wants to exit early can therefore choose between:
- `requestWithdraw` → receipt reduced by `_totalWithdrawFees` (management fee on principal for a full epoch + buffer, performance fee on interest), or
- `writeOffDeposit` via `WriteOffEscrow`/`writeOffDeposit` agreement → principal exits with zero fee, and the borrower only repays `expectedEpochInterest` minus the removed gross interest.

### Impact Explanation
Fee revenue owed to `feeReceiver` and `owner` is permanently uncollected on every write-off exit, and the written-off lender effectively redeems principal + avoids the fee that any comparable withdrawal would pay. Quantified: for a write-off of principal `P` with management fee rate `m` over duration `D = epochDuration + bufferPeriod` (the same duration used for withdrawal receipts), the lost fee is roughly `P * m * D / (FULL_ALLOC * 365 days)` plus the performance fee `fee * interest / FULL_ALLOC` on the removed interest. With e.g. 1–2% management fee, 10% performance fee, and multi-year epochs, this is a material, recurring loss on a privileged-but-legitimate flow that unprivileged lenders can reach through the `WriteOffEscrow` fulfilment path.

### Likelihood Explanation
Write-offs are a designed feature (`WriteOffEscrow.create/delete/fullfill` + `writeOffDeposit`), so the path is exercised in normal operation, not an edge case. Every write-off deterministically under-accruess fees relative to a withdrawal of the same size, since the code simply lacks the `_totalWithdrawFees`/`pendingWithdrawFees` accounting. There is no compensating fee charged elsewhere: `_withdrawOps` only reduces NAV and `expectedEpochInterest -= interest` only reduces the borrower's repayment obligation.

### Recommendation
Apply the same fee accounting as `requestWithdraw` inside `writeOffDeposit`: compute `totalFees = _totalWithdrawFees(_underlyings, interest)` (using the buffer-scaled interest consistent with the write-off's own scaling), and either
- add `totalFees` to `pendingWithdrawFees` so it is paid to fee receivers at the next `stopEpoch`, with the borrower still liable for the fee portion (i.e. do not subtract the fee component from `expectedEpochInterest`), or
- accrue it directly to `unclaimedFees`.

This aligns the write-off exit with the documented intent that all user exits pay the protocol its management/performance fee share.

### Proof of Concept
Foundry fork test sketch (mirror `test/foundry/IdleCreditVault.t.sol` helpers such as `_setFeeParams`, `_startEpochAndCheckPrices`, `writeOffDeposit`):

```solidity
function testWriteOffSkipsFees() external {
    // 10% performance fee, 2% management fee, all to TL_MULTISIG
    _setFeeParams(TL_MULTISIG, 10_000, FULL_ALLOC, 2_000);

    uint256 amount = 10_000 * ONE_SCALE;
    idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);

    // long epoch with buffer so _withdrawRequestManagementFeeDuration() is large
    vm.startPrank(manager);
    cdoEpoch.setEpochParams(365 days, 30 days);
    IdleCreditVault(address(strategy)).setApr(initialProvidedApr);
    cdoEpoch.startEpoch();
    vm.stopPrank();

    uint256 pendingFeesPre = cdoEpoch.pendingWithdrawFees();
    uint256 unclaimedPre   = cdoEpoch.unclaimedFees();

    // borrower write-off of the full AA position (epoch running)
    vm.prank(borrower);
    cdoEpoch.writeOffDeposit(AAtranche.balanceOf(address(this)), address(AAtranche));

    // BUG: no fee is accrued on the written-off principal + projected interest,
    // while an equivalent requestWithdraw would have charged _totalWithdrawFees.
    assertEq(cdoEpoch.pendingWithdrawFees(), pendingFeesPre, "no withdraw fee accrued on write-off");
    assertEq(cdoEpoch.unclaimedFees(), unclaimedPre, "no fee accrued to fee receivers");

    // expected fee that a normal withdrawal of the same principal would have paid:
    // ~ principal * mgmtFee * (epochDuration + bufferPeriod) / (FULL_ALLOC * 365 days)
    //   + fee * interest / FULL_ALLOC  --> currently lost.
}
```

A passing assertion on `pendingWithdrawFees`/`unclaimedFees` being unchanged (and borrower repayment reduced by the full gross `interest` at line 962) demonstrates the missing fee distribution.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L772-778)
```text
    uint256 principal = _underlyings;
    (uint256 interest, int256 diff) = _calcInterestWithdrawRequest(_underlyings, _tranche);
    uint256 totalFees = _totalWithdrawFees(principal, interest);
    // user is requesting principal + interest minus upfront management fee and net performance fee
    _underlyings = principal + interest - totalFees;
    // add expected fees to pending withdraw fees counter
    pendingWithdrawFees += totalFees;
```

**File:** contracts/IdleCDOEpochVariant.sol (L900-906)
```text
  function _totalWithdrawFees(uint256 _principal, uint256 _interest) private view returns (uint256) {
    uint256 _mgmtFee = _calculateManagementFee(_principal, _withdrawRequestManagementFeeDuration());
    // When interest covers management fees, interest - netGain equals management fee plus performance fee.
    return _mgmtFee >= _interest ?
      _mgmtFee :
      _interest - _netGainAfterFees(_interest, _mgmtFee);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L925-931)
```text
  function _withdrawRequestManagementFeeDuration() private view returns (uint256 _duration) {
    uint256 bufferEnd = epochEndDate + bufferPeriod;
    _duration = epochDuration;
    if (block.timestamp < bufferEnd) {
      _duration += bufferEnd - block.timestamp;
    }
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L944-962)
```text
    uint256 _underlyings = _trancheToUnderlyings(_amount, _tranche);
    // We now calculate how much interest + fee the tranche would have generated in the current epoch
    (uint256 interest, int256 diff) = _calcInterestWithdrawRequest(_underlyings, _tranche);
    // given that _calcInterestWithdrawRequest returns an interest and diff value meant to be used in requestWithdraw
    // it does not include the buffer period but given that the epoch is running expectedEpochInterest was
    // calculated with the buffer period included, so we need to scale the interest
    uint256 _epochDuration = epochDuration;

    // (interest + diff) gives the interest based on tvl as write off debt won't follow senior/junior interest split ratio
    // diff is positive for AA and negative for BB (in this case it won't be > of interest)
    interest = uint256(int256(interest) + diff) * (_epochDuration + bufferPeriod) / _epochDuration;

    // Burn tranche tokens and decrease lastNAV
    _withdrawOps(_amount, _underlyings, _tranche);
    // Burn strategy tokens and decrease NAV (1:1 with underlyings)
    IdleCreditVault(strategy).burnStrategyTokens(_underlyings);

    // remove the interest + fee from expectedEpochInterest
    expectedEpochInterest -= interest;
```
