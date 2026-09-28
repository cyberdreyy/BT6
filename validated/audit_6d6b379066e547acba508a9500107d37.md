### Title
Anyone can set `lastApr`/`unscaledApr` on `IdleCreditVault` while `idleCDO` is unset, and `unscaledApr` persists after wiring, enabling APR0 mis-bucketing that DoSes `stopEpoch` — (File: `contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
The external bug class is a permissionless write to a storage slot during a setup window (before the privileged counterpart is wired), which later breaks a privileged flow. `IdleCreditVault.setApr`, `setAprs` and `setAprsWithBuffer` are callable by any EOA whenever `idleCDO == address(0)`, i.e. between `initialize` and the owner's `setWhitelistedCDO` call. The attacker-set `unscaledApr` is never rewritten by `setApr`, so a poisoned value silently survives after the CDO is wired and later causes `prepareStopEpochWithApr0` to revert, temporarily freezing all vault funds.

### Finding Description
In `contracts/strategies/idle/IdleCreditVault.sol`, `setApr` explicitly skips its caller check while the CDO is not yet configured: `if (_cdo != address(0)) { if (msg.sender != _cdo && msg.sender != manager) revert NotAllowed(); }` with the comment "this can happen only during the setup" [1](#0-0) . Both `setAprs` and `setAprsWithBuffer` write `unscaledApr` unconditionally before delegating to `setApr`, so during the same window any attacker can set `unscaledApr` to an arbitrary value including `0` [2](#0-1) .

`idleCDO` is only populated later via `setWhitelistedCDO`, which is `onlyOwner` but is a separate transaction from `initialize` [3](#0-2) . A mempool-observing attacker can land a transaction between the two calls and set `unscaledApr = 0` while leaving `lastApr` at a plausible non-zero value, or vice versa.

The poisoned `unscaledApr` persists because `setApr` only writes `lastApr` — the manager's normal "fix the APR" call does not restore `unscaledApr` [1](#0-0) . `unscaledApr` then controls the withdraw-request routing: `requestWithdraw` puts a request into the `apr0Users`/`apr0TotalPrincipal` bucket iff `unscaledApr == 0` [4](#0-3) . At epoch end, `prepareStopEpochWithApr0` reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0` but `unscaledApr != 0` — and conversely, if `unscaledApr` was poisoned to `0` while the pool intends a fixed APR, all withdraw requests are silently bucketed as APR0 and their interest is settled via `apr0RateByEpoch` instead of the funded normal path [5](#0-4) .

### Impact Explanation
Two attacker-selectable end states after wiring:

1. `unscaledApr = 0` poisoned during setup, then manager sets a real APR via `setApr`/`setAprs`. If the manager's correction only goes through `setApr`, `unscaledApr` stays `0`; withdraw requests during the running epoch accumulate in `apr0TotalPrincipal` and are treated as zero-APR requests, so APR0 users receive only the pro-rata `apr0RateByEpoch` interest computed in `prepareStopEpochWithApr0` rather than the negotiated fixed APR — a direct mispricing of owed interest baked into `pendingWithdraws` [6](#0-5) .
2. `unscaledApr = X` poisoned, then later reset to `0` or left mismatched: once `apr0TotalPrincipal != 0` exists, a subsequent `stopEpoch` under `unscaledApr != 0` makes `prepareStopEpochWithApr0` revert, so the epoch cannot be stopped until the APR is forced back — temporary freezing of all principal and funded withdraw claims for the duration [7](#0-6) .

### Likelihood Explanation
The exploit window is the gap between `initialize` and `setWhitelistedCDO`. If the factory or deployment script wires the CDO atomically in one transaction there is no window, which is the main mitigating uncertainty; I could not confirm from the factory whether wiring is atomic. Even with an atomic deployment, the permissionless branch remains dangerous for any vault that is intentionally operated before the CDO is set (the code comment anticipates exactly this setup flow). Once a poisoned `unscaledApr` is in place, the mis-bucketing triggers deterministically at the first `stopEpoch` or the first `requestWithdraw`, and the honest manager cannot be blamed for missing it because `unscaledApr` is not part of the `setApr` interface they would normally use [2](#0-1) .

### Recommendation
Enforce the caller check unconditionally once initialization is complete: revert in `setApr` whenever `msg.sender != idleCDO && msg.sender != manager`, regardless of whether `idleCDO` is set, or require `idleCDO != address(0)` before accepting any APR update and set the initial APR inside `initialize` only. Additionally, keep `unscaledApr` and `lastApr` updates atomic — have `setApr` also update `unscaledApr`, or remove the standalone unguarded write in `setAprs`/`setAprsWithBuffer` — so a stray `unscaledApr` cannot diverge from the APR the operator believes is active.

### Proof of Concept
A Foundry fork test would: (1) deploy `IdleCreditVault` proxy and call `initialize` with honest params (`unscaledApr = _apr`); (2) from an attacker EOA, call `setAprs(0, smallApr)` — succeeds because `idleCDO == 0`; (3) owner calls `setWhitelistedCDO(cdo)`; (4) deposits occur, epoch runs; (5) a user calls `requestWithdraw` — routed into `apr0Users` despite the pool running at a fixed APR; (6) manager attempts to correct with `setAprs(realUnscaledApr, realApr)` making `unscaledApr != 0` while `apr0TotalPrincipal > 0`; (7) `stopEpoch` reverts in `prepareStopEpochWithApr0`, demonstrating frozen epoch settlement until the APR is forced back, plus APR0-bucketed claims mispriced relative to the negotiated fixed APR.

Caveat: the window depends on `setWhitelistedCDO` not being executed in the same transaction as `initialize`; I did not verify the deployment script/factory path.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L206-220)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L225-235)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L284-294)
```text
    // `_amount` here is the post-management-fee principal bucket for that flow.
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L499-508)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L513-540)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L953-957)
```text
  /// @notice allow to update whitelisted address
  function setWhitelistedCDO(address _cdo) external onlyOwner {
    require(_cdo != address(0), "IS_0");
    idleCDO = _cdo;
  }
```
