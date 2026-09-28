### Title
APR0 withdrawal interest is distributed before the epoch management-fee accrual, overpaying requesters - ([File: `contracts/strategies/idle/IdleCreditVault.sol`])

### Summary
`prepareStopEpochWithApr0` allocates realized epoch interest to APR0 withdrawal requesters before `IdleCDOEpochVariant._stopEpoch` checkpoints elapsed management fees. The APR0 split therefore uses gross epoch interest and a pre-accrual NAV snapshot, while active tranche holders absorb the subsequently accrued management fee. An unprivileged KYC-passed lender can enter the APR0 withdrawal bucket and receive yield that should have remained with active tranche holders.

### Finding Description
During `stopEpoch`, the strategy first computes the APR0 bucket's pro-rata interest in `prepareStopEpochWithApr0`. It calculates `_apr0InterestGross` from `_interestNetOfFees * _principal / (_tvl + _principal)`, adds the net amount to `pendingWithdraws`, and removes it from the interest left for active pool accounting. The management-fee checkpoint happens only after this call in `IdleCDOEpochVariant._stopEpoch`.

Relevant ordering:

```solidity
(_expectedInterest, _pendingWithdrawFees) =
  _strategy.prepareStopEpochWithApr0(_interest);
// ...
_accrueManagementFee();
```

The APR0 allocation uses the state before `_accrueManagementFee()` adds the elapsed-period fee to `unclaimedFees`. `_accrueManagementFee` explicitly applies `managementFee` to `_managedContractValue()` for the elapsed period. Thus, the requester's yield share is computed as though that management fee did not reduce the epoch yield/NAV, even though it is charged immediately afterward.

### Impact Explanation
The broken invariant is fair yield distribution.

Let:

- `P` = attacker's APR0 withdrawal principal
- `T` = active NAV before the uncheckpointed management fee
- `I` = epoch interest net of withdrawal-request fees
- `M` = elapsed management fee accrued at stop

The APR0 bucket should receive approximately:

```text
(I - M) * P / (T + P)
```

Instead it receives approximately:

```text
I * P / (T + P)
```

The excess is approximately:

```text
M * P / (T + P)
```

That excess is paid from the same borrower-funded epoch interest, reducing the amount available to active AA/BB holders. If the attacker supplies half of the relevant principal, the theft is approximately half of the epoch's accrued management fee. Repeated APR0 withdrawal/request cycles compound the loss.

### Likelihood Explanation
The attacker only needs to be an allowed lender capable of creating an APR0 withdrawal request. The remaining call sequence uses honest manager behavior: wait for `epochEndDate`, then call `stopEpoch` with realized override interest. No malicious owner, manager, borrower, oracle manipulation, or reentrancy is required.

The issue is specific to APR0 settlement because `prepareStopEpochWithApr0` performs a separate pro-rata allocation before the CDO's fee checkpoint. Ordinary fixed-APR accounting reaches `_updateAccounting()` after `_accrueManagementFee()`.

### Recommendation
Move the elapsed-period management-fee checkpoint before calling `prepareStopEpochWithApr0`, or pass a post-management-fee interest/NAV basis into the APR0 calculation.

A safer ordering is:

```solidity
_accrueManagementFee();
(_expectedInterest, _pendingWithdrawFees) =
  _strategy.prepareStopEpochWithApr0(_interest);
```

Care must be taken not to change the intended treatment of separately tracked `pendingWithdrawFees`. Add an invariant test showing that, for active principal `T`, APR0 principal `P`, epoch interest `I`, and elapsed management fee `M`, APR0 interest equals the expected share of the post-management-fee yield.

### Proof of Concept
Foundry fork/test sequence:

```solidity
function testApr0InterestUsesStaleManagementFeeAccounting() external {
  // Setup:
  // - managementFee > 0
  // - unscaledApr == 0
  // - attacker is KYC-passed
  // - one passive lender remains active

  uint256 attackerDeposit = 1_000_000e6;
  uint256 passiveDeposit  = 1_000_000e6;

  depositAA(attacker, attackerDeposit);
  depositAA(passiveLP, passiveDeposit);

  vm.prank(manager);
  cdo.startEpoch();

  // Honest stop/start leaves the next APR at zero.
  fundBorrower(cdo.expectedEpochInterest());
  vm.warp(cdo.epochEndDate() + 1);
  vm.prank(manager);
  cdo.stopEpoch(0, 0);

  // Attacker moves into the APR0 receipt bucket.
  vm.prank(attacker);
  uint256 apr0Principal =
    cdo.requestWithdraw(trancheAA.balanceOf(attacker), address(trancheAA));

  uint256 activePrincipal = cdo.getContractValue();
  uint256 apr0Total = IdleCreditVault(strategy).apr0TotalPrincipal();
  assertEq(apr0Total, apr0Principal);

  vm.prank(manager);
  cdo.startEpoch();

  // Allow management fees to accrue for a material period.
  uint256 feeCheckpointPre = cdo.unclaimedFees();
  vm.warp(cdo.epochEndDate() + 1);

  uint256 epochInterest = 10_000e6;
  fundBorrower(epochInterest + IdleCreditVault(strategy).pendingWithdraws());

  vm.prank(manager);
  cdo.stopEpoch(0, epochInterest);

  uint256 attackerReceived = claimAndMeasure(attacker);

  // Calculate the fee accrued during the epoch.
  uint256 accruedManagementFee =
    cdo.unclaimedFees() + paidManagementFeeDelta() - feeCheckpointPre;

  uint256 expectedApr0Interest =
    (epochInterest - accruedManagementFee) *
    apr0Principal /
    (activePrincipal + apr0Principal);

  uint256 actualApr0Interest = attackerReceived - apr0Principal;

  assertGt(
    actualApr0Interest,
    expectedApr0Interest,
    "APR0 requester was paid interest on pre-management-fee yield"
  );

  assertApproxEqAbs(
    actualApr0Interest - expectedApr0Interest,
    accruedManagementFee * apr0Principal /
      (activePrincipal + apr0Principal),
    2,
    "theft should equal APR0 share of the late-accrued management fee"
  );
}
```

The vulnerable ordering is in `IdleCDOEpochVariant._stopEpoch`, where APR0 settlement occurs before the fee checkpoint, and in `IdleCreditVault.prepareStopEpochWithApr0`, where the pro-rata APR0 share is calculated from that stale basis. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L361-384)
```text
    // Strategy finalizes APR0 bucket state and returns adjusted stopEpoch values.
    (_expectedInterest, _pendingWithdrawFees) = _strategy.prepareStopEpochWithApr0(_interest);
    // Pending withdraws may be increased during APR0 settlement, so read after prepareStopEpochWithApr0.
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();

    // special case where we get everything back from the borrower
    if (_isRequestingAllFunds) {
      // Recall gross strategy-token principal, including fee backing excluded from net CDO NAV.
      // Prefunded variants also add queue deposits already sent directly to the borrower.
      _totBorrowed += _contractTokenBalance(strategyToken);
      _expectedInterest += _totBorrowed;
    }
    uint256 _grossInterest = _isRequestingAllFunds ? _expectedInterest - _totBorrowed : _expectedInterest;
    bool _mintInterest = isInterestMinted && !_isRequestingAllFunds;
    // In minted mode IdleCDO only needs cash for withdraw requests, not for epoch interest itself.
    uint256 _amountToPullFromBorrower = _mintInterest ? 0 : _expectedInterest;
    if (_mintInterest && _interest > 1) {
      uint256 _maxApr = _strategy.maxApr();
      _checkNotAllowed(_maxApr != 0 && _grossInterest > _calcInterestWithApr(getContractValue(), _maxApr) + _pendingWithdrawFees);
    }

    // Checkpoint management fees before borrower funds are pulled in so the elapsed-period
    // accrual applies only to the pre-stop live NAV, not to newly received epoch interest.
    _accrueManagementFee();
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L490-540)
```text
  function prepareStopEpochWithApr0(uint256 _interest) external returns (uint256 _expInterest, uint256 _adjPendingWithdrawFees) {
    _onlyIdleCDO();
    IIdleCDOEpochVariant _cdo = IIdleCDOEpochVariant(idleCDO);
    uint256 _pendingFees = _cdo.pendingWithdrawFees();
    uint256 _tvl = _cdo.getContractValue();
    _expInterest = _interest > 1 ? _interest : _cdo.expectedEpochInterest();
    _adjPendingWithdrawFees = _pendingFees;
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

    uint256 _apr0NetInterest;
    // APR0 allocation is computed only when stopEpoch receives a real override interest.
    // _expectedInterest == 1 is the "request all funds back" sentinel and is handled in IdleCDO.
    if (_expInterest > 1 && _expInterest > _pendingFees) {
      // Remove already booked withdraw fees from the interest base before splitting.
      uint256 _interestNetOfFees = _expInterest - _pendingFees;
      // Total principal used for the pro-rata split:
      // IdleCDO TVL (which excludes APR0 requested principal) + APR0 principal bucket.
      uint256 _totalPrincipalForSplit = _tvl + _principal;
      if (_totalPrincipalForSplit != 0) {
        // APR0 users get a pro-rata share of realized interest.
        uint256 _apr0InterestGross = _interestNetOfFees * _principal / _totalPrincipalForSplit;
        if (_apr0InterestGross != 0) {
          // Same fee model as normal withdraw interest.
          uint256 _apr0Fee = _apr0InterestGross * _cdo.fee() / FULL_ALLOC;
          _apr0NetInterest = _apr0InterestGross - _apr0Fee;
          _adjPendingWithdrawFees += _apr0Fee;
          _expInterest -= _apr0NetInterest;
        }
      }
    }

    // Finalize one-epoch APR0 interest for current epoch only.
    if (_apr0NetInterest != 0) {
      // Funds owed to withdraw requesters increase by APR0 net interest.
      pendingWithdraws += _apr0NetInterest;
      // Save per-epoch net rate; each APR0 request accrues exactly once on its request epoch.
      apr0RateByEpoch[epochNumber] = (_apr0NetInterest * 1e18) / _principal;
    }
    // Close current APR0 bucket so it cannot accrue again on later stopEpoch calls.
    apr0TotalPrincipal = 0;
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
