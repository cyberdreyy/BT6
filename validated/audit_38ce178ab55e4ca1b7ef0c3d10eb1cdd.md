### Title
APR0 withdraw request permanently reverts `stopEpoch` when APR is raised above zero, freezing all pending withdrawals - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`prepareStopEpochWithApr0` reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0`. An unprivileged, KYC-passing lender can open an APR0 withdraw request while the pool is at 0 APR and keep it unclaimed; from that point, every `stopEpoch`/`stopEpochWithDuration` call reverts as long as the manager runs the pool at any non-zero APR. The epoch state machine wedges in the "running" phase: no new epoch can start, no pending receipts can be funded, and all queued withdraw claims are frozen until the manager is forced to run a full epoch at 0% APR to flush the attacker's bucket.

### Finding Description
`requestWithdraw` routes to `_requestWithdrawApr0` when `unscaledApr == 0`, which increments the global `apr0TotalPrincipal` bucket [1](#0-0) [2](#0-1) . On `stopEpoch`, the CDO calls `prepareStopEpochWithApr0`, which hits this ordering: [3](#0-2) 

```solidity
uint256 _principal = apr0TotalPrincipal;
if (_principal == 0) {
  return (_expInterest, _adjPendingWithdrawFees);
}
// APR0 principal is only valid while APR is 0 for that request lifecycle.
if (unscaledApr != 0) {
  revert NotAllowed();   // <-- reverts BEFORE the bucket is cleared
}
```

The revert happens **before** `apr0TotalPrincipal = 0` is reached, so the bucket can never be cleared while APR is non-zero. There is no user-side cancel for a normal withdraw request in `IdleCreditVault`, and the only other clearing path is `_clearWithdrawClaimForEpoch` with `_isClearingApr0 = true`, reachable only through defaulted-epoch claims after `finalizeDefault` [4](#0-3) . The attacker has no incentive to claim (claiming their receipt is what clears it, but they simply don't call it; `claimWithdrawRequest` is user-initiated).

Sequence:
1. Manager sets APR to 0 (a legitimate configuration, e.g. to wind down or pause yield). Attacker deposits via `depositAA` and calls `requestWithdraw`. `_requestWithdrawApr0` sets `apr0TotalPrincipal = attackerAmount`.
2. Manager (honest) sets APR back to a positive value and calls `startEpoch` — no check on `apr0TotalPrincipal` at start.
3. At epoch end, `stopEpoch` calls `prepareStopEpochWithApr0` → reverts. Every retry reverts. `epochEndDate` stays set and `isEpochRunning()` stays true, so `startEpoch` also reverts and `claimWithdrawRequest` reverts via the `epochNumber <= lastWithdrawRequest` gate [5](#0-4) .
4. Recovery requires the manager to set `unscaledApr` back to 0 and run one full epoch at 0% APR — forfeiting an entire epoch of interest for all LPs — and even then the attacker can re-request during the next 0-APR buffer, repeating the lock.

The attacker only needs a dust-sized request (any `_amount > 0` makes `apr0TotalPrincipal != 0`), so cost is negligible relative to freezing the whole pool's withdrawal pipeline.

### Impact Explanation
This is a direct analog of the CVE's availability bug class: a low-cost, unprivileged action causes a repeatable "hang" of the service. Concretely:

- **Temporary freezing of all funds**: while `stopEpoch` reverts, no pending withdraw receipt can be funded (`collectWithdrawFunds` is only called from `stopEpoch`), borrowers cannot be repaid/recalled through the epoch flow, and every LP's `claimWithdrawRequest` reverts. All pool TVL plus all queued receipts are frozen for at least one full `epochDuration + bufferPeriod`.
- **Forced 0% APR epoch**: the only recovery path is `setAprs(0)` + a complete epoch, so all LPs lose one epoch of interest (quantifiable: `expectedEpochInterest` for that epoch ≈ TVL × APR × duration, set to zero).
- Repeatable: the attacker can re-arm the revert during each 0-APR buffer by submitting a new dust request.

Existing guards do not stop it: `_settleApr0` only runs on user claim (attacker never calls), `startEpoch` doesn't check `apr0TotalPrincipal`, and `prepareStopEpochWithApr0` reverts before clearing state.

### Likelihood Explanation
Medium. It requires the pool to operate at `unscaledApr == 0` at some point — a supported and documented mode (`_requestWithdrawApr0`, `apr0RateByEpoch`, APR0 tests exist) — plus a manager APR change while an APR0 request is pending. Any KYC-passing lender or tranche holder can execute it with dust capital and no privileged role; the honest manager triggering the freeze (raising APR) is normal operations.

### Recommendation
- In `prepareStopEpochWithApr0`, settle/close the APR0 bucket instead of reverting when `unscaledApr != 0` — e.g. move the revert inside the interest-allocation branch (only skip pro-rata interest when APR changed) and still execute `apr0TotalPrincipal = 0`, treating pending APR0 principal as already-funded principal.
- Alternatively, add a user/manager escape path (cancel APR0 request or a `sweepApr0Bucket`) so the global bucket can be cleared without waiting for the attacker to claim.
- Reject APR0 requests at `requestWithdraw` time if a pending `apr0TotalPrincipal` exists under a different effective APR regime.

### Proof of Concept
Reproducible Foundry fork PoC (structure, mirroring `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testApr0RequestFreezesStopEpochAfterAprRaise() external {
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);   // APR = 0 mode

    uint256 amount = 10000 * ONE_SCALE;
    idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);

    _startEpochAndCheckPrices(0);
    // stop epoch 0 at APR 0
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, cdoEpoch.expectedEpochInterest());
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    // attacker opens a dust APR0 withdraw request during the buffer
    uint256 dust = 1;
    cdoEpoch.requestWithdraw(dust, address(AAtranche));
    assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

    // manager legitimately raises APR back to non-zero and starts epoch 1
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(initialProvidedApr, initialProvidedApr);
    _startEpochAndCheckPrices(1);

    // every stopEpoch reverts while apr0TotalPrincipal != 0 and apr != 0
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, type(uint256).max);
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 0);

    // second withdrawer's funded receipt is now unclaimable
    // cdoEpoch.claimWithdrawRequest() reverts via epochNumber <= lastWithdrawRequest gate
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L285-286)
```text
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L326-328)
```text
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L822-831)
```text
    if (apr0User.principal != 0 && apr0User.principalEpoch == _claimEpoch) {
      if (_isClearingApr0) {
        uint256 apr0Principal = apr0User.principal;
        uint256 totalApr0Principal = apr0TotalPrincipal;
        // prepareStopEpochWithApr0 may already close the global APR0 bucket before default finalization.
        apr0TotalPrincipal = apr0Principal >= totalApr0Principal ? 0 : totalApr0Principal - apr0Principal;
      }
      apr0User.principal = 0;
      apr0User.principalEpoch = 0;
    }
```
