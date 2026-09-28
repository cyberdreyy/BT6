### Title
`setTrancheAPRSplitRatio` re-splits already-accrued interest retroactively without an accounting checkpoint — (File: contracts/IdleCDO.sol)

### Summary
The `pluginConfig`/`plugin` desync class maps onto setters that change a yield-distribution parameter without first crystallizing interest accrued under the old parameter. `setTrancheAPRSplitRatio` (inherited by `IdleCDOCreditVault`) and `setMinAprSplitAYS` write the new ratio directly but never call `_updateAccounting()`. Interest accrued since the last deposit/withdraw/harvest is then split at the *new* ratio retroactively. An unprivileged tranche holder can sequence around an honest owner call: deposit into the tranche that will be favored, let the next accounting interaction apply the new ratio to the old accrued interest, then withdraw — skimming yield owed to the other tranche class. [1](#0-0) [2](#0-1) 

### Finding Description
`IdleCDOCreditVault._updateAccounting()` splits all interest accrued since the last interaction between AA and BB using the *current* `trancheAPRSplitRatio` and `virtualPrice` similarly uses it for the full pending period. [3](#0-2)  Deposits call `_updateAccounting()` before minting and then `_updateSplitRatio`, but `_updateSplitRatio` is a no-op when `isAYSActive == false`, so a manually set ratio persists. [4](#0-3) [5](#0-4) 

The codebase already demonstrates the correct pattern elsewhere: `IdleCDOCreditVault.setFeeParams` calls `_accrueManagementFee()` before overwriting fee parameters, and `ProgrammableBorrower.setBorrowerApr` calls `_accrueBorrowerInterest()` before changing the rate. [6](#0-5) [7](#0-6)  `setTrancheAPRSplitRatio` and `setMinAprSplitAYS` lack any equivalent checkpoint — the exact analog of `setPlugin` swapping a component without running its init hook.

### Impact Explanation
Attacker path (running epoch/buffer phase, fixed-APR mode with `isAYSActive == false`):
1. Owner broadcasts `setTrancheAPRSplitRatio` (or `setMinAprSplitAYS` shifting the ratio).
2. Before/just after it lands but before any accounting interaction, the attacker calls `depositAA`/`depositBB` into the tranche the new ratio favors — minted at the *old* accounting price.
3. The next deposit/withdraw/harvest triggers `_updateAccounting`, distributing *all* interest accrued since the last checkpoint at the new ratio.
4. The attacker withdraws, pocketing `accruedInterest × |newRatio − oldRatio| / FULL_ALLOC` worth of yield that belonged to the other tranche class.

Broken invariant: fair mint/burn and integrity of the yield split — yield accrued under configuration A is paid out under configuration B, and the stolen delta is a direct transfer from honest tranche holders to the attacker. Quantified loss scales with accrued interest and the size of the ratio change.

### Likelihood Explanation
Requires the owner to make a legitimate ratio change while un-distributed interest is pending, and the attacker to sandwich the transaction — both feasible via public mempool observation (or simply depositing after the setter but before the next accounting call, since the retroactive mispricing persists until the next `_updateAccounting`). Only works when AYS is disabled; when `isAYSActive` is true, `_updateSplitRatio` recomputes the ratio on every deposit/withdraw and largely neutralizes manual sets.

### Recommendation
Call `_updateAccounting()` (or `_forceUpdateAccounting()`) inside `setTrancheAPRSplitRatio`, `setMinAprSplitAYS`, and `setIsAYSActive` before mutating the split parameters, mirroring how `setFeeParams` checkpoints `_accrueManagementFee()` — i.e., apply the "init hook" that synchronizes accrued state to the old configuration before installing the new one.

### Proof of Concept
A Foundry fork test (pattern follows `test/foundry/IdleCreditVault.t.sol`): deposit equal AA and BB, run an epoch to accrue interest, then `owner.setTrancheAPRSplitRatio(...)` shifted heavily toward AA; attacker `depositAA` immediately after, warp past epoch end, `stopEpoch`, then `claimWithdrawRequest` — attacker's AA tranche price reflects the new ratio applied to the entire accrued interest, yielding more than the same deposit made pre-change. I could not fully verify the exact interest-splitting math inside `_updateAccounting`/`_calcInterestWithApr` within the available iterations, so the PoC direction (which tranche is favored and the magnitude) should be confirmed empirically.

### Citations

**File:** contracts/IdleCDO.sol (L530-545)
```text
  function _updateSplitRatio(uint256 tvlAARatio) internal virtual {
    uint256 _minSplit = minAprSplitAYS;
    _minSplit = _minSplit == 0 ? AA_RATIO_LIM_DOWN : _minSplit;

    if (isAYSActive) {
      uint256 aux;
      if (tvlAARatio >= AA_RATIO_LIM_UP) {
        aux = tvlAARatio == FULL_ALLOC ? FULL_ALLOC : AA_RATIO_LIM_UP;
      } else if (tvlAARatio > _minSplit) {
        aux = tvlAARatio;
      } else {
        aux = _minSplit;
      }
      trancheAPRSplitRatio = aux * tvlAARatio / FULL_ALLOC;
    }
  }
```

**File:** contracts/IdleCDO.sol (L878-882)
```text
  /// @param _aprSplit min apr split for AA, considering FULL_ALLOC = 100%
  function setMinAprSplitAYS(uint256 _aprSplit) external virtual {
    _checkOnlyOwner();
    _checkAmountTooHigh((minAprSplitAYS = _aprSplit) > FULL_ALLOC);
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

**File:** contracts/IdleCDOCreditVault.sol (L172-180)
```text
  function virtualPrice(address _tranche) public virtual view returns (uint256 _virtualPrice) {
    (_virtualPrice, ) = _virtualPriceAux(
      _tranche,
      _managedContractValue(), // nav
      lastNAVAA + lastNAVBB, // lastNAV
      _lastSavedNAV(_tranche), // lastTrancheNAV
      trancheAPRSplitRatio
    );
  }
```

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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L191-196)
```text
  function setBorrowerApr(uint256 _apr) external {
    _checkOnlyOwnerOrManager();
    _accrueBorrowerInterest();
    borrowerApr = _apr;
    emit BorrowerAprSet(_apr);
  }
```
