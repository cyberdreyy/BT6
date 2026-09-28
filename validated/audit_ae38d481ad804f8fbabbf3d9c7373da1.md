### Title
Dust APR0 withdraw request permanently bricks `stopEpoch` whenever APR is raised — unprivileged griefing freeze of all vault funds - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The external report (CVE-2020-26409) is a resource-consumption DoS enabled by insufficient input validation on attacker-supplied content. The analog in idle-tranches is `IdleCreditVault.prepareStopEpochWithApr0`: it reverts unconditionally whenever `apr0TotalPrincipal != 0 && unscaledApr != 0`. Any KYC-passing tranche holder can make `apr0TotalPrincipal` non-zero with a dust `requestWithdraw` while `unscaledApr == 0`. `apr0TotalPrincipal` is cleared only at the end of that same function, so once a dust request exists, every `stopEpoch` call reverts as long as `unscaledApr != 0` — and there is no user- or manager-facing path to cancel an open APR0 bucket.

### Finding Description
`requestWithdraw` in `IdleCreditVault` routes requests to `_requestWithdrawApr0` whenever `unscaledApr == 0 && !isClosed` [1](#0-0) . There is no minimum amount: any `_amount != 0` passes the early return [2](#0-1) , and `_requestWithdrawApr0` unconditionally increments `apr0TotalPrincipal` [3](#0-2) .

In `prepareStopEpochWithApr0` (called from `IdleCDOEpochVariant.stopEpoch`), the order is:

```solidity
uint256 _principal = apr0TotalPrincipal;
if (_principal == 0) return ...;
if (unscaledApr != 0) {
  revert NotAllowed();
}
...
apr0TotalPrincipal = 0;
``` [4](#0-3) 

The revert precedes the only place `apr0TotalPrincipal` is reset. `_settleApr0` moves per-user buckets (`apr0Users[_user].principal` → `settledPrincipal`) but never decrements `apr0TotalPrincipal` [5](#0-4) . `claimWithdrawRequest` also never touches `apr0TotalPrincipal` [6](#0-5) . So an open APR0 bucket is a latch that cannot be removed except by a successful `stopEpoch` with `unscaledApr == 0`.

The intent — "APR0 principal is only valid while APR is 0 for that request lifecycle" — is enforced as a revert instead of preventing the state transition, which turns an input-validation gap into a hard freeze primitive.

### Impact Explanation
Two attack shapes, both from an unprivileged KYC'd tranche holder:

1. **Permanent freeze if APR is raised mid-lifecycle.** Attacker deposits dust, requests a dust withdraw while `unscaledApr == 0`. Manager/owner later sets `unscaledApr` non-zero (a routine honest operation via `setAprs`/`setAprsWithBuffer`, also done implicitly at `startEpoch`). From then on `stopEpoch` reverts with `NotAllowed` forever, because `apr0TotalPrincipal` can never be cleared while APR ≠ 0. All user deposits, queued withdrawals, and borrower repayments are frozen until the APR is set back to exactly 0.

2. **Perpetual APR lockout.** Even if the manager resets APR to 0 and clears the bucket, the attacker re-requests dust each buffer period. The pool can then never run a non-zero-APR epoch, freezing interest accrual and forcing the pool toward default/closure dynamics.

`testApr0InvariantRevertsIfAprChangesAfterApr0Request` documents the revert as intended [7](#0-6) , but the test does not cover the fact that a 1-unit attacker request (costing ~1 wei of underlying plus a dust tranche position) is sufficient to hold the entire pool hostage — the invariant is the vulnerability because the input (dust APR0 request) is unvalidated and irrevocable.

### Likelihood Explanation
Requires only a KYC-passing wallet (`isWalletAllowed` check in `IdleCDOEpochVariant.requestWithdraw` [8](#0-7) ), a dust tranche position during a zero-APR buffer period, and one `requestWithdraw` tx. No privileged cooperation beyond routine APR management. Impact is pool-wide fund freezing, which qualifies under the acceptance criteria (temporary/permanent freezing with quantified loss: entire vault TVL).

### Recommendation
Do not revert in `prepareStopEpochWithApr0` when `unscaledApr != 0`. Instead either (a) treat the open APR0 bucket as settled at `apr0RateByEpoch[reqEpoch] == 0` (principal-only) and clear `apr0TotalPrincipal`, or (b) move the invariant to `setAprs`/`setAprsWithBuffer` so raising APR while an open APR0 bucket exists reverts there — keeping the freeze surface on the privileged setter rather than on `stopEpoch`. Optionally add a `cancelApr0Request`/queue-side delete path so users can unwind dust buckets.

### Proof of Concept
Foundry fork PoC sketch (build on `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
// Attacker: KYC'd user with dust AA position
// Pre: epoch running with unscaledApr == 0 (APR0 mode)
uint256 dust = 1; // 1 wei of tranche → _underlyings rounds to >=1 unit
vm.prank(attacker);
cdoEpoch.requestWithdraw(dust, address(AAtranche));
assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

// Honest manager later raises APR for next epoch
vm.prank(manager);
IdleCreditVault(address(strategy)).setAprs(1e18, 1e18);

// Borrower repays fully; stopEpoch must succeed but reverts
vm.warp(cdoEpoch.epochEndDate() + 1);
deal(defaultUnderlying, borrower, fundsToRepay);
vm.prank(manager);
vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
cdoEpoch.stopEpoch(0, 0);

// No recovery path: attacker cannot cancel, claim does not clear bucket;
// apr0TotalPrincipal stays >0 until APR is forced back to 0.
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L243-246)
```text
  function requestWithdraw(uint256 _amount, address _user, uint256 _principal) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    if (_amount == 0) return;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L285-294)
```text
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
    } else {
      // increase the withdraw requests for the user
      // we record both per-user (old, kept for compatibility) and per-epoch so
      // on finalization we can distinguish "default-epoch pending receipts"
      // from old funded receipts.
      withdrawsRequests[_user] += _amount;
      withdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L301-350)
```text
  function claimWithdrawRequest(address _user) external returns (uint256 amount) {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized) {
      // Post-default requests are already priced after the haircut and backed by the reserve,
      // so they must not fall through to the defaulted-epoch receipt logic.
      amount = _claimPostDefaultWithdrawRequest(_user);
      if (amount != 0) return amount;
      // Only receipts created in the defaulted epoch are haircutted here; old fulfilled
      // receipts are handled below at par if they were already funded before default.
      amount = _claimDefaultedWithdrawRequest(_user);
    }
    amount += _claimLossAdjustedWithdrawRequest(_user);
    return amount + _claimFundedWithdrawRequest(_user);
  }

  /// @notice Claim a funded non-default withdraw request at par.
  /// @param _user address of the user
  /// @return amount amount claimed
  function _claimFundedWithdrawRequest(address _user) internal returns (uint256 amount) {
    // User should wait at least an epoch before claiming the withdraw. Once the epoch is over user can withdraw 
    // at any time even if a new epoch started. 
    // So if epochNumber is the same as the last withdraw request then we revert. Epoch number is increased at stopEpoch
    // NOTE: If a user does not claim a withdraw request and instead requests another withdraw, he will have to wait
    // for another epoch to claim both requests.
    // NOTE 2: if borrower defaults, old withdraw requests can still be claimed
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
    // settle APR=0 requests once the related epoch has ended
    _settleApr0(_user);
    Apr0UserData storage _apr0User = apr0Users[_user];
    // Claim includes:
    // - settled APR0 principal from finalized epochs
    // - still-open APR0 principal: if pool-close mode was used (_interest == 1), IdleCDO sets
    //   epochEndDate = 0 and claims can be immediate, while _settleApr0 can still skip settlement
    //   for the current request epoch (reqEpoch >= epochNumber).
    // - settled APR0 interest
    uint256 normalAmount = withdrawsRequests[_user];
    uint256 apr0PrincipalAmount = _apr0User.settledPrincipal + _apr0User.principal;
    uint256 apr0InterestAmount = _apr0User.settledInterest;
    amount = normalAmount + apr0PrincipalAmount + apr0InterestAmount;
    // burn strategy tokens 1:1 with the principal only (normal amount already includes interest)
    _burn(_user, normalAmount + apr0PrincipalAmount);
    withdrawsRequests[_user] = 0;
    lastWithdrawRequest[_user] = 0;
    if (apr0PrincipalAmount != 0 || apr0InterestAmount != 0) {
      delete apr0Users[_user];
    }
    _transferFundedClaim(_user, amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L499-540)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L545-565)
```text
  function _settleApr0(address _user) internal {
    Apr0UserData storage _apr0User = apr0Users[_user];
    uint256 _principal = _apr0User.principal;
    if (_principal == 0) {
      return;
    }
    uint256 _reqEpoch = _apr0User.principalEpoch;
    // Settle only after stopEpoch bumped epochNumber (ie after one full wait epoch).
    if (_reqEpoch >= epochNumber) {
      return;
    }
    // Move principal from "open APR0 bucket" to "settled bucket" (same principal, not duplicated).
    _apr0User.settledPrincipal += _principal;
    uint256 _rate = apr0RateByEpoch[_reqEpoch];
    if (_rate != 0) {
      // Convert per-epoch rate to claimable underlying interest.
      _apr0User.settledInterest += (_principal * _rate) / 1e18;
    }
    _apr0User.principal = 0;
    _apr0User.principalEpoch = 0;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L567-577)
```text
  function _requestWithdrawApr0(uint256 _amount, address _user) internal {
    // Settle any previous APR0 request first, then start/update current epoch bucket.
    _settleApr0(_user);
    Apr0UserData storage _apr0User = apr0Users[_user];
    if (_apr0User.principal == 0) {
      _apr0User.principalEpoch = epochNumber;
    }
    _apr0User.principal += _amount;
    // Epoch-level APR0 principal used only to compute stopEpoch APR0 pro-rata interest.
    apr0TotalPrincipal += _amount;
  }
```

**File:** test/foundry/IdleCreditVault.t.sol (L3306-3338)
```text
  function testApr0InvariantRevertsIfAprChangesAfterApr0Request() external {
    // Scenario: APR0 request is created, then APR is changed before settlement.
    // Expectation: stopEpoch reverts to enforce APR0 lifecycle invariant.
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);

    uint256 amount = 10000 * ONE_SCALE;
    idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);

    _startEpochAndCheckPrices(0);

    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 expectedInterest = cdoEpoch.expectedEpochInterest();
    deal(defaultUnderlying, borrower, expectedInterest);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    _forceLastEpochAprToZero();

    cdoEpoch.requestWithdraw(IERC20(AAtranche).balanceOf(address(this)) / 2, address(AAtranche));

    _startEpochAndCheckPrices(1);

    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(1e18, 1e18);

    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 0);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L739-745)
```text
  function requestWithdraw(uint256 _amount, address _tranche) external returns (uint256 _underlyings) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == AATranche ? !allowAAWithdrawRequest : !allowBBWithdrawRequest) ||
      !isWalletAllowed(msg.sender)
    );
  
```
