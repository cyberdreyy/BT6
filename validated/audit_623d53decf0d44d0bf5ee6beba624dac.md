### Title
`setFeeParams()` applies the new performance fee retroactively to unaccounted yield - (File: `contracts/IdleCDOCreditVault.sol`)

### Summary

`IdleCDOCreditVault.setFeeParams()` checkpoints only the management fee before updating the performance-fee parameters. It does not call accounting before replacing `fee` and `feeSplit`. Consequently, all gains accrued since the previous accounting checkpoint are charged using the new performance fee, even though part or all of those gains accrued while the old fee was in effect.

### Finding Description

`_updateAccounting()` calculates the gain since `lastNAVAA + lastNAVBB` was last checkpointed and adds the current `fee` percentage of that entire gain to `unclaimedFees`. The same current `fee` value is also deducted from the gain before calculating tranche prices. [1](#0-0) 

`setFeeParams()` changes `fee`, `feeSplit`, and `managementFee`, but only calls `_accrueManagementFee()` before doing so. [2](#0-1) 

Management-fee accrual does not checkpoint the performance fee or the gain stored between the current NAV and the saved tranche NAVs. The first later deposit, withdrawal, epoch settlement, or owner/guardian accounting call applies `fee` to the full uncheckpointed gain.

For example:

1. Performance fee is `0`.
2. The vault accrues `1,000` underlying of uncheckpointed gain.
3. The honest owner calls `setFeeParams(..., 10_000, ...)` to set a 10% fee.
4. A user subsequently calls a deposit or withdrawal function that invokes `_updateAccounting()`.
5. `unclaimedFees` receives approximately `100` underlying, although the entire `1,000` gain accrued while the configured fee was `0`.

The reverse sequence leaks value to tranche holders: gains accrued under a 10% fee are later charged a newly configured 0% fee.

### Impact Explanation

This breaks the intended fee-accounting invariant and causes direct value misallocation.

- Increasing `fee` retroactively takes yield from tranche holders and transfers it to the configured fee recipients.
- Decreasing `fee` retroactively forfeits protocol yield that accrued under the old rate and leaves it to tranche holders.
- Changing `feeSplit` before checkpointing can also alter who receives fees that accrued under the previous split, depending on when `unclaimedFees` is paid out.

For an uncheckpointed gain `G` and a fee change from `oldFee` to `newFee`, the misallocated amount is approximately:

```text
abs(newFee - oldFee) * G / FULL_ALLOC
```

The loss grows linearly with the uncheckpointed gain and can cover the entire interval since the last accounting call. An unprivileged tranche holder can sequence deposits or withdrawals around a public owner fee update to trigger accounting with the new fee or to lock in pricing before it changes. The privileged owner remains honest; the bug is that the privileged setter fails to settle the old accounting period before replacing the rate.

### Likelihood Explanation

Likelihood depends on fee updates occurring while uncheckpointed gains exist. Deposits and withdrawals checkpoint accounting opportunistically, so long-lived vaults with inactive users can accumulate a large uncheckpointed interval. Epoch settlement, mid-epoch deposits, and withdrawals are likely interactions that later expose the stale rate.

The issue does not require malicious privileged behavior, oracle manipulation, borrower default, or a paused/defaulted vault. It requires only:

1. A non-zero uncheckpointed gain.
2. An honest owner fee update.
3. A later accounting-triggering user transaction.

### Recommendation

Checkpoint accounting under the old performance-fee parameters before assigning the new ones.

Conceptually, `setFeeParams()` should perform a forced accounting update before changing `fee` and `feeSplit`, while preserving the existing management-fee checkpoint behavior:

```solidity
function setFeeParams(
    address _feeReceiver,
    uint256 _fee,
    uint256 _feeSplit,
    uint256 _managementFee
) external {
    _checkOnlyOwner();
    _checkAmountTooHigh(
        _fee > MAX_FEE ||
        _feeSplit > FULL_ALLOC ||
        _managementFee > MAX_FEE / 10
    );
    _checkIs0(_feeReceiver == address(0));

    _forceUpdateAccounting();

    feeReceiver = _feeReceiver;
    fee = _fee;
    feeSplit = _feeSplit;
    managementFee = _managementFee;
}
```

`_forceUpdateAccounting()` temporarily sets `skipDefaultCheck`, so care should be taken to preserve its existing shutdown/default semantics. If a full checkpoint is intentionally too invasive for this setter, it should at least settle `unclaimedFees`, `priceAA`, `priceBB`, `lastNAVAA`, and `lastNAVBB` under the old fee before changing fee-related parameters.

### Proof of Concept

A Foundry PoC can be built against the existing `IdleCreditVault.t.sol` fixtures:

```solidity
function testSetFeeParamsAppliesNewFeeRetroactively() external {
    uint256 amount = 10_000 * ONE_SCALE;

    // Start with a 0% performance fee.
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, 0);

    // Deposit and move the position into positive NAV without another
    // accounting checkpoint. The exact mechanism can be an elapsed epoch
    // with credited borrower yield or the existing test strategy's value
    // increase helper.
    idleCDO.depositAA(amount);
    uint256 uncheckpointedGain = 1_000 * ONE_SCALE;
    _increaseStrategyValue(uncheckpointedGain);

    uint256 feesBefore = cdoEpoch.unclaimedFees();

    // Honest owner raises the performance fee to 10%.
    _setFeeParams(TL_MULTISIG, 10_000, FULL_ALLOC, 0);

    // An unprivileged user triggers accounting. This may be any normal
    // deposit/withdrawal path that calls _updateAccounting().
    address user = makeAddr("accountingTrigger");
    uint256 depositAmount = 1 * ONE_SCALE;
    deal(defaultUnderlying, user, depositAmount);
    vm.startPrank(user);
    IERC20Detailed(defaultUnderlying).approve(address(cdoEpoch), depositAmount);
    cdoEpoch.depositAA(depositAmount);
    vm.stopPrank();

    // Expected if accounting had been checkpointed before the rate change.
    uint256 expectedFees = feesBefore;

    // Actual: the new 10% rate is charged against the entire old gain.
    uint256 actualFees = feesBefore + uncheckpointedGain * 10_000 / FULL_ALLOC;

    assertEq(cdoEpoch.unclaimedFees(), actualFees);
    assertGt(actualFees, expectedFees);
}
```

The decisive assertion is that `unclaimedFees` increases by `10%` of yield that fully accrued while `fee == 0`. A symmetric test can decrease the fee from `10_000` to `0` and show that the protocol permanently loses `10%` of the previously accrued gain.

### Citations

**File:** contracts/IdleCDOCreditVault.sol (L222-234)
```text
  function _updateAccounting() internal virtual returns (bool shutdown) {
    _accrueManagementFee();
    uint256 _lastNAVAA = lastNAVAA;
    uint256 _lastNAVBB = lastNAVBB;
    uint256 _lastNAV = _lastNAVAA + _lastNAVBB;
    uint256 nav = getContractValue();
    uint256 _aprSplitRatio = trancheAPRSplitRatio;
    // If gain is > 0, then collect some fees in `unclaimedFees`
    if (nav > _lastNAV) {
      unclaimedFees += (nav - _lastNAV) * fee / FULL_ALLOC;
    }
    (uint256 _priceAA, int256 _totalAAGain) = _virtualPriceAux(AATranche, nav, _lastNAV, _lastNAVAA, _aprSplitRatio);
    (uint256 _priceBB, int256 _totalBBGain) = _virtualPriceAux(BBTranche, nav, _lastNAV, _lastNAVBB, _aprSplitRatio);
```

**File:** contracts/IdleCDOCreditVault.sol (L461-470)
```text
  function setFeeParams(address _feeReceiver, uint256 _fee, uint256 _feeSplit, uint256 _managementFee) external {
    _checkOnlyOwner();
    _checkAmountTooHigh(_fee > MAX_FEE || _feeSplit > FULL_ALLOC || _managementFee > MAX_FEE / 10);
    _checkIs0((feeReceiver = _feeReceiver) == address(0));

    _accrueManagementFee();
    fee = _fee;
    feeSplit = _feeSplit;
    managementFee = _managementFee;
  }
```
