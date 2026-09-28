### Title
Withdrawal requests can revert when configured management-fee duration makes fees exceed principal - ([File: contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol))

### Summary
`requestWithdraw()` charges an upfront management fee over one full configured epoch plus any remaining buffer, but subtracts that fee from `principal + interest` without checking whether it exceeds the user's claim. Because `setEpochParams()` accepts an arbitrary nonzero duration and `setFeeParams()` allows a 2% annual management fee, an honest configuration change can make the withdrawal fee greater than a user's entire receipt. For an APR-0 vault, an epoch duration above 50 years is sufficient to make every withdrawal request revert with an arithmetic underflow.

### Finding Description
`requestWithdraw()` computes `principal`, projected `interest`, and `totalFees`, then performs `principal + interest - totalFees` directly. [1](#0-0)  `_totalWithdrawFees()` returns the raw management fee whenever it is at least the projected interest; it is not capped by the principal. [2](#0-1)  The charged duration is `epochDuration` plus the remaining buffer. [3](#0-2) 

`setEpochParams()` only rejects zero durations and does not impose an upper bound. [4](#0-3)  `setFeeParams()` allows `managementFee == MAX_FEE / 10`, which is 2% annually because `MAX_FEE` is 20%. [5](#0-4) [6](#0-5) 

With `epochDuration = 51 years`, `managementFee = 2%`, and zero APR, a receipt for `P` underlying charges approximately `1.02 * P` in upfront management fees. Since interest is zero, `P - 1.02 * P` underflows before `IdleCreditVault.requestWithdraw()` is reached. [7](#0-6) 

### Impact Explanation
All tranche holders are unable to create normal withdrawal receipts while this configuration remains active. The transaction reverts before tranche tokens are burned or strategy receipt tokens are minted, so the users' funds remain locked in the live vault rather than entering the withdrawal queue. [8](#0-7) 

For a 1,000-underlying position, the configured charge is about 1,020 underlying, producing a 20-underlying shortfall and reverting the request. This is a temporary freezing of user funds: the owner or manager can restore withdrawals by lowering `epochDuration`, lowering the management fee, or eventually closing the pool, but unprivileged users cannot bypass the revert.

### Likelihood Explanation
The issue requires an unusual but permitted administrative configuration: an epoch duration over roughly 50 years combined with the maximum 2% annual management fee, or proportionally lower durations at lower effective claim value. No privileged actor must be malicious; the failure follows from ordinary configuration ordering. The APR-0 path is the cleanest trigger because projected interest cannot offset the fee. The closest existing guard only checks that APR remains zero for an open APR0 receipt lifecycle; it does not prevent a fee from exceeding the receipt's principal. [9](#0-8) 

### Recommendation
Cap withdrawal charges at the user's payable amount before creating the receipt:

```solidity
uint256 grossClaim = principal + interest;
uint256 totalFees = _totalWithdrawFees(principal, interest);
if (totalFees > grossClaim) {
  totalFees = grossClaim;
}
_underlyings = grossClaim - totalFees;
```

Alternatively, bound `epochDuration + bufferPeriod` so `managementFee * duration / 365 days` cannot exceed `FULL_ALLOC`. Capping the fee is preferable because it preserves support for long-duration epochs and makes `requestWithdraw()` safe for every combination of APR, fees, and epoch parameters.

### Proof of Concept
Add this test to the existing `test/foundry/IdleCreditVault.t.sol` harness, which already exposes `idleCDO`, `cdoEpoch`, `strategy`, `underlying`, `owner`, `manager`, `borrower`, and `_depositWithUser`:

```solidity
function testRequestWithdrawRevertsWhenManagementFeeExceedsPrincipal() external {
  uint256 amount = 1_000 * ONE_SCALE;
  address user = makeAddr("fee-locked-user");

  // Keep the request in the APR0 path so interest cannot offset the fee.
  vm.prank(manager);
  IdleCreditVault(address(strategy)).setAprs(0, 0);

  // Maximum allowed management fee: 2% annually.
  _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, 2_000);

  // Permitted by setEpochParams: 2% * 51 years = 102% of principal.
  vm.prank(manager);
  cdoEpoch.setEpochParams(51 years, 0);

  _depositWithUser(user, amount, true);

  uint256 trancheBalance = IERC20(address(AAtranche)).balanceOf(user);
  uint256 principal = cdoEpoch.maxWithdrawable(user, address(AAtranche));

  // Sanity check: the upfront fee exceeds principal plus APR0 interest.
  uint256 fee = principal * 2_000 * 51 years / (FULL_ALLOC * 365 days);
  assertGt(fee, principal);

  vm.prank(user);
  vm.expectRevert(); // arithmetic underflow in principal + interest - totalFees
  cdoEpoch.requestWithdraw(trancheBalance, address(AAtranche));

  // The user retains the tranche position and cannot enter the withdrawal queue.
  assertEq(IERC20(address(AAtranche)).balanceOf(user), trancheBalance);
}
```

This reproduces the invariant break on a forked deployment: a configured charge calculated from current parameters exceeds the user's actual claim, and the unchecked subtraction reverts rather than reducing the receipt to zero or imposing a bounded charge.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L117-124)
```text
  function setEpochParams(uint256 _epochDuration, uint256 _bufferPeriod) public {
    _checkOnlyOwnerOrManager();
    // cannot set epoch params if epoch is running
    // cannot set epochDuration to 0 as it's reserved for closing the pool
    // and cannot set epochDuration if previously was set to 0 as borrower repaid all funds
    _checkNotAllowed(defaulted || isEpochRunning || _epochDuration == 0 || epochDuration == 0);
    epochDuration = _epochDuration;
    bufferPeriod = _bufferPeriod;
```

**File:** contracts/IdleCDOEpochVariant.sol (L739-790)
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

    /// if there is an AA withdrawal the overperformance that the amount withdrawed would have generated for BB tranches
    /// is saved in interestForOverUnderPerformance. This is used to calculate the interest that should be added to the
    /// expectedEpochInterest at the startEpoch.
    /// If there is a BB withdrawal this amount is subtracted from the expectedEpochInterest
    interestForOverUnderPerformance += diff;

    // The receipt is fixed now and leaves live NAV. Charge management fees upfront
    // for the time it waits outside live NAV: remaining buffer plus the next epoch.
    creditVault.requestWithdraw(_underlyings, msg.sender, principal);
    // burn tranche tokens and decrease NAV without interest for the next epoch as it was not yet counted in NAV
    _withdrawOps(_amount, principal, _tranche);
```

**File:** contracts/IdleCDOEpochVariant.sol (L896-905)
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

**File:** contracts/IdleCDOStorage.sol (L8-10)
```text
  uint256 public constant FULL_ALLOC = 100000;
  // max fee, relative to FULL_ALLOC
  uint256 internal constant MAX_FEE = 20000;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L497-508)
```text
    // Principal currently waiting for withdraw that was requested while APR was 0,
    // net of the upfront management fee charged at request time.
    uint256 _principal = apr0TotalPrincipal;

    // Fast path: no APR0 accounting needed.
    if (_principal == 0) {
      return (_expInterest, _adjPendingWithdrawFees);
    }
    // APR0 principal is only valid while APR is 0 for that request lifecycle.
    if (unscaledApr != 0) {
      revert NotAllowed();
    }
```
