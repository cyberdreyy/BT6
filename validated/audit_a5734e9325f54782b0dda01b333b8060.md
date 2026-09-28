### Title
Unprivileged APR0 withdraw request permanently bricks `stopEpoch` once APR is raised — ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The external CVE is a crash-class bug: an unhandled input state causes a hard failure (dissector crash → denial of service). The closest credit-vault analog is a hard `revert` on the epoch lifecycle path. In `IdleCreditVault.prepareStopEpochWithApr0`, the call unconditionally reverts with `NotAllowed` whenever `unscaledApr != 0` while `apr0TotalPrincipal != 0`. An unprivileged lender can keep a dust-sized APR0 withdraw request open; when the honest manager/CDO later sets a non-zero APR for a subsequent epoch, every `stopEpoch` call reverts, permanently freezing all vault funds.

### Finding Description
`requestWithdraw` routes requests into the APR0 bucket whenever `unscaledApr == 0`, incrementing `apr0TotalPrincipal` via `_requestWithdrawApr0` [1](#0-0) . That bucket is only closed inside `prepareStopEpochWithApr0`, which `stopEpoch` invokes — but it reverts before doing so if `unscaledApr` has been raised:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:505-508
// APR0 principal is only valid while APR is 0 for that request lifecycle.
if (unscaledApr != 0) {
    revert NotAllowed();
}
```

`apr0TotalPrincipal` is zeroed only at the end of a *successful* `prepareStopEpochWithApr0` call (line 540), so once the revert path is hit there is no other code path that clears it. `_settleApr0` only migrates per-user `principal` to `settledPrincipal`; it never decrements `apr0TotalPrincipal` [2](#0-1) . There is no recovery: `setApr`/`setAprsWithBuffer` only mutate `lastApr`/`unscaledApr` and never touch the bucket [3](#0-2) .

Attack sequence:
1. Epoch runs (or buffer) with `unscaledApr == 0` — a normal, legitimate mode.
2. Attacker (any KYC-passing lender) deposits a minimal amount and calls `cdoEpoch.requestWithdraw(dust)`. `apr0TotalPrincipal += dust`.
3. Manager honestly raises APR for a later epoch via `setAprsWithBuffer`/`setApr` — `unscaledApr` becomes non-zero while the dust request is still pending.
4. `stopEpoch` → `prepareStopEpochWithApr0` hits the `unscaledApr != 0` revert. Since `apr0TotalPrincipal` can never be cleared after this, *every* subsequent `stopEpoch` reverts. The epoch state machine is permanently stuck in "running"; `claimWithdrawRequest`/`claimInstantWithdrawRequest` and all LP redemptions are blocked indefinitely.

### Impact Explanation
Permanent freezing of 100% of vault TVL — the strongest allowed impact class for a DoS analog. All active LP principal, pending withdraw receipts, and instant-withdraw claims are locked because `epochNumber` can never advance and no claim path resolves without a stopped epoch (for normal claims, `_claimFundedWithdrawRequest` reverts while `epochNumber <= lastWithdrawRequest` [4](#0-3) ). Quantified loss = full `getContractValue()` plus all `pendingWithdraws` at the moment of the brick.

### Likelihood Explanation
- Attacker cost is a dust deposit + one `requestWithdraw` while APR is 0.
- The trigger requires an honest privileged action (manager raising APR between epochs), which is a routine configuration change, not an attacker assumption.
- Residual uncertainty: whether APR is raised while an epoch is still running or only during buffer, and whether any operational convention prevents mixing modes — `requestWithdraw` and `setApr` do not enforce such a convention on-chain. The revert itself is unconditional once the state combination exists, so if the state is reachable, the freeze is deterministic.

### Recommendation
- In `prepareStopEpochWithApr0`, do not revert on `unscaledApr != 0` while `apr0TotalPrincipal != 0`; instead settle the APR0 bucket at the rate stored for its request epoch (or zero interest) and clear `apr0TotalPrincipal`, so `stopEpoch` can always proceed.
- Alternatively, block `requestWithdraw` into the APR0 path when an APR change is pending, and/or sweep `apr0TotalPrincipal` into `pendingWithdraws` inside `setAprsWithBuffer` when `unscaledApr` transitions away from 0.
- Add a state-transition invariant test: APR0 request → APR raised → `stopEpoch` must succeed.

### Proof of Concept
Foundry fork PoC outline (against mainnet `IdleCreditVault`/`IdleCDOEpochVariant`):

```solidity
// test/foundry/Apr0StopEpochBrick.t.sol
function testApr0RequestBricksStopEpochAfterAprRaise() external {
    // 1. Configure vault in APR0 mode (unscaledApr == 0), deposit as attacker (KYC'd).
    vm.prank(manager);
    strategy.setAprsWithBuffer(0, epochDuration, bufferDuration);
    deal(underlying, attacker, 1e6);
    _depositAsAttacker(1e6);                       // mints tranche tokens

    vm.prank(manager);
    cdoEpoch.startEpoch();

    // 2. Attacker requests a dust withdraw while APR0 is active.
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(1, address(AAtranche)); // apr0TotalPrincipal = 1

    // 3. Honest manager raises APR for the next configuration.
    vm.prank(manager);
    strategy.setAprsWithBuffer(10e18, epochDuration, bufferDuration); // unscaledApr != 0

    // 4. Warp to epoch end; borrower repays; stopEpoch must succeed but reverts.
    vm.warp(cdoEpoch.epochEndDate() + 1);
    _fundBorrowerRepayment();
    vm.expectRevert(NotAllowed.selector);
    vm.prank(manager);
    cdoEpoch.stopEpoch(apr, 0);

    // 5. Repeat: every subsequent stopEpoch reverts -> permanent freeze.
    vm.expectRevert(NotAllowed.selector);
    vm.prank(manager);
    cdoEpoch.stopEpoch(apr, 0);
    assertEq(underlying.balanceOf(attacker), /* still locked */ 0);
}
```

Note: the exact privileged-call wiring (where `setAprsWithBuffer` is invoked relative to epoch boundaries) should be confirmed against `IdleCDOEpochVariant.startEpoch`; if APR can only change during buffer while `apr0TotalPrincipal` still counts an in-flight epoch request, the revert still fires at the next `stopEpoch`. If review shows `unscaledApr` is immutable per deployment or transitions are otherwise impossible with pending APR0 principal, this degrades to no-vulnerability.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L326-328)
```text
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
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
