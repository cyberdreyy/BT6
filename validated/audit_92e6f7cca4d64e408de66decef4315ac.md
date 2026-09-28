### Title
Withdrawal receipt fee locks in a mismatched `managementFee`/`epochDuration` pair because the rate and duration are set in separate transactions by different roles - ([File: contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol))

### Summary
`requestWithdraw` crystallizes an immutable payout receipt whose upfront management fee is computed as `principal * managementFee * (epochDuration + remainingBuffer)` using the *current* values of two independently settable parameters. `managementFee` is set by `owner()` via `setFeeParams`, while `epochDuration`/`bufferPeriod` are set by owner-or-manager via `setEpochParams`. Because these are two different setters (and commonly two different accounts), any coordinated reconfiguration is inherently non-atomic. A user who calls `requestWithdraw` between the two transactions locks a receipt priced with an unintended fee×duration product, permanently undercharging the protocol (or overcharging themselves), exactly mirroring the `tradeFee`/`acceptedCurrency` inconsistency.

### Finding Description
- `requestWithdraw` computes `totalFees = _totalWithdrawFees(principal, interest)` and adds it to `pendingWithdrawFees`, then fixes the receipt via `creditVault.requestWithdraw(_underlyings, msg.sender, principal)` — the fee is never recomputed at claim time. [1](#0-0) 
- `_totalWithdrawFees` calls `_calculateManagementFee(_principal, _withdrawRequestManagementFeeDuration())`, and `_withdrawRequestManagementFeeDuration` returns `epochDuration + (epochEndDate + bufferPeriod - block.timestamp)` while a buffer is live. [2](#0-1) [3](#0-2) 
- The duration component is updated by `setEpochParams(_epochDuration, _bufferPeriod)`, callable by owner *or* manager whenever the pool is not running/defaulted (i.e., precisely during the buffer window when `requestWithdraw` is open). [4](#0-3) 
- The rate component is updated by `setFeeParams`, which is `onlyOwner` and updates `fee`, `feeSplit`, `feeReceiver`, and `managementFee` atomically — but cannot touch `epochDuration`. [5](#0-4) 
- During the buffer phase (`epochEndDate != 0`, epoch stopped, requests allowed), `requestWithdraw` is permissionless for any KYC'd wallet, so an unprivileged attacker can freely choose which intermediate parameter pair to lock in. [6](#0-5) 

### Impact Explanation
Broken invariant: one receipt should carry the fee intended for the epoch it waits through. Because the receipt is fixed at request time, a request landed in the gap between `setFeeParams` and `setEpochParams` permanently escapes the intended fee. Example: pool runs `managementFee = 3%`, `epochDuration = 30d` (product ≈ 0.9%-equivalent). Governance decides to move to `managementFee = 1%`, `epochDuration = 90d` (same product). If `setFeeParams` lands first while duration is still 30d, an attacker requesting during the buffer pays `1% × 30d` instead of `1% × 90d` — a 3× undercharge on the upfront fee; the missing amount never reaches `pendingWithdrawFees` and is unrecoverable. Conversely if `setEpochParams` lands first, users pay 3× the intended fee (direct user loss). Loss scales linearly with the requested principal.

### Likelihood Explanation
- Both parameters are legitimately re-tunable during every buffer window, and `setEpochParams` is delegated to the manager while `setFeeParams` is owner-only, so the two updates cannot be bundled in one transaction even when honestly coordinated — a gap exists on every fee/duration reconfiguration.
- The attacker only needs to be a KYC-passing lender holding tranche tokens; no privileged action, no malicious role, and no oracle manipulation is required. Mempool observation suffices to sandwich the admin sequence.
- Likelihood is bounded by how often fee/duration reconfigurations occur and by the fact that `epochDuration` cannot be changed while `isEpochRunning` or `defaulted`, narrowing the window to buffer/idle phases — where `requestWithdraw` is exactly the open path.

### Recommendation
Require that `managementFee` and the fee-relevant epoch duration be updated atomically: either fold `epochDuration`/`bufferPeriod` into a single owner-level setter with `setFeeParams`, or snapshot the (fee, duration) pair used for receipts at `stopEpoch`/`startEpoch` so `requestWithdraw` always uses the coherent pair of the epoch the receipt will wait through. Alternatively, gate `requestWithdraw` while a parameter-change is pending, or recompute the upfront management fee at `startEpoch` when the new duration is known.

### Proof of Concept
Foundry fork test (adapted from `testRequestWithdrawChargesManagementFeeUpfront` in `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testRequestWithdrawSandwichedBetweenFeeAndDurationChange() external {
    uint256 amount = 10_000 * ONE_SCALE;

    // Setup: 3% mgmt fee, 30d epoch (default config)
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee()); // perf fee 0
    _setManagementFee(3_000); // 3%

    uint256 trancheAmount = idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch()); // buffer opens

    // Reconfiguration begins: owner lowers mgmt fee to 1% for the new 90d epoch
    _setManagementFee(1_000); // tx1 lands; setEpochParams(90 days, buffer) still pending

    uint256 principal = trancheAmount * cdoEpoch.tranchePrice(address(AAtranche)) / ONE_TRANCHE_TOKEN;
    uint256 chargedFee = _calcManagementFee(principal, 1_000, _withdrawRequestManagementFeeDuration());
    // Attacker locks receipt under (1%, still-30d duration)
    uint256 requested = cdoEpoch.requestWithdraw(trancheAmount, address(AAtranche));
    assertEq(requested, principal + /*interest*/ 0 - chargedFee);
    assertEq(cdoEpoch.pendingWithdrawFees(), chargedFee);

    // Manager completes the reconfiguration: 90d epoch
    vm.prank(manager);
    cdoEpoch.setEpochParams(90 days, cdoEpoch.bufferPeriod());

    // Intended fee was 1% * (90d + buffer); receipt already fixed with ~30d duration
    uint256 intendedFee = _calcManagementFee(principal, 1_000, 90 days + cdoEpoch.bufferPeriod());
    assertLt(chargedFee, intendedFee); // ~3x undercharge, permanently lost from pendingWithdrawFees
}
```

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L121-125)
```text
    // and cannot set epochDuration if previously was set to 0 as borrower repaid all funds
    _checkNotAllowed(defaulted || isEpochRunning || _epochDuration == 0 || epochDuration == 0);
    epochDuration = _epochDuration;
    bufferPeriod = _bufferPeriod;
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L739-744)
```text
  function requestWithdraw(uint256 _amount, address _tranche) external returns (uint256 _underlyings) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == AATranche ? !allowAAWithdrawRequest : !allowBBWithdrawRequest) ||
      !isWalletAllowed(msg.sender)
    );
```

**File:** contracts/IdleCDOEpochVariant.sol (L772-790)
```text
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

**File:** contracts/IdleCDOEpochVariant.sol (L925-930)
```text
  function _withdrawRequestManagementFeeDuration() private view returns (uint256 _duration) {
    uint256 bufferEnd = epochEndDate + bufferPeriod;
    _duration = epochDuration;
    if (block.timestamp < bufferEnd) {
      _duration += bufferEnd - block.timestamp;
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
