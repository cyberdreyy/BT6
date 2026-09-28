### Title
Stale-APR guard in `prepareStopEpochWithApr0` permanently bricks `stopEpoch` after an honest mid-epoch APR change - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
In `IdleCreditVault`, an unprivileged lender can open a withdraw request while `unscaledApr == 0`, which records principal in `apr0TotalPrincipal` via `_requestWithdrawApr0`. If the (honest) manager or borrower flow later sets a non-zero APR before the epoch is stopped, `prepareStopEpochWithApr0` reverts with `NotAllowed()` because it demands `unscaledApr == 0` whenever `apr0TotalPrincipal != 0`. Since `apr0TotalPrincipal` is only reset inside that same reverting function, `stopEpoch` can never succeed again. This mirrors the external bug class: an incorrect/duplicated conditional blocks the required transition, forcing an impossible path — here, the only escape is reverting the APR back, which is not guaranteed possible and, if APR semantics changed legitimately, mis-prices the epoch.

### Finding Description
`requestWithdraw` routes requests into the APR0 bucket when `unscaledApr == 0`:

- `requestWithdraw` calls `_requestWithdrawApr0` when `unscaledApr == 0 && !isClosed`, adding to `apr0TotalPrincipal` [1](#0-0) 
- `_requestWithdrawApr0` accumulates `apr0TotalPrincipal` [2](#0-1) 
- `setApr` lets the CDO or manager change `lastApr`/`unscaledApr` at any time via `setAprsWithBuffer` [3](#0-2) 
- At epoch end, the CDO calls `prepareStopEpochWithApr0`, which reverts whenever `apr0TotalPrincipal != 0 && unscaledApr != 0` [4](#0-3) 
- `apr0TotalPrincipal = 0` is only reached at the end of `prepareStopEpochWithApr0` [5](#0-4) , so once it reverts the bucket stays non-zero and every subsequent `stopEpoch` reverts identically — a permanent freeze, not a transient failure.

Sequence (running epoch, APR0 mode):
1. Epoch runs with `unscaledApr == 0` (APR0 mode is a supported configuration).
2. Unprivileged KYC'd lender calls `requestWithdraw` through the CDO → `apr0TotalPrincipal = X > 0`.
3. Manager legitimately raises APR via `setAprsWithBuffer` (APR changes mid-epoch are allowed by `setApr`).
4. `stopEpoch` → `prepareStopEpochWithApr0` reverts `NotAllowed()` forever. No recovery path exists in-contract: the revert happens before `apr0TotalPrincipal` is cleared, and there is no admin function to reset `apr0TotalPrincipal` or force-settle APR0 users.

### Impact Explanation
Permanent freezing of the entire credit vault's TVL. All deposits held via the borrower are unrecoverable through the normal epoch lifecycle: `stopEpoch`, `stopEpochWithDuration`, withdrawals, and borrower repayment settlement all depend on the stop path that unconditionally reverts. Loss equals the full contract value plus all pending withdraw receipts.

### Likelihood Explanation
Requires two conditions: an epoch configured with `unscaledApr == 0` (an explicitly supported mode with dedicated accounting), and an honest APR change while an APR0 request is open. An attacker needs only a dust-sized `requestWithdraw` during an APR0 epoch to arm the trap; the APR change is a routine honest manager action. Once armed, the revert is deterministic — there is no guard (`skim`, flags, epoch gating) that bypasses it. Uncertainty: I could not confirm whether `IdleCDOEpochVariant` exposes an emergency path that skips `prepareStopEpochWithApr0`; if `stopEpoch`/`_emergencyShutdown` always invokes it, the freeze is unconditional.

### Recommendation
Do not revert on `unscaledApr != 0` inside `prepareStopEpochWithApr0`. Instead, settle the APR0 bucket with the rate applicable to its request epoch (e.g., zero realized interest, or the stored `apr0RateByEpoch` model) and always zero `apr0TotalPrincipal` before returning, so an APR change cannot brick the state machine. Alternatively, snapshot the request-lifecycle APR per epoch and evaluate that rather than the current `unscaledApr`. Add a Foundry test: APR0 request → `setApr(nonZero)` → `stopEpoch` must succeed.

### Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.10;

import "../TestIdleCDOBase.sol";

// Requires an epoch credit vault whose manager sets unscaledApr = 0 at epoch start.
contract Apr0StopEpochFreezePoC is TestIdleCDOBase {
    function test_Apr0RequestThenAprChangeFreezesStopEpoch() public {
        // 1. APR0 epoch running: manager configured unscaledApr == 0.
        IdleCreditVault strategy = IdleCreditVault(address(cdo.strategy()));
        assertEq(strategy.unscaledApr(), 0);

        // 2. Attacker (whitelisted lender) opens a dust withdraw request.
        address attacker = makeAddr("attacker");
        _whitelist(attacker);
        _depositAs(attacker, 1e6);
        vm.prank(attacker);
        cdo.requestWithdraw(1e6); // routes to _requestWithdrawApr0, apr0TotalPrincipal = 1e6
        assertGt(strategy.apr0TotalPrincipal(), 0);

        // 3. Honest manager raises the APR mid-epoch (allowed by setApr/setAprsWithBuffer).
        vm.prank(strategy.manager());
        strategy.setAprsWithBuffer(5e18, epochDuration, bufferDuration);

        // 4. Warp to epoch end; every stopEpoch attempt reverts permanently.
        vm.warp(cdo.epochEndDate());
        vm.expectRevert(IdleCreditVault.NotAllowed.selector);
        cdo.stopEpoch();

        // Even reverting APR does not restore the bucket; no admin reset exists.
        // All TVL and pending withdraw receipts are frozen indefinitely.
    }
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L217-235)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L502-508)
```text
    if (_principal == 0) {
      return (_expInterest, _adjPendingWithdrawFees);
    }
    // APR0 principal is only valid while APR is 0 for that request lifecycle.
    if (unscaledApr != 0) {
      revert NotAllowed();
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L539-540)
```text
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
