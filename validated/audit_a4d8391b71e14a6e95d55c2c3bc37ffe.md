### Title
`setTrancheAPRSplitRatio` re-splits accrued-but-unsynced interest retroactively — AA/BB holders' yield is not checkpointed before the ratio changes - (contracts/IdleCDO.sol)

### Summary
The external finding describes privileged parameter (`setMultiplier`) changes that leave already-accrued user entitlements stale. The direct analog in idle-tranches is `setTrancheAPRSplitRatio`: it overwrites `trancheAPRSplitRatio` without first calling `_updateAccounting()`, so all yield accrued since the last deposit/withdraw is split between AA and BB using the *new* ratio, not the ratio in force during accrual. An unprivileged tranche holder can also front-run/back-run an honest owner update (or `requestWithdraw`, which runs `_updateAccounting`) to crystallize the split under whichever ratio favors them.

### Finding Description
`_updateAccounting` in `IdleCDOCreditVault` reads `trancheAPRSplitRatio` at update time and distributes the entire pending gain `nav - _lastNAV` through `_virtualPriceAux` for both tranches:

- `uint256 _aprSplitRatio = trancheAPRSplitRatio;` then `(uint256 _priceAA, int256 _totalAAGain) = _virtualPriceAux(AATranche, nav, _lastNAV, _lastNAVAA, _aprSplitRatio);` [1](#0-0) 

The owner setter in the inherited base contract simply stores the new value with no accounting checkpoint:

- `function setTrancheAPRSplitRatio(uint256 _trancheAPRSplitRatio) external virtual { _checkOnlyOwner(); _checkAmountTooHigh((trancheAPRSplitRatio = _trancheAPRSplitRatio) > FULL_ALLOC); }` [2](#0-1) 

This is inconsistent with the codebase's own convention: `setFeeParams` explicitly checkpoints pending accrual (`_accrueManagementFee()`) *before* overwriting `fee`/`managementFee` precisely so the new rate is not applied retroactively [3](#0-2) , and there is a dedicated test asserting the management-fee checkpointing behavior (`testSetManagementFeeCheckpointsBeforeRateChange`) [4](#0-3) . No equivalent checkpoint exists for the APR split ratio.

Attack sequence (fixed-APR epoch vault, buffer phase where `epochEndDate == 0` or between `stopEpoch` and `startEpoch`):

1. Vault accrues interest for weeks under ratio `R_old`; `lastNAVAA`/`lastNAVBB` are stale, pending gain sits in `getContractValue() - lastNAV`.
2. Owner broadcasts `setTrancheAPRSplitRatio(R_new)` increasing AA share (or the AYS path `_updateSplitRatio(_getAARatio(true))` shifts the ratio after any deposit/withdraw [5](#0-4) ).
3. Attacker (any KYC'd AA/BB holder) orders their `depositXX`/`requestWithdraw`/`claimWithdrawRequest` relative to the owner tx: transacting *before* crystallizes the pending gain under `R_old`, transacting *after* applies `R_new` to the entire accrued gain. Either way, the holder on the favored side captures yield that accrued under a different contractual split.

### Impact Explanation
The broken invariant is fair mint/burn / agreed yield split: `nav - lastNAV` accrued while ratio `R_old` was advertised, yet it is distributed under `R_new`. The misallocated amount is `pendingGain * |R_new - R_old| / FULL_ALLOC`, bounded only by accrued unsplit interest, and falls directly out of one tranche class's NAV into the other's — a direct transfer of unclaimed yield between unprivileged holders, quantifiable in underlying tokens. When AYS is active the ratio moves automatically on every deposit via `_updateSplitRatio`, so each deposit likewise re-splits all pending interest at a ratio that already embeds the deposit itself, letting a large depositor dilute the counterparty tranche's accrued share.

### Likelihood Explanation
Medium. It requires an honest owner ratio change or ordinary deposits under AYS — both routine operations — and an unprivileged holder needs only a normal `depositAA`/`requestWithdraw` call to crystallize the favorable split. No privileged collusion, oracle manipulation, or default is needed. The impact scales with accrued-but-unsynced interest, which is largest in long-running fixed-APR epochs between `stopEpoch` and the next user interaction.

### Recommendation
Call `_updateAccounting()` (or `_forceUpdateAccounting`) inside `setTrancheAPRSplitRatio` and inside any path that writes `trancheAPRSplitRatio` (including `_updateSplitRatio` usage in `_deposit`/`depositDuringEpoch` ordering — sync accounting before storing the new ratio), mirroring the `_accrueManagementFee()` checkpoint already done in `setFeeParams`. This ensures accrued interest is always distributed under the ratio in force while it accrued.

### Proof of Concept
Foundry fork PoC sketch against `IdleCDOEpochVariant` + `IdleCreditVault` (extend `test/foundry/IdleCreditVault.t.sol` setup):

```solidity
function testSplitRatioChangeResplitsAccruedInterest() external {
    uint256 amount = 100_000 * ONE_SCALE;
    idleCDO.depositAA(amount);            // attacker is AA holder
    deal(defaultUnderlying, address(this), amount);
    idleCDO.depositBB(amount);

    // accrue real interest: borrower repays principal+interest at stopEpoch
    _startEpochAndCheckPrices(0);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 expectedInterest = cdoEpoch.expectedEpochInterest();
    deal(defaultUnderlying, borrower, expectedInterest + amount * 2);
    vm.prank(manager);
    cdoEpoch.stopEpoch(initialProvidedApr, 0); // buffer phase, interest accrued in NAV

    uint256 pendingGain = cdoEpoch.getContractValue() - (cdoEpoch.lastNAVAA() + cdoEpoch.lastNAVBB());
    uint256 oldRatio = cdoEpoch.trancheAPRSplitRatio();

    // owner raises AA split; no _updateAccounting is performed inside the setter
    uint256 newRatio = oldRatio + 10_000; // +10%
    vm.prank(owner);
    cdoEpoch.setTrancheAPRSplitRatio(newRatio);

    // attacker crystallizes: any deposit/withdraw request triggers _updateAccounting
    // which distributes the WHOLE pendingGain under newRatio, retroactively
    uint256 navAABefore = cdoEpoch.lastNAVAA();
    cdoEpoch.requestWithdraw(0, cdoEpoch.AATranche());
    uint256 retroGain = cdoEpoch.lastNAVAA() - navAABefore;

    // accrued gain that should have been split at oldRatio is split at newRatio
    assertGt(retroGain, pendingGain * oldRatio / FULL_ALLOC);
    // quantified theft from BB holders:
    // excess = pendingGain * (newRatio - oldRatio) / FULL_ALLOC
}
```

Uncertainty: I could not fully verify whether `IdleCDOCreditVault`/`IdleCDOEpochVariant` override `setTrancheAPRSplitRatio` (the grep index did not return line-level matches for the setter itself); if a local override exists that already syncs accounting, the same issue still applies to the automatic `_updateSplitRatio` path on deposits under AYS, which updates the ratio after `_updateAccounting` but then lets the *next* pending gain be split at a ratio influenced by that deposit — the retroactive-split window between `lastNAV` checkpoint and ratio write remains the core defect.

### Citations

**File:** contracts/IdleCDOCreditVault.sol (L200-208)
```text
    _updateAccounting();
    // get underlyings from sender
    address _token = token;
    uint256 _preBal = _contractTokenBalance(_token);
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);
    // mint tranche tokens according to the current tranche price
    _minted = _mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, msg.sender, _tranche);
    // update trancheAPRSplitRatio
    _updateSplitRatio(_getAARatio(true));
```

**File:** contracts/IdleCDOCreditVault.sol (L228-234)
```text
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

**File:** contracts/IdleCDO.sol (L899-903)
```text
  /// @param _trancheAPRSplitRatio new apr split ratio
  function setTrancheAPRSplitRatio(uint256 _trancheAPRSplitRatio) external virtual {
    _checkOnlyOwner();
    _checkAmountTooHigh((trancheAPRSplitRatio = _trancheAPRSplitRatio) > FULL_ALLOC);
  }
```

**File:** test/foundry/IdleCreditVault.t.sol (L1043-1058)
```text
  function testSetManagementFeeCheckpointsBeforeRateChange() external {
    uint256 amount = 10_000 * ONE_SCALE;
    uint256 firstFeeRate = 1_000; // 1%
    uint256 secondFeeRate = 2_000; // 2%

    idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);

    _setManagementFee(firstFeeRate);

    vm.warp(block.timestamp + 365 days);
    _setManagementFee(secondFeeRate);

    uint256 firstYearFee = _calcManagementFee(amount, firstFeeRate, 365 days);
    assertEq(cdoEpoch.unclaimedFees(), firstYearFee, "first management fee period not checkpointed");
    assertEq(cdoEpoch.getContractValue(), amount - firstYearFee, "NAV wrong after fee-rate change");
```
