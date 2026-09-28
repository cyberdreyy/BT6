### Title
APR0 withdraw receipt permanently bricks `stopEpoch` once APR turns non‑zero, freezing all vault funds - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault.prepareStopEpochWithApr0` reverts whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0`. An unprivileged lender can plant an APR0 receipt while the pool APR is 0; once the APR for the next epoch is set to a non‑zero value, `apr0TotalPrincipal` is never cleared (the `revert` happens before `apr0TotalPrincipal = 0`), so every subsequent `stopEpoch` call reverts `NotAllowed`, halting the epoch machine — the on‑chain analog of a NULL‑deref crash on a crafted input.

### Finding Description
In `requestWithdraw`, when `unscaledApr == 0` and the pool is not closed, the vault routes the receipt to `_requestWithdrawApr0`, which increments the global bucket `apr0TotalPrincipal` ( [1](#0-0) [2](#0-1) ).

At `stopEpoch`, the CDO calls `prepareStopEpochWithApr0`. If the bucket is non‑zero but `unscaledApr` is now non‑zero, the function reverts — before the `apr0TotalPrincipal = 0` reset at the end of the function:

```solidity
uint256 _principal = apr0TotalPrincipal;
if (_principal == 0) {
  return (_expInterest, _adjPendingWithdrawFees);
}
if (unscaledApr != 0) {
  revert NotAllowed();
}
...
apr0TotalPrincipal = 0;
``` [3](#0-2) 

The revert is self‑perpetuating: the only code paths that decrease `apr0TotalPrincipal` are `prepareStopEpochWithApr0` itself (which reverts first) and `_clearWithdrawClaimForEpoch` during a *default* finalization ( [4](#0-3) ). There is no user‑callable path to cancel the APR0 receipt, so the poisoned state cannot be cleared by the attacker being cooperative or by users claiming — claiming via `claimWithdrawRequest` moves principal to `settledPrincipal` only after a `stopEpoch` bumped `epochNumber` (`_settleApr0` returns early when `_reqEpoch >= epochNumber`), and the funded claim does not touch `apr0TotalPrincipal` ( [5](#0-4) ).

Broken invariant: the epoch state machine must always be able to settle; a single dust receipt must not make `stopEpoch` uncallable.

### Impact Explanation
With `stopEpoch` permanently reverting, the epoch never ends: no deposits/withdrawals are priced, `requestWithdraw` proceeds normally but claims can never be funded, and the entire pool TVL plus all pending receipts are frozen (temporary freezing of the full vault, and effectively permanent unless the manager manually drives `unscaledApr` back to 0 and completes an epoch purely to flush the bucket — during which no interest accrues and the borrower cannot operate normally). Quantified impact: 100% of pool NAV plus `pendingWithdraws`/`pendingInstantWithdraws` are locked.

### Likelihood Explanation
Only requires: (1) the pool running an epoch with `unscaledApr == 0` (a supported mode — "apr0" flow); (2) any KYC‑passing lender requesting a withdraw of a dust amount; (3) the APR later being set non‑zero — which honest actors do routinely when renegotiating borrower terms via `setAprs`/`setAprsWithBuffer` ( [6](#0-5) ). No privileged‑role misbehavior is needed; the attacker only sequences around honest manager/borrower calls. Cost to the attacker is one dust deposit plus gas.

### Recommendation
Do not couple the `apr0TotalPrincipal` reset to the `unscaledApr != 0` revert. Instead, either:
- settle/migrate the open APR0 bucket when APR changes (e.g., in `setApr`/`setAprs`, finalize `apr0RateByEpoch[epochNumber] = 0` and zero `apr0TotalPrincipal`), or
- replace the revert with graceful handling: treat a non‑zero APR at stop as "APR0 receipts earn zero rate for this epoch" and still clear `apr0TotalPrincipal`, since `apr0RateByEpoch` entries of 0 already produce 0 interest in `_settleApr0`.

### Proof of Concept
Foundry fork sketch (against the repo's `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function testApr0ReceiptBricksStopEpoch() external {
  // setup: depositAA with a KYC'd user while unscaledApr == 0
  uint256 amount = 1_000 * ONE_SCALE;
  uint256 trancheAmount = idleCDO.depositAA(amount);
  _transferBurnedTrancheTokens(address(this), true);

  // run one epoch at APR 0, then during the next buffer request a withdraw
  // -> _requestWithdrawApr0 sets apr0TotalPrincipal > 0
  _startEpochAndCheckPrices(0);
  _stopEpochAndCheckPrices(0, 0, _expectedFundsEndEpoch()); // unscaledApr == 0
  cdoEpoch.requestWithdraw(trancheAmount / 1e6, address(AAtranche)); // dust
  assertGt(strategy.apr0TotalPrincipal(), 0);

  // honest manager/borrower raises APR for the coming epoch
  vm.prank(manager);
  strategy.setAprs(5e18, 5e18); // unscaledApr != 0 now

  // epoch ends; borrower repays; stopEpoch now always reverts
  deal(defaultUnderlying, borrower, cdoEpoch.expectedEpochInterest() + strategy.pendingWithdraws());
  vm.warp(cdoEpoch.epochEndDate() + 1);
  vm.prank(manager);
  vm.expectRevert(NotAllowed.selector);
  cdoEpoch.stopEpoch(0, 0);

  // repeating always reverts: apr0TotalPrincipal is never cleared
  vm.prank(manager);
  vm.expectRevert(NotAllowed.selector);
  cdoEpoch.stopEpoch(0, 0);
}
```

One caveat to flag honestly: I could not verify within this session the exact call ordering inside `IdleCDOEpochVariant.stopEpoch` (whether `setAprsWithBuffer` runs before or after `prepareStopEpochWithApr0`). If the CDO sets the new APR inside the same `stopEpoch` transaction *before* `prepareStopEpochWithApr0`, the attack is even easier — the APR 0→positive transition itself triggers the freeze. If it sets APR after, the freeze requires the manager to raise APR mid‑lifecycle via `setAprs`, which is a routine honest action. Either ordering leaves a reachable, attacker‑planted revert with full‑TVL freezing impact.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L206-235)
```text
  function setAprs(uint256 _unscaledApr, uint256 _apr) external {
    unscaledApr = _unscaledApr;
    // here we also check that msg.sender is allowed
    setApr(_apr);
  }

  /// @notice set both the unscaled APR and APR scaled by epoch plus buffer duration.
  /// @dev only CDO and manager can set the APR through `setApr`.
  /// @param _unscaledApr unscaled APR
  /// @param _duration epoch duration
  /// @param _buffer buffer duration
  function setAprsWithBuffer(uint256 _unscaledApr, uint256 _duration, uint256 _buffer) external {
    unscaledApr = _unscaledApr;
    setApr(_duration == 0 ? _unscaledApr : _unscaledApr * (_duration + _buffer) / _duration);
  }

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L285-295)
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
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L499-541)
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
  }
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L821-831)
```text
    Apr0UserData storage apr0User = apr0Users[_user];
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
