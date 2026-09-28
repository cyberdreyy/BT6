### Title
Performance-fee changes are applied retroactively to uncheckpointed gains - (File: `contracts/IdleCDOCreditVault.sol`)

### Summary
`IdleCDOCreditVault.setFeeParams` updates the performance fee after checkpointing only the management fee, leaving all yield accrued since the previous accounting checkpoint to be charged at the newly configured performance-fee rate. A KYC-passed tranche holder can submit the first deposit or withdrawal after an honest owner lowers `fee`, causing historical gains to be recalculated under the lower fee and withdrawing value that should have been booked as fees under the prior rate. [1](#0-0) [2](#0-1) 

### Finding Description
`_updateAccounting` first calls `_accrueManagementFee`, then calculates every gain since `lastNAVAA + lastNAVBB` and books `fee` against the entire accumulated NAV delta. [2](#0-1) 

`setFeeParams` calls only `_accrueManagementFee`; that helper checkpoints `managementFee` and advances `latestHarvestBlock`, but it does not split the already-accrued performance gain or update `lastNAVAA`, `lastNAVBB`, `priceAA`, or `priceBB`. [1](#0-0) [3](#0-2) 

Consequently, the new `fee` applies to gain generated while the old `fee` was active. During the buffer phase, a permitted lender can trigger `requestWithdraw`, which invokes `_updateAccounting` before calculating the receipt amount. [4](#0-3) 

### Impact Explanation
If the owner reduces `fee` from `F_old` to `F_new` while a positive uncheckpointed gain `G` exists, approximately `G * (F_old - F_new) / FULL_ALLOC` is reassigned from protocol fees to tranche holders. [5](#0-4) 

An attacker who is an ordinary KYC-passed lender can make the first withdrawal request after the fee reduction and receive a larger receipt than the historical fee configuration should permit. The loss is bounded by `MAX_FEE / FULL_ALLOC` of all uncheckpointed gains, up to 20% of those gains, and is directly quantifiable on fork state. [6](#0-5) [7](#0-6) 

### Likelihood Explanation
The attacker requires only a KYC-passed wallet, an existing tranche position, and the ability to submit the first permitted accounting interaction after an honest fee update. `requestWithdraw` supplies the trigger during the buffer phase and has no privileged-role requirement beyond `isWalletAllowed`. [8](#0-7) [9](#0-8) 

Existing controls do not prevent the sequence: `setFeeParams` is intentionally owner-controlled, `_accrueManagementFee` succeeds but checkpoints the wrong accounting dimension, and `_updateAccounting` remains callable indirectly through normal user interactions. [1](#0-0) [10](#0-9) 

### Recommendation
Before assigning a new `fee`, `feeSplit`, or `managementFee`, run the complete accounting checkpoint so the pending NAV gain is divided under the old fee parameters. In the normal non-default path this should invoke `_updateAccounting`; where a loss must be crystallized, the owner path should use `_forceUpdateAccounting`. [11](#0-10) 

### Proof of Concept
A Foundry fork test can reproduce the retroactive fee assignment:

```solidity
function testFeeChangeIsRetroactive() public {
    // Existing holder deposits while fee = F_old and yield subsequently accrues.
    idleCDO.depositAA(amount);

    // Realize positive uncheckpointed NAV according to the fork's strategy state.
    // Then the honest owner lowers the fee.
    vm.prank(owner);
    cdo.setFeeParams(feeReceiver, F_new, feeSplit, managementFee);

    // Attacker is a KYC-passed lender and triggers accounting first.
    uint256 receipt = cdo.requestWithdraw(0, AATranche);

    // Expected result:
    // unclaimedFees excludes G * F_old / FULL_ALLOC and instead uses F_new.
    // The attacker's receipt is approximately G * (F_old - F_new) / FULL_ALLOC
    // larger than under a complete pre-change accounting checkpoint.
    assertGt(receipt, receiptUnderOldFee);
}
```

The deterministic assertion is that `unclaimedFees` after `requestWithdraw` equals the historical gain multiplied by `F_new`, not `F_old`, because `setFeeParams` left the pending gain unsplit. [2](#0-1) [1](#0-0)

### Citations

**File:** contracts/IdleCDOCreditVault.sol (L222-235)
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
    lastNAVAA = uint256(int256(_lastNAVAA) + _totalAAGain);
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

**File:** contracts/IdleCDOCreditVault.sol (L488-501)
```text
  function updateAccounting() external virtual {
    _checkOnlyOwnerOrGuardian();
    _forceUpdateAccounting();
  }

  /// @notice force accounting update without reverting on default path
  function _forceUpdateAccounting() internal {
    bool wasSkippingDefaultCheck = skipDefaultCheck;
    skipDefaultCheck = true;
    // Preserve an existing emergency shutdown and any wipe reported by accounting.
    if (!_updateAccounting()) {
      skipDefaultCheck = wasSkippingDefaultCheck;
    }
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L550-560)
```text
  /// @notice Checkpoint accrued management fees into `unclaimedFees`.
  /// @dev Raw underlyings are excluded because unsolicited transfers are skimmed instead of managed.
  function _accrueManagementFee() internal {
    unclaimedFees += _calculateManagementFee(_managedContractValue(), block.timestamp - latestHarvestBlock);
    latestHarvestBlock = block.timestamp;
  }

  /// @notice calculate annualized management fee for a balance over a duration
  function _calculateManagementFee(uint256 _nav, uint256 _duration) internal view returns (uint256) {
    // 3153600000000 == FULL_ALLOC * 365 days
    return _nav * managementFee * _duration / 3153600000000;
```

**File:** contracts/IdleCDOEpochVariant.sol (L739-778)
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

    // Programmable borrower deployments do not support instant withdrawals.
    // If apr decresed wrt last epoch, request instant withdraw and burn tranche tokens directly
    // we compare unscaled aprs
    if (_isInstantWithdrawEnabled()) {
      uint256 currentApr = creditVault.unscaledApr();
      if (lastEpochApr > (currentApr + instantWithdrawAprDelta)) {
        // burn strategy tokens from cdo and mint an equal amount to msg.sender as receipt
        creditVault.requestInstantWithdraw(_underlyings, msg.sender);
        // burn tranche tokens and decrease NAV
        _withdrawOps(_amount, _underlyings, _tranche);
        return _underlyings;
      }
    }

    uint256 principal = _underlyings;
    (uint256 interest, int256 diff) = _calcInterestWithdrawRequest(_underlyings, _tranche);
    uint256 totalFees = _totalWithdrawFees(principal, interest);
    // user is requesting principal + interest minus upfront management fee and net performance fee
    _underlyings = principal + interest - totalFees;
    // add expected fees to pending withdraw fees counter
    pendingWithdrawFees += totalFees;
```

**File:** contracts/IdleCDOEpochVariant.sol (L981-987)
```text
  /// @notice Check if wallet is allowed to interact with the contract
  /// @param _user User address
  /// @return true if wallet is allowed or keyring address is not set
  function isWalletAllowed(address _user) public view returns (bool) {
    address _keyring = keyring;
    return _keyring == address(0) || IKeyring(_keyring).checkCredential(keyringPolicyId, _user);
  }
```
