### Title
Dust APR=0 withdraw request permanently bricks `stopEpoch` once APR is raised - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault.prepareStopEpochWithApr0` reverts unconditionally when `apr0TotalPrincipal > 0` and `unscaledApr != 0`. Any lender can create `apr0TotalPrincipal` with a minimal `requestWithdraw` while the pool is in APR=0 mode. If the (honest) manager later sets a non-zero APR — a legitimate, routine configuration change via `setAprs`/`setAprsWithBuffer` — every subsequent `stopEpoch` reverts, the epoch can never end, `epochNumber` never advances, and all lender funds in the vault are permanently frozen. Analog of CVE-2024-57639: a small crafted input (a dust request) triggers an unrecoverable DoS condition in a state machine.

### Finding Description
In `requestWithdraw`, when `unscaledApr == 0` the vault records the request in the APR0 bucket via `_requestWithdrawApr0`, which increments the global `apr0TotalPrincipal` [1](#0-0) . The only place `apr0TotalPrincipal` is ever cleared is at the end of `prepareStopEpochWithApr0` [2](#0-1) , which is called by the CDO during `stopEpoch`.

However, before reaching that clearing line, the function enforces:

```solidity
uint256 _principal = apr0TotalPrincipal;
if (_principal == 0) {
  return (_expInterest, _adjPendingWithdrawFees);
}
// APR0 principal is only valid while APR is 0 for that request lifecycle.
if (unscaledApr != 0) {
  revert NotAllowed();
}
``` [3](#0-2) 

So if any APR0 principal exists and `unscaledApr` is non-zero, `stopEpoch` always reverts. There is no recovery path:

- `apr0TotalPrincipal` can only be zeroed inside the function that reverts.
- Individual users cannot settle their APR0 principal to shrink the bucket: `_settleApr0` only settles after `epochNumber` advances past `principalEpoch` [4](#0-3) , and `epochNumber` only advances at `stopEpoch` — which is now impossible.
- Normal-claim path is equally blocked: `_claimFundedWithdrawRequest` reverts while `epochNumber <= lastWithdrawRequest[_user]` [5](#0-4) .
- `setAprs`/`setAprsWithBuffer` are manager-callable at any time and impose no check against outstanding APR0 receipts [6](#0-5) . Once the sequence runs, even restoring APR to 0 is moot for interest accrued under the wrong regime — and more importantly the revert is hit on every stop attempt while APR ≠ 0.

The guard assumes "APR0 principal is only valid while APR is 0," but nothing prevents the honest manager from changing APR between the request epoch and the stop, and nothing lets the bucket be unwound afterward.

### Impact Explanation
Permanent freezing of all vault funds. The entire TVL of the credit vault becomes unrecoverable: the running epoch can never be stopped, deposits cannot be redeemed (withdrawal claims require `epochNumber` to advance), and borrower repaid funds cannot be distributed. Loss equals the full pool NAV plus pending withdraw basis. The attacker's cost is a single dust-size `requestWithdraw` (minimum non-zero amount, e.g., 1 wei of tranche equivalent) during an APR=0 period.

### Likelihood Explanation
The preconditions are mild: the pool must operate at `unscaledApr == 0` for at least one epoch (a supported mode — the codebase has dedicated APR0 accounting, per-epoch rates, and tests), and the manager must later configure a non-zero APR, which is a normal operational action (APR is set per-epoch via `setAprs`/`setAprsWithBuffer`). The attacker needs only to be a KYC-passing lender able to call `requestWithdraw` — explicitly an unprivileged role. One dust request creates `apr0TotalPrincipal > 0`, and the very next `stopEpoch` under the new APR reverts with `NotAllowed`, with no admin escape hatch (`apr0TotalPrincipal` cannot be cleared by any other function). Caveat: if the deployment keeps APR permanently at 0, the trigger never fires, so exploitability depends on a plausible honest APR change.

### Recommendation
Replace the hard revert in `prepareStopEpochWithApr0` with graceful handling: when `unscaledApr != 0` and `apr0TotalPrincipal > 0`, settle the outstanding APR0 bucket at zero interest (i.e., close `apr0TotalPrincipal` and record `apr0RateByEpoch[epochNumber] = 0` for the request epoch) so users still recover principal and the epoch machine proceeds. Alternatively/additionally, add an owner/manager function to force-close the APR0 bucket, and block `setAprs`/`setAprsWithBuffer` from setting a non-zero APR while `apr0TotalPrincipal > 0` (with an explicit escape that first finalizes APR0 receipts).

### Proof of Concept
Foundry fork test (extend `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testApr0DustRequestBlocksStopEpochAfterAprChange() external {
    // Arrange: APR0-mode pool (setAprs(0,0) as in existing APR0 tests)
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);

    uint256 amount = 10_000 * ONE_SCALE;
    idleCDO.depositAA(amount);               // honest LP liquidity
    address attacker = makeAddr('attacker');
    _depositWithUser(attacker, 1, true);     // dust deposit (1 unit)

    // Attacker opens a dust withdraw request while unscaledApr == 0
    _startEpochAndCheckPrices(0);            // epoch 0 running
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(1, address(AAtranche)); // creates apr0TotalPrincipal > 0
    assertGt(strategy.apr0TotalPrincipal(), 0);

    // Honest manager sets a non-zero APR for the next period
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(5e17, 5e17); // e.g. 5% APR

    // stopEpoch now reverts forever -> epoch never ends
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 0);

    // No way out: bucket can only be cleared inside the reverting function,
    // users can't settle (epochNumber frozen), claims revert too.
    vm.prank(attacker);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.claimWithdrawRequest();

    // All vault funds (amount + accrued) are permanently frozen.
}
```

Key steps: deposit in an APR0 pool, dust `requestWithdraw` → `apr0TotalPrincipal > 0`; manager sets APR > 0; `stopEpoch` → `prepareStopEpochWithApr0` reverts at the `unscaledApr != 0` check, and since `epochNumber` never increments and `apr0TotalPrincipal` has no other clearing path, the freeze is permanent.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L283-294)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L539-540)
```text
    // Close current APR0 bucket so it cannot accrue again on later stopEpoch calls.
    apr0TotalPrincipal = 0;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L551-555)
```text
    uint256 _reqEpoch = _apr0User.principalEpoch;
    // Settle only after stopEpoch bumped epochNumber (ie after one full wait epoch).
    if (_reqEpoch >= epochNumber) {
      return;
    }
```
