### Title
Stale scaled APR after `setEpochParams` overcharges borrowers and transfers excess yield to depositors - (File: `contracts/IdleCDOEpochVariant.sol`)

### Summary

`setEpochParams` can change `epochDuration` while the strategy retains `lastApr`, which is stored already scaled by the previous `(duration + buffer) / duration` ratio. If the duration is changed during the buffer and the APR is not separately rescaled before `startEpoch`, the next epoch computes borrower interest from an inconsistent duration/APR pair. A KYC-passed depositor can enter after the parameter change and receive the resulting excess fixed APR paid by the honest borrower.

### Finding Description

`IdleCreditVault.lastApr` is explicitly documented and stored as the scaled APR, while `unscaledApr` stores the borrower APR before duration scaling [1](#0-0) [2](#0-1) . The coupled setter `setAprsWithBuffer` correctly derives `lastApr` as `unscaledApr * (duration + buffer) / duration` [3](#0-2) .

However, `IdleCDOEpochVariant.setEpochParams` updates `epochDuration` and `bufferPeriod` without recalculating the scaled APR stored in the strategy [4](#0-3) . The function is permitted while the vault is in its buffer phase because it only rejects defaults, running epochs, zero duration, and already-closed pools [5](#0-4) .

The stale scaled APR is then consumed together with the newly configured duration by `_calcInterestWithApr` [6](#0-5) . `startEpoch` uses that calculation to set `expectedEpochInterest` for the entire live NAV [7](#0-6) , and `stopEpoch` pulls that expected interest from the borrower [8](#0-7) [9](#0-8) .

For example, a 30-day epoch with a 5-day buffer and intended 10% APR stores approximately `11.667%` as `lastApr`. If `setEpochParams(60 days, 5 days)` is called without updating the APR, the correct scaled rate should be approximately `10.833%`, but the vault still uses `11.667%`. `_calcInterestWithApr` therefore computes roughly 19.17% annualized interest over 60 days instead of 10%, almost doubling the borrower's expected payment.

### Impact Explanation

An attacker who is an ordinary KYC-passing lender can deposit during the buffer after the duration change and before `startEpoch`. The attacker then receives a share of the inflated `expectedEpochInterest`, which is paid directly by the honest borrower at epoch stop.

The excess payment is approximately:

```text
NAV * staleScaledApr * newDuration / YEAR
-
NAV * intendedApr * (newDuration + buffer) / YEAR
```

For 1,000,000 tokens, an intended 10% APR, a 60-day duration, and a 5-day buffer, the expected payment should be about 17,808 tokens. With a stale 11.667% scaled APR, the contract requests about 19,178 tokens, creating approximately 1,370 tokens of excess yield. This is direct value extraction from the borrower rather than merely an accounting inconsistency.

### Likelihood Explanation

The issue requires an honest owner or manager to change the epoch duration during the buffer and start the next epoch without separately rescaling the APR. Both `setEpochParams` and orchestrator forwarding expose duration changes as an independent operation, so the code permits the inconsistent state without requiring a malicious privileged role [4](#0-3) [10](#0-9) .

The attacker does not control the privileged calls, but can monitor the mempool or chain for the duration update and deposit before the next epoch starts. Existing access controls only ensure that the depositor is permitted; they do not ensure that the duration and scaled APR remain consistent.

### Recommendation

Keep `epochDuration`, `bufferPeriod`, `unscaledApr`, and scaled `lastApr` synchronized in a single state transition.

In `setEpochParams`, after validating and storing the new parameters, recalculate the strategy APR from `IdleCreditVault(strategy).unscaledApr()` using the new duration and buffer. Alternatively, require all duration changes to pass through a function that accepts the intended unscaled APR and atomically calls `setAprsWithBuffer`.

The second option is safer operationally:

```solidity
function setEpochParamsAndApr(
  uint256 _epochDuration,
  uint256 _bufferPeriod,
  uint256 _unscaledApr
) external {
  _checkOnlyOwnerOrManager();
  _checkNotAllowed(defaulted || isEpochRunning || _epochDuration == 0 || epochDuration == 0);

  epochDuration = _epochDuration;
  bufferPeriod = _bufferPeriod;
  IdleCreditVault(strategy).setAprsWithBuffer(_unscaledApr, _epochDuration, _bufferPeriod);
}
```

### Proof of Concept

A Foundry fork test can reproduce the mismatch with the existing credit-vault fixtures:

```solidity
function testStaleScaledAprAfterEpochDurationChange() public {
    uint256 nav = 1_000_000 * ONE_SCALE;
    uint256 intendedApr = 10e18;

    // Initial epoch: duration 30 days, buffer 5 days.
    // The vault therefore stores lastApr = 10e18 * 35 / 30.
    depositAA(nav);
    startEpoch();
    stopEpochWithDuration(intendedApr, 0, 30 days, 0);

    uint256 staleScaledApr = strategy.getApr();
    assertEq(staleScaledApr, intendedApr * 35 days / 30 days);

    // Honest manager/owner changes only the duration during buffer.
    vm.prank(manager);
    cdo.setEpochParams(60 days, 5 days);

    // A permitted attacker deposits after the configuration change.
    address attacker = makeKycUser("attacker");
    uint256 attackerDeposit = 100_000 * ONE_SCALE;
    depositAAAs(attacker, attackerDeposit);

    uint256 navAtStart = cdo.getContractValue();
    cdo.startEpoch();

    uint256 staleInterest =
        navAtStart * (staleScaledApr / 100) * 60 days /
        (365 days * ONE_TRANCHE_TOKEN);

    uint256 correctInterest =
        navAtStart * (intendedApr / 100) * 65 days /
        (365 days * ONE_TRANCHE_TOKEN);

    assertGt(staleInterest, correctInterest);
    assertEq(cdo.expectedEpochInterest(), staleInterest);
}
```

At epoch end, fund the borrower with `staleInterest`, call `stopEpoch`, then claim the attacker's tranche balance or withdrawal proceeds. The attacker receives part of `staleInterest - correctInterest` even though the intended borrower APR and new epoch parameters never implied that payment.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L50-51)
```text
  /// @notice latest saved apr, already scaled to include the buffer period
  uint256 public lastApr;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L72-73)
```text
  /// @notice unscaled apr
  uint256 public unscaledApr;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L217-219)
```text
  function setAprsWithBuffer(uint256 _unscaledApr, uint256 _duration, uint256 _buffer) external {
    unscaledApr = _unscaledApr;
    setApr(_duration == 0 ? _unscaledApr : _unscaledApr * (_duration + _buffer) / _duration);
```

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

**File:** contracts/IdleCDOEpochVariant.sol (L254-262)
```text
    // calculate expected interest 
    // NOTE: all withdrawal requests, burn tranche tokens and decrease getContractValue,
    // this can be done only prior to the start of the epoch so getContractValue() is the total amount net
    // of all withdrawal requests. We add the fee that we should get for normal pending withdraws
    // we also add/remove the over/under performance caused by withdraw requests.
    // Pending fees remain due even if fixed-APR requests exhaust active interest.
    int256 adjustedActiveInterest = int256(_calcInterest(getContractValue())) + interestForOverUnderPerformance;
    if (adjustedActiveInterest < 0) adjustedActiveInterest = 0;
    expectedEpochInterest = pendingWithdrawFees + uint256(adjustedActiveInterest);
```

**File:** contracts/IdleCDOEpochVariant.sol (L373-379)
```text
    uint256 _grossInterest = _isRequestingAllFunds ? _expectedInterest - _totBorrowed : _expectedInterest;
    bool _mintInterest = isInterestMinted && !_isRequestingAllFunds;
    // In minted mode IdleCDO only needs cash for withdraw requests, not for epoch interest itself.
    uint256 _amountToPullFromBorrower = _mintInterest ? 0 : _expectedInterest;
    if (_mintInterest && _interest > 1) {
      uint256 _maxApr = _strategy.maxApr();
      _checkNotAllowed(_maxApr != 0 && _grossInterest > _calcInterestWithApr(getContractValue(), _maxApr) + _pendingWithdrawFees);
```

**File:** contracts/IdleCDOEpochVariant.sol (L408-410)
```text
    try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
      // transfer in strategy and decrease pendingWithdraws
        _strategy.collectWithdrawFunds(_pendingWithdraws);
```

**File:** contracts/IdleCDOEpochVariant.sol (L800-808)
```text
  function _calcInterest(uint256 _amount) internal view returns (uint256) {
    return _calcInterestWithApr(_amount, _getStrategyApr());
  }

  /// @notice Calculate the interest of an epoch for the given amount and apr
  /// @param _amount Amount of underlyings
  /// @param _apr Apr used for the calculation
  function _calcInterestWithApr(uint256 _amount, uint256 _apr) internal view returns (uint256) {
    return _amount * (_apr / 100) * epochDuration / (365 days * ONE_TRANCHE_TOKEN);
```

**File:** contracts/IdleCreditVaultManagerOrchestrator.sol (L108-115)
```text
  /// @notice Forward `setEpochParams` to one registered CDO.
  /// @param _cdo credit vault address
  /// @param _epochDuration new epoch duration
  /// @param _bufferPeriod new buffer period between epochs
  function setEpochParams(address _cdo, uint256 _epochDuration, uint256 _bufferPeriod) external {
    _checkOnlyOperator();
    _creditVault(_cdo).setEpochParams(_epochDuration, _bufferPeriod);
  }
```
