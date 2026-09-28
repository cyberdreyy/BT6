### Title
Performance-fee changes apply retroactively to unaccounted epoch gains - (File: contracts/IdleCDOCreditVault.sol)

### Summary
`setFeeParams` checkpoints only the management fee before changing `fee`; it does not crystallize the accrued performance fee. The next accounting update applies the newly configured `fee` to the entire gain accumulated since `lastNAVAA`/`lastNAVBB` was last updated. [1](#0-0) [2](#0-1) 

### Finding Description
`_accrueManagementFee` only adds the elapsed management fee to `unclaimedFees` and updates `latestHarvestBlock`; it does not update tranche NAV or reserve the performance fee calculated under the old rate. [3](#0-2) 

Subsequently, `_virtualPriceAux` calculates `totalGain` as current NAV minus the last saved NAV and subtracts `totalGain * fee / FULL_ALLOC` using the current value of `fee`. [2](#0-1) 

Therefore, if the owner lowers the performance fee during an epoch, tranche holders receive the historical epoch gain without the previously configured performance fee. If the owner raises the fee, the higher fee is charged against yield that accrued while a lower fee was configured. Accounting is performed automatically by deposits, withdrawals, and epoch settlement. [4](#0-3) 

### Impact Explanation
A KYC-passing tranche holder benefits pro rata from a retroactive fee reduction. For example, with a 10,000-underlying gain and an old 10% performance fee, reducing `fee` to zero before `stopEpoch` diverts the expected 1,000-underlying fee from `feeReceiver`/owner to current tranche holders. [5](#0-4) 

The same mechanism can permanently overcharge holders if the fee is increased, because the new rate applies to the entire uncheckpointed gain rather than only post-change yield. [6](#0-5) 

### Likelihood Explanation
The sequence requires only an ordinary owner fee update while unaccounted yield exists. The attacker does not need a privileged role, control the owner, or manipulate oracle data; holding or acquiring tranche tokens before the update is sufficient to receive the retroactive benefit. The existing management-fee checkpoint does not guard performance fees. [7](#0-6) 

### Recommendation
Before assigning `fee`, force a full accounting checkpoint that realizes gains and performance fees under the old rate. Management-fee accrual alone is insufficient. `setFeeParams` should effectively perform the equivalent of the normal accounting update—after donation skimming—and only then update `fee`, `feeSplit`, and `managementFee`.

### Proof of Concept
Add this test to `test/foundry/IdleCreditVault.t.sol`, which already provides `idleCDO`, `cdoEpoch`, `owner`, `manager`, `borrower`, `defaultUnderlying`, `_setFeeParams`, `_startEpochAndCheckPrices`, and `initialApr`. [8](#0-7) 

```solidity
function testPerformanceFeeChangeAppliesRetroactively() external {
    uint256 amount = 10_000 * ONE_SCALE;
    uint256 oldPerformanceFee = 10_000; // 10%

    _setFeeParams(
        TL_MULTISIG,
        oldPerformanceFee,
        FULL_ALLOC,
        cdoEpoch.managementFee()
    );

    idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);

    _startEpochAndCheckPrices(0);

    uint256 expectedInterest = cdoEpoch.expectedEpochInterest();
    uint256 expectedOldFee =
        expectedInterest * oldPerformanceFee / FULL_ALLOC;

    deal(defaultUnderlying, borrower, expectedInterest);

    // Honest owner intends to remove the fee only for subsequent yield.
    // No accounting checkpoint occurs before this write.
    vm.prank(owner);
    cdoEpoch.setFeeParams(
        TL_MULTISIG,
        0,
        FULL_ALLOC,
        cdoEpoch.managementFee()
    );

    vm.warp(cdoEpoch.epochEndDate());

    uint256 feeReceiverBefore =
        IERC20Detailed(defaultUnderlying).balanceOf(TL_MULTISIG);

    vm.prank(manager);
    cdoEpoch.stopEpoch(initialApr, 0);

    uint256 feeReceiverAfter =
        IERC20Detailed(defaultUnderlying).balanceOf(TL_MULTISIG);

    assertEq(
        feeReceiverAfter - feeReceiverBefore,
        0,
        "new zero fee was applied to yield accrued under old fee"
    );

    // Correct behavior would reserve approximately expectedOldFee before
    // applying the new rate to subsequent yield.
    assertGt(expectedOldFee, 0, "test requires a nonzero expected fee");
}
```

### Citations

**File:** contracts/IdleCDOCreditVault.sol (L305-314)
```text
    // Calculate the total gain/loss
    int256 totalGain = int256(_nav) - int256(_lastNAV);
    // Ordinary zero-delta interactions keep their saved price for compatibility. Forced
    // accounting recomputes NAV per share, which is required after discounted mid-epoch deposits.
    if (totalGain == 0 && !skipDefaultCheck) return (_tranchePrice(_tranche), 0);

    // Remove performance fee for gains
    if (totalGain > 0) {
      totalGain -= totalGain * int256(fee) / int256(FULL_ALLOC);
    }
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

**File:** contracts/IdleCDOCreditVault.sol (L484-490)
```text
  /// @notice this method updates the accounting of the contract and effectively splits the yield/loss between the
  /// AA and BB tranches. This can be called at any time as is called automatically on each deposit/redeem. It's here
  /// just to be called when a loss exhausted BB, as deposits/redeems are paused, but we need to
  /// crystallize the BB-first loss.
  function updateAccounting() external virtual {
    _checkOnlyOwnerOrGuardian();
    _forceUpdateAccounting();
```

**File:** contracts/IdleCDOCreditVault.sol (L550-555)
```text
  /// @notice Checkpoint accrued management fees into `unclaimedFees`.
  /// @dev Raw underlyings are excluded because unsolicited transfers are skimmed instead of managed.
  function _accrueManagementFee() internal {
    unclaimedFees += _calculateManagementFee(_managedContractValue(), block.timestamp - latestHarvestBlock);
    latestHarvestBlock = block.timestamp;
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L435-456)
```text
      // update tranche prices and unclaimed fees
      _updateAccounting();

      // transfer fees
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
```

**File:** test/foundry/IdleCreditVault.t.sol (L3618-3625)
```text
  function _setManagementFee(uint256 _managementFee) internal {
    _setFeeParams(cdoEpoch.feeReceiver(), cdoEpoch.fee(), cdoEpoch.feeSplit(), _managementFee);
  }

  function _setFeeParams(address _feeReceiver, uint256 _fee, uint256 _feeSplit, uint256 _managementFee) internal {
    vm.prank(owner);
    cdoEpoch.setFeeParams(_feeReceiver, _fee, _feeSplit, _managementFee);
  }
```
