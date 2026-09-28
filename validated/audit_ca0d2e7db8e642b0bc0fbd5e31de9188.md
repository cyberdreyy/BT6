### Title
Unprivileged APR0 withdraw request permanently bricks `stopEpoch` if APR is later raised - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug is a NULL-pointer dereference causing denial of service. The closest credit-vault analog is a "dereference of inconsistent/unset state" that makes a core epoch function unconditionally revert: `prepareStopEpochWithApr0` hard-reverts whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0`. An unprivileged lender can open an APR=0 withdraw request, and if the (honest) manager later raises the APR before the epoch ends — a permitted, ordinary action — `stopEpoch` reverts forever, permanently freezing all LP funds in the vault.

### Finding Description
`requestWithdraw` records APR=0 requests in the `apr0TotalPrincipal` bucket when `unscaledApr == 0` [1](#0-0) , and `_requestWithdrawApr0` increments `apr0TotalPrincipal` [2](#0-1) .

At epoch end, `stopEpoch` calls `prepareStopEpochWithApr0`, which reverts if APR is no longer zero:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:505-508
// APR0 principal is only valid while APR is 0 for that request lifecycle.
if (unscaledApr != 0) {
  revert NotAllowed();
}
```

The only places `apr0TotalPrincipal` is decreased are inside this same reverting function (`apr0TotalPrincipal = 0` at line 540, unreachable once the revert triggers) and `_clearWithdrawClaimForEpoch` with `_isClearingApr0`, which requires the pool to be defaulted and finalized [3](#0-2) . But default can only be entered via a successful `stopEpoch`/`stopEpochWithDuration`, which itself reverts. The attacker's own claim path (`_settleApr0`) cannot clear the bucket either, since settlement requires `epochNumber` to advance — which only `stopEpoch` does [4](#0-3) .

`setAprs`/`setApr` is callable by the manager at any time and is an honest, expected operation (e.g., repricing the loan mid-epoch) [5](#0-4) .

### Impact Explanation
Permanent freezing of all vault funds. Once `apr0TotalPrincipal > 0` and `unscaledApr != 0`, every `stopEpoch` call reverts: the running epoch can never end, no withdraw request (normal, APR0, or instant-funded at epoch end) can be claimed, new epochs can never start, and the default/finalization recovery path that would clean up `apr0TotalPrincipal` is unreachable because it is gated behind the same reverting `stopEpoch`. The entire pool NAV plus pending receipts is locked. Loss = full TVL plus all pending withdraw basis.

### Likelihood Explanation
Requires two conditions: (1) an unprivileged, KYC-passing lender calls `requestWithdraw` while `unscaledApr == 0` (the APR0 mode is a first-class supported flow, and the attacker needs only a normal deposit and a withdraw request — no privilege, no default needed); (2) the manager subsequently sets a nonzero APR before the epoch closes. Condition (2) is an honest manager action outside the attacker's control, but it is a routine configuration change the code explicitly permits mid-lifecycle, and nothing warns the manager that doing so will brick the vault. The attacker can also keep the poison state alive cheaply with a dust-sized APR0 request. One caveat I could not fully verify within scope: whether an alternate privileged escape hatch (e.g., `restoreOperations`/`_emergencyShutdown` or direct manager intervention on strategy storage) could eventually unwind the lock — even if one exists, the funds remain frozen for the duration and recovery is not part of normal operation.

### Recommendation
Do not gate `stopEpoch` on `unscaledApr == 0` when an APR0 bucket exists. Either:
- settle the open APR0 bucket at the APR in effect for the epoch (or at the rate recorded at request time via a per-epoch stored APR), computing `apr0RateByEpoch[epochNumber]` normally and clearing `apr0TotalPrincipal`, or
- snapshot-and-zero `apr0TotalPrincipal` when `setAprs` transitions APR away from zero, converting open requests into settled/fixed-rate claims, so a later APR change cannot invalidate already-booked requests.

### Proof of Concept
Reproducible Foundry sequence (modeled on `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testApr0RequestThenAprRaiseBricksStopEpoch() external {
    uint256 amount = 10_000 * ONE_SCALE;

    // Unprivileged attacker deposits while pool APR is 0
    _depositWithUser(attacker, amount, true);          // fixed-APR path with unscaledApr == 0

    // Attacker opens an APR0 withdraw request during buffer period
    uint256 bal = IERC20(AAtranche).balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(bal / 2, address(AAtranche));
    assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

    // Epoch starts (borrower draws funds)
    _startEpochAndCheckPrices(0);

    // Honest manager reprices the loan: raise APR before epoch end
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(10e18, 10e18 * epochScaling);

    // Epoch end: every stopEpoch attempt reverts with NotAllowed
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, expectedInterest);

    // Same for the default-triggering variant: permanent freeze
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 0);

    // epochNumber never advances: no claim, no default, no new epoch — funds frozen
}
```

Expected result: both `stopEpoch` calls revert at `prepareStopEpochWithApr0` (`IdleCreditVault.sol:506-508`) because `apr0TotalPrincipal != 0 && unscaledApr != 0`, and since `epochNumber` can never increase, the APR0 bucket is never settleable and the default-recovery cleanup path is unreachable — the vault is permanently frozen by an unprivileged request plus a routine honest APR change.

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
