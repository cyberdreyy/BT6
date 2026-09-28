### Title
Unprivileged APR0 withdraw request permanently blocks `stopEpoch` after any APR increase, freezing the vault or forcing zero-yield operation - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
An unprivileged KYC-passing lender can write into the shared `apr0TotalPrincipal` accounting bucket by calling `requestWithdraw` while `unscaledApr == 0`. If the APR is later raised by the honest manager before the next `stopEpoch`, `prepareStopEpochWithApr0` reverts `NotAllowed()` on every subsequent `stopEpoch` call and `apr0TotalPrincipal` is never cleared, creating a deadlock: the epoch can never be stopped at a non-zero APR, so all pending withdraw claims and epoch transitions are frozen until the manager reverts the APR to 0. The attacker can repeat the request each time APR is 0, effectively vetoing any yield-bearing configuration of the vault.

### Finding Description
The external bug (CVE-2020-6796) is an unprivileged process corrupting shared state used by another component. The analog is the shared `apr0TotalPrincipal` accumulator in `IdleCreditVault`, which any lender can increase via `requestWithdraw`, and which the epoch state machine (controlled by the honest manager/owner) must consume during `stopEpoch`.

Flow:

1. `IdleCDOEpochVariant.requestWithdraw` calls `IdleCreditVault.requestWithdraw`, which mints a strategy-token receipt and, when `unscaledApr == 0` and the pool is not closed, routes to `_requestWithdrawApr0`, incrementing the global `apr0TotalPrincipal` [1](#0-0) [2](#0-1) 

2. At `stopEpoch`, the CDO calls `prepareStopEpochWithApr0`, which returns early only when `_principal == 0`. With a pending APR0 request it enforces `unscaledApr == 0`, reverting `NotAllowed()` otherwise [3](#0-2) 

3. The revert fires before `apr0TotalPrincipal = 0`, so the bucket is never closed on a failed call [4](#0-3) 

4. The user's own escape path is also blocked: `_settleApr0` only settles once `epochNumber` advances past `principalEpoch`, but `epochNumber` only increments inside `deposit` during a successful `stopEpoch` [5](#0-4) [6](#0-5) . And `requestWithdraw` rejects new requests for a user with a pending APR0 position only in the loss-adjusted case; the APR0 user cannot self-clear while `unscaledApr != 0` because `requestWithdraw` requires `unscaledApr == 0` to even enter `_requestWithdrawApr0`, and there is no `cancelWithdraw` path.

Broken invariant: the epoch state machine must remain live — a lender's receipt write must not be able to deadlock `stopEpoch`. No existing guard prevents it: `requestWithdraw` is permissionless for allowed wallets, the `unscaledApr != 0` check in `prepareStopEpochWithApr0` treats a legitimate APR change as an error rather than settling the bucket at the old rate, and `collectWithdrawFunds`/default paths do not clear `apr0TotalPrincipal` either.

### Impact Explanation
Temporary freezing of all vault funds, or a permanent zero-yield constraint, triggered by an unprivileged lender and an ordinary honest-manager APR change:

- While `unscaledApr != 0` and `apr0TotalPrincipal != 0`, every `stopEpoch`/`stopEpochWithDuration` reverts in `prepareStopEpochWithApr0`, so the epoch cannot advance, pending withdraw receipts cannot be funded (`collectWithdrawFunds` is only reached inside the successful `stopEpoch` path), and instant/normal claimants cannot be paid. All deposits and receipt payouts are frozen for as long as the APR stays non-zero.
- The only recovery is for the manager to set APR back to 0 via `setAprs`/`setAprsWithBuffer`, run `stopEpoch`, and only then raise the APR for the next epoch. The attacker can frontrun each such recovery by depositing again and filing a fresh APR0 withdraw request while APR is 0 (allowed — new epochs can start at APR 0), repeating the veto indefinitely.
- Quantified effect: either (a) all LP and borrower funds locked for the duration of the deadlock, or (b) the vault forced to operate permanently at `unscaledApr == 0` (total loss of epoch yield for all tranche holders) to remain live. With a dust-sized request the attacker's cost is near zero.

### Likelihood Explanation
Likelihood is moderate and requires no malicious privileged role:

- Attacker requirement: any KYC-passing lender with tranche tokens; the APR0 request requires `allowAAWithdrawRequest`/`allowBBWithdrawRequest` and `isWalletAllowed`, all normal conditions [7](#0-6) .
- Trigger: an honest manager raising the APR between epochs via `setAprs`/`setAprsWithBuffer` while an APR0 receipt is pending — a routine operation, since `setApr` is explicitly manager-callable mid-lifecycle [8](#0-7) . The in-`stopEpoch` APR update (`_setScaledApr`) happens after `prepareStopEpochWithApr0`, so it cannot clear the bucket in the same transaction; the freeze requires a direct APR call, which the interface explicitly supports [9](#0-8) .
- No skim, default-check, or epoch gate prevents it; the `NotAllowed` revert is unconditional once `unscaledApr != 0` and `apr0TotalPrincipal != 0`.

### Recommendation
In `prepareStopEpochWithApr0`, do not revert when `unscaledApr != 0`. Instead, settle the open APR0 bucket at the recorded per-epoch terms (or at zero rate) for its `principalEpoch`: write `apr0RateByEpoch[principalEpoch]` (0 if the epoch produced no interest), move each user's principal eligibility forward via the existing `_settleApr0`/`apr0Users` machinery, and zero `apr0TotalPrincipal` unconditionally before returning, so a single honest APR change cannot deadlock the epoch state machine. Alternatively, block `setApr`/`setAprs` transitions to non-zero values while `apr0TotalPrincipal != 0` with an explicit dedicated error, making the dependency explicit rather than a latent `stopEpoch` revert.

### Proof of Concept
Foundry fork test sketch (solidity, against `test/foundry/IdleCreditVault.t.sol` harness helpers):

```solidity
// Setup: epoch running, unscaledApr == 0, lender holds tranche tokens.
// 1) Attacker (KYC'd lender) requests withdraw while APR is 0
vm.prank(attacker);
cdoEpoch.requestWithdraw(1e18, address(AAtranche)); // dust amount
assertGt(creditVault.apr0TotalPrincipal(), 0);

// 2) Honest manager raises APR mid-cycle (routine operation via setAprs)
vm.prank(manager);
creditVault.setAprs(5e18, scaledApr); // any non-zero apr <= maxApr

// 3) Owner tries to stop the epoch -> always reverts NotAllowed()
vm.prank(owner);
vm.expectRevert(NotAllowed.selector);
cdoEpoch.stopEpochWithDuration(newApr, interest, duration, 0);

// 4) Deadlock confirmed: pending receipts can never be funded and
//    the APR0 bucket can never clear while apr > 0. Repeating step 1
//    whenever apr == 0 permanently vetoes any positive-APR epoch.
```

Note on uncertainty: I was unable to fully verify `_transferFundedClaim`'s reserve handling (line ~897 onward was truncated) and whether a separate privileged remediation path exists; however, the revert-before-clear in `prepareStopEpochWithApr0` and the `epochNumber`-gated `_settleApr0` are directly evidenced and sufficient to establish the freeze.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L222-235)
```text
  /// @notice set the fixed apr
  /// @dev only cdo and manager can set the apr. If manager manually set apr from 
  /// here it will not be scaled to include the buffer period
  function setApr(uint256 _apr) public {
    address _cdo = idleCDO;

    // if cdo is not yet set we skip the check (this can happen only during the setup)
    if (_cdo != address(0)) {
      if (msg.sender != _cdo && msg.sender != manager) revert NotAllowed();
    }
    uint256 _maxApr = maxApr;
    if (_maxApr != 0 && _apr > _maxApr) revert NotAllowed();
    lastApr = _apr;
  }
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L468-472)
```text
  function _lossActiveBasis(IIdleCDOEpochVariant _cdo) internal view returns (uint256 activeBasis) {
    if (_cdo.isInterestMinted()) {
      // Accrued fees become AA shares before the loss burn, so the full CDO strategy-token
      // balance plus gross minted interest participates on the active side.
      return balanceOf(idleCDO) + _cdo.expectedEpochInterest();
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L545-555)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L596-617)
```text
  function deposit(uint256 _amount)
    external
    virtual
    override
    returns (uint256) {
    _onlyIdleCDO();
    if (_amount > 0) {
      underlyingToken.safeTransferFrom(msg.sender, address(this), _amount);
      _mint(msg.sender, _amount);
    }

    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
      // deposit done between epochs so we increase the counter
      totEpochDeposits += _amount;
    }

    return _amount;
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
