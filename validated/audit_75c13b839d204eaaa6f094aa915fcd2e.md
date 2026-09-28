### Title
Instant withdrawals skip the management fee applied to delayed withdrawal receipts - ([File: contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol))

### Summary
`requestWithdraw` applies upfront management and performance fees to ordinary withdrawal receipts, but the instant-withdrawal branch returns the full tranche value and never books an equivalent fee. When an honest manager lowers the next epoch’s APR by more than `instantWithdrawAprDelta`, a KYC-passed tranche holder can select the instant path and avoid the management fee for the period during which its receipt remains outside live NAV awaiting borrower funding. This mirrors the inconsistent classification issue: two economically similar withdrawal paths use different fee criteria.

### Finding Description
`requestWithdraw` first converts tranche tokens to their current underlying value, then checks whether instant withdrawals are enabled and whether `lastEpochApr > currentApr + instantWithdrawAprDelta`. If the condition is true, it calls `requestInstantWithdraw(_underlyings, msg.sender)` and immediately returns the unmodified principal value. [1](#0-0) 

For the ordinary path, the same initial `_underlyings` value is treated as `principal`, projected epoch interest is calculated, and `_totalWithdrawFees(principal, interest)` is deducted before creating the receipt. [2](#0-1)  `_totalWithdrawFees` always includes an annualized management fee over `_withdrawRequestManagementFeeDuration()`, and also charges performance fees when projected interest exceeds that management fee. [3](#0-2) 

The fee duration includes the next epoch plus the remaining buffer because ordinary receipts leave live NAV at request time but are claimable only after the following epoch. [4](#0-3)  Instant receipts also leave live NAV immediately: the strategy burns `_principal` or `_underlyings` strategy tokens from the CDO and mints receipt tokens to the user. [5](#0-4)  However, the instant branch performs no equivalent haircut or `pendingWithdrawFees` accounting before returning. [6](#0-5) 

### Impact Explanation
A withdrawing user receives `principal * managementFee * instantWithdrawDelay / 365 days` more than under a consistent management-fee policy for time spent outside live NAV. With a 10,000,000-token withdrawal, a 10% management fee, and a three-day instant-withdraw delay, the skipped fee is approximately `8,219.18` underlying units, assuming six-decimal units are expressed in whole-token terms. The loss accrues to `feeReceiver` and `owner()` according to `feeSplit`, while the withdrawer receives the corresponding excess receipt amount. [7](#0-6) 

If the intended fee model is that every withdrawal leaving active NAV pays for its settlement delay, this is direct loss of protocol fee revenue rather than a view-only discrepancy. The ordinary path explicitly documents that upfront management fees compensate for the receipt’s time outside live NAV. [8](#0-7) 

### Likelihood Explanation
The trigger does not require a malicious privileged role. The manager only needs to make a normal APR reduction exceeding `instantWithdrawAprDelta`; after that, any wallet passing `isWalletAllowed` can call `requestWithdraw` and automatically enters the cheaper instant branch. [9](#0-8)  The attacker does not need to manipulate oracle data, force a default, freeze funds, or control the borrower.

The opportunity is conditional on instant withdrawals being enabled and an APR reduction occurring, but those are normal supported operating modes. Existing guards do not prevent it because `_isInstantWithdrawEnabled()` deliberately selects this branch, and `requestInstantWithdraw` mints the full receipt amount. [10](#0-9) 

### Recommendation
Apply the same fee policy to instant receipts before calling `requestInstantWithdraw`. At minimum, charge `_calculateManagementFee(_underlyings, instantWithdrawDelay)` and add that amount to `pendingWithdrawFees`, or share a common fee-calculation helper so ordinary and instant withdrawals cannot diverge.

If instant withdrawals are intentionally exempt from settlement-delay management fees, document that exemption explicitly and ensure `maxWithdrawable` exposes the fee-free instant result when the APR condition is met. Add regression coverage comparing ordinary and instant withdrawal fees after an APR decrease.

### Proof of Concept
Add this test beside the existing credit-vault tests in `test/foundry/IdleCreditVault.t.sol`. It uses the file’s existing deployment variables and helpers.

```solidity
function testInstantWithdrawSkipsManagementFee() external {
    uint256 amount = 10_000 * ONE_SCALE;
    uint256 mgmtFeeRate = 10_000; // 10% annualized

    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, mgmtFeeRate);

    idleCDO.depositAA(amount);
    idleCDO.depositBB(amount);
    _transferBurnedTrancheTokens(address(this), true);
    _transferBurnedTrancheTokens(address(this), false);

    _startEpochAndCheckPrices(0);

    // Honest manager action: lower the next epoch APR enough to enable instant withdrawals.
    _stopEpochAndCheckPrices(
        0,
        initialProvidedApr - (cdoEpoch.instantWithdrawAprDelta() + 1),
        _expectedFundsEndEpoch()
    );

    uint256 trancheBalance = IERC20Detailed(address(AAtranche)).balanceOf(address(this));
    uint256 principal = trancheBalance * cdoEpoch.tranchePrice(address(AAtranche)) / ONE_TRANCHE_TOKEN;

    uint256 expectedFee =
        principal * mgmtFeeRate * cdoEpoch.instantWithdrawDelay() / FULL_ALLOC / 365 days;

    uint256 requested = cdoEpoch.requestWithdraw(trancheBalance, address(AAtranche));

    // Current behavior: the instant receipt is the entire principal.
    assertEq(requested, principal, "instant branch returns unhaircut principal");
    assertEq(cdoEpoch.pendingWithdrawFees(), 0, "instant branch books no withdrawal fee");

    // The difference from the ordinary fee policy is the lost management fee.
    assertGt(expectedFee, 0, "test requires a nonzero skipped fee");
    assertEq(principal - expectedFee, requested - expectedFee, "consistent haircut would be lower");
}
```

The relevant production divergence is that the instant branch returns at line 768 before `_totalWithdrawFees` is reached, while ordinary requests deduct `totalFees` and book it in `pendingWithdrawFees`. [11](#0-10)

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L739-744)
```text
  function requestWithdraw(uint256 _amount, address _tranche) external returns (uint256 _underlyings) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == AATranche ? !allowAAWithdrawRequest : !allowBBWithdrawRequest) ||
      !isWalletAllowed(msg.sender)
    );
```

**File:** contracts/IdleCDOEpochVariant.sol (L752-779)
```text
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

**File:** contracts/IdleCDOEpochVariant.sol (L786-790)
```text
    // The receipt is fixed now and leaves live NAV. Charge management fees upfront
    // for the time it waits outside live NAV: remaining buffer plus the next epoch.
    creditVault.requestWithdraw(_underlyings, msg.sender, principal);
    // burn tranche tokens and decrease NAV without interest for the next epoch as it was not yet counted in NAV
    _withdrawOps(_amount, principal, _tranche);
```

**File:** contracts/IdleCDOEpochVariant.sol (L837-840)
```text
  /// @notice Return whether new requests may use instant-withdraw mode.
  /// @dev Child variants can permanently disable instant mode independently of legacy storage.
  function _isInstantWithdrawEnabled() internal view virtual returns (bool) {
    return !disableInstantWithdraw && !isProgrammableBorrower;
```

**File:** contracts/IdleCDOEpochVariant.sol (L896-906)
```text
  /// @notice Calculate total fees charged on a withdrawal request.
  /// @param _principal Principal leaving live NAV.
  /// @param _interest Projected interest for the requested principal.
  /// @return Total management and performance fees to subtract from principal plus projected interest.
  function _totalWithdrawFees(uint256 _principal, uint256 _interest) private view returns (uint256) {
    uint256 _mgmtFee = _calculateManagementFee(_principal, _withdrawRequestManagementFeeDuration());
    // When interest covers management fees, interest - netGain equals management fee plus performance fee.
    return _mgmtFee >= _interest ?
      _mgmtFee :
      _interest - _netGainAfterFees(_interest, _mgmtFee);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L920-930)
```text
  /// @notice Get the duration used for upfront management fees on withdrawal receipts.
  /// @dev Receipts leave live NAV at request time but can be claimed only after the
  /// next epoch settles. Requests made during the buffer also pay for the remaining
  /// buffer time before that next epoch can start.
  /// @return _duration One epoch plus any remaining buffer before the next epoch.
  function _withdrawRequestManagementFeeDuration() private view returns (uint256 _duration) {
    uint256 bufferEnd = epochEndDate + bufferPeriod;
    _duration = epochDuration;
    if (block.timestamp < bufferEnd) {
      _duration += bufferEnd - block.timestamp;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-374)
```text
  function requestInstantWithdraw(uint256 _amount, address _user) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    // burn strategy tokens from cdo
    _burn(msg.sender, _amount);
  
    // mint equal amount of strategy tokens to the user as receipt, useful in case of default
    _mint(_user, _amount);

    // increase the instant withdraw requests for the user
    instantWithdrawsRequests[_user] += _amount;
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
```

**File:** contracts/IdleCDOCreditVault.sol (L585-598)
```text
  /// @notice transfer fee to feeReceiver and owner according to feeSplit
  /// @param _amount total fee amount to split and transfer (in underlyings)
  function _transferFeeUnderlyings(uint256 _amount) internal {
    uint256 feeReceiverAmount = _feeReceiverAmount(_amount);
    _transferUnderlyings(feeReceiver, feeReceiverAmount);
    _transferUnderlyings(owner(), _amount - feeReceiverAmount);
  }

  /// @notice calculates the amount to transfer to feeReceiver based on the feeSplit
  /// @dev `setFeeParams` guarantees a nonzero receiver; a zero split naturally returns zero.
  /// @param _amount total fee amount to split
  function _feeReceiverAmount(uint256 _amount) internal view returns (uint256) {
    return _amount * feeSplit / FULL_ALLOC;
  }
```
