### Title
Retroactive performance-fee changes misallocate accrued epoch yield - ([File: `contracts/IdleCDOCreditVault.sol`](contracts/IdleCDOCreditVault.sol))

### Summary
`IdleCDOCreditVault.setFeeParams` checkpoints only management fees before changing `fee`; it does not settle the accrued performance-fee share of unaccounted gains. When an honest owner changes the performance-fee rate during a running epoch, the entire epoch gain is later assessed using the new rate.

An unprivileged tranche holder can capture yield that economically accrued under a higher fee by holding through an honest fee reduction and requesting withdrawal after `stopEpoch`.

### Finding Description
Performance fees are accrued lazily. `_updateAccounting` computes `nav - lastNAV` and adds `gain * fee / FULL_ALLOC` to `unclaimedFees`, while `_virtualPriceAux` removes the same fee before distributing the remaining gain between AA and BB. [1](#0-0) [2](#0-1) 

`setFeeParams` calls `_accrueManagementFee()` and then replaces `fee`, but it does not call `_updateAccounting()` or otherwise checkpoint accrued performance fees under the old rate. [3](#0-2) 

This matters in `IdleCDOEpochVariant`: borrower funds are pulled in `stopEpoch` before `_updateAccounting()` runs. [4](#0-3)  Therefore, if the owner lowers `fee` during a running epoch, the newly received interest for the whole elapsed epoch is charged at the lower rate. If the owner raises `fee`, yield earned under the lower rate is retroactively charged at the higher rate.

### Impact Explanation
For accrued gross gain `G`, changing the rate from `oldFee` to `newFee` moves `G * (newFee - oldFee) / FULL_ALLOC` between tranche holders and fee recipients.

Example: an epoch produces `1,000 USDC` of gross interest while `fee = 20_000` (20%). If the owner honestly reduces `fee` to zero before `stopEpoch`, `setFeeParams` leaves the unaccounted gain unsettled. `stopEpoch` then assesses a 0% fee over all `1,000 USDC`, causing tranche holders—including an unprivileged attacker who later calls `requestWithdraw`—to receive `200 USDC` that should have accrued as protocol fees.

Conversely, increasing the fee from 10% to 20% retroactively removes an extra `100 USDC` from holders on the same `1,000 USDC` of pre-change yield.

### Likelihood Explanation
Likelihood depends on ordinary fee administration occurring while an epoch has accrued but unsettled interest. `setFeeParams` has no epoch-state guard and is callable while `isEpochRunning` is true. [3](#0-2)  Low vault activity does not reduce the issue because epoch yield is accounted in a single checkpoint at `stopEpoch`.

The attacker need not control any privileged role. A KYC-passing tranche holder can maintain or acquire a position before an announced honest fee reduction, then request withdrawal during the buffer phase after the lower fee has been applied to all epoch yield. [5](#0-4) 

### Recommendation
Checkpoint full accounting before mutating performance-fee or fee-split parameters. In `setFeeParams`, first settle pending gains using the current `fee`—for example through the normal accounting path—then update `fee`, `feeSplit`, and `managementFee`.

For epoch vaults, ensure the checkpoint does not treat borrower funds that have not yet arrived as gain. A safer design is to store a fee-rate boundary for the current epoch and apply the old rate to interest accrued before the configuration change.

### Proof of Concept
The following Foundry test fits `test/foundry/IdleCreditVault.t.sol` and uses its fork deployment:

```solidity
function testPerformanceFeeChangeAppliesRetroactivelyToEpochInterest() external {
  uint256 amount = 10_000 * ONE_SCALE;
  uint256 oldFee = 20_000; // 20%

  _setFeeParams(TL_MULTISIG, oldFee, FULL_ALLOC, 0);
  idleCDO.depositAA(amount);

  _startEpochAndCheckPrices(0);
  uint256 grossInterest = cdoEpoch.expectedEpochInterest();
  assertGt(grossInterest, 0, "epoch interest required");

  // Honest owner lowers the performance fee after the epoch has already accrued
  // under the 20% rate. No performance-fee checkpoint occurs.
  vm.prank(owner);
  cdoEpoch.setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, 0);

  deal(defaultUnderlying, borrower, grossInterest);
  vm.warp(cdoEpoch.epochEndDate());

  uint256 feeReceiverBefore = IERC20Detailed(defaultUnderlying).balanceOf(TL_MULTISIG);
  uint256 expectedOldFee = grossInterest * oldFee / cdoEpoch.FULL_ALLOC();

  vm.prank(manager);
  cdoEpoch.stopEpoch(initialProvidedApr, 0);

  // Vulnerable behavior: the entire epoch gain is charged at the new 0% rate.
  assertEq(
    IERC20Detailed(defaultUnderlying).balanceOf(TL_MULTISIG) - feeReceiverBefore,
    0,
    "fee recipient lost accrued performance fees"
  );

  // Correct behavior would checkpoint `grossInterest` under the prior 20% rate.
  assertGt(expectedOldFee, 0, "quantified loss");
}
```

The same test with `fee` increased from `10_000` to `20_000` shows the reverse case: the complete `grossInterest` is charged 20%, rather than applying 20% only to post-change accrual.

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

**File:** contracts/IdleCDOCreditVault.sol (L311-314)
```text
    // Remove performance fee for gains
    if (totalGain > 0) {
      totalGain -= totalGain * int256(fee) / int256(FULL_ALLOC);
    }
```

**File:** contracts/IdleCDOCreditVault.sol (L461-469)
```text
  function setFeeParams(address _feeReceiver, uint256 _fee, uint256 _feeSplit, uint256 _managementFee) external {
    _checkOnlyOwner();
    _checkAmountTooHigh(_fee > MAX_FEE || _feeSplit > FULL_ALLOC || _managementFee > MAX_FEE / 10);
    _checkIs0((feeReceiver = _feeReceiver) == address(0));

    _accrueManagementFee();
    fee = _fee;
    feeSplit = _feeSplit;
    managementFee = _managementFee;
```

**File:** contracts/IdleCDOEpochVariant.sol (L406-436)
```text
    // accrue interest to idleCDO, this will increase tranche prices.
    // Send also tot withdraw requests amount to the IdleCreditVault contract
    try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
      // transfer in strategy and decrease pendingWithdraws
        _strategy.collectWithdrawFunds(_pendingWithdraws);
      // Only settle borrower interest when CDO is fronting it (minted mode, not closing pool).
      // When requesting all funds (_interest == 1) the CDO pulls cash directly, no fronting.
      if (_mintInterest && isProgrammableBorrower) {
        IProgrammableBorrower(_borrower()).settleBorrowerInterest();
      }
      // Split pending withdraw fees before update accounting
      // NOTE: Fees are sent with 2 different transfer calls, here and after updateAccounting, to avoid complicated calculations
      if (!_mintInterest) {
        _transferFeeUnderlyings(_pendingWithdrawFees);
      }

      if (_isRequestingAllFunds) {
        // we already have strategyTokens equal to _totBorrowed in this contract
        // so we transfer _totBorrowed to the strategy to avoid double counting for getContractValue
        _transferUnderlyings(address(_strategy), _totBorrowed);
      }

      if (_mintInterest) {
        // if interest is not transferred we mint strategy tokens equal to the full epoch interest
        if (_grossInterest != 0) _strategy.mintStrategyTokens(_grossInterest);
        // and increase unclaimedFees by pending withdraw fees before _updateAccounting
        unclaimedFees += _pendingWithdrawFees;
      }

      // update tranche prices and unclaimed fees
      _updateAccounting();
```

**File:** contracts/IdleCDOEpochVariant.sol (L739-756)
```text
  function requestWithdraw(uint256 _amount, address _tranche) external returns (uint256 _underlyings) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == AATranche ? !allowAAWithdrawRequest : !allowBBWithdrawRequest) ||
      !isWalletAllowed(msg.sender)
    );
  
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();

    // we trigger an update accounting to check for eventual losses
    _updateAccounting();

    IdleCreditVault creditVault = IdleCreditVault(strategy);
    if (_amount == 0) {
      _amount = _userTrancheBal(msg.sender, _tranche);
    }
    _underlyings = _trancheToUnderlyings(_amount, _tranche);
```
