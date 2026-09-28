### Title
`IdleCreditVault::setAprs`/`setApr` update `unscaledApr` without accounting for already-scheduled APR0 withdraw receipts, making `stopEpoch` revert and freezing the vault — ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary

In the Nouns bug, `setReservedUntilTokenId` changes `reservedUntilTokenId` without updating the `tokenRecipient[]` assignments derived from it, leaving founders' assigned tokens unclaimable. The analog in this repo is `IdleCreditVault.setAprs`/`setApr`/`setAprsWithBuffer`, which update `unscaledApr` while outstanding APR0 withdraw receipts (`apr0Users` / `apr0TotalPrincipal`) were scheduled under the `unscaledApr == 0` assumption. `prepareStopEpochWithApr0` then hard-reverts `NotAllowed` whenever `unscaledApr != 0` and `apr0TotalPrincipal > 0`, so any honest manager/operator APR update during an epoch containing even a dust-sized APR0 withdraw request bricks `stopEpoch` until the APR is set back to zero.

### Finding Description

`requestWithdraw` routes users into the APR0 accounting bucket whenever `unscaledApr == 0` at request time: [1](#0-0) 

The request is recorded in `apr0Users[_user]` and the global `apr0TotalPrincipal` bucket: [2](#0-1) 

The APR setters update `unscaledApr` unconditionally, with no check for open APR0 receipts and no migration of the scheduled bucket: [3](#0-2) 

At epoch end, `prepareStopEpochWithApr0` (called by the CDO during `stopEpoch`/`stopEpochWithDuration`) reverts if `unscaledApr != 0` while the bucket is non-zero: [4](#0-3) 

Just like `setReservedUntilTokenId` leaves `tokenRecipient[]` pointing at stale IDs, `setAprs` leaves `apr0Users`/`apr0TotalPrincipal` scheduled under the old APR regime, and the epoch state machine has no path to reconcile them. The orchestrator exposes this exact call as a routine operator action via `setStrategyAprsRaw`: [5](#0-4) 

### Impact Explanation

- **Temporary freezing of all vault funds.** Once `unscaledApr` is non-zero while any APR0 receipt is open, every `stopEpoch`/`stopEpochWithDuration` call reverts inside `prepareStopEpochWithApr0`. The epoch cannot end, `epochNumber` cannot advance, and no withdraw request (normal, APR0, or instant) can be claimed — the funded-claim path requires `epochNumber > lastWithdrawRequest[_user]` [6](#0-5) . All user principal and interest stay locked until the operator reverts the APR back to 0.
- **Perpetual APR0 griefing.** `apr0TotalPrincipal` is only cleared at a successful stop (line 540), and `_settleApr0` only runs lazily when the requester claims or re-requests [7](#0-6) . An unprivileged attacker holding a dust APR0 receipt can re-request each epoch, forcing the vault to keep `unscaledApr == 0` for every epoch in which their receipt is open — i.e., the vault can never charge a nonzero APR without first freezing itself. Cost to the attacker is a minimum-denomination deposit; the impact applies to the entire TVL.

### Likelihood Explanation

- The attacker needs only a KYC-passing deposit and one dust `requestWithdraw` while `unscaledApr == 0` — no privileged role.
- Triggering requires a routine, honest privileged action (operator/manager setting a nonzero APR via `setAprs` or `Orchestrator.setStrategyAprsRaw`), which is a normal part of operating a credit vault when rates change.
- No existing guard prevents it: `setApr` checks only `msg.sender` and `maxApr` (lines 229–233), not `apr0TotalPrincipal`; `prepareStopEpochWithApr0` reverts rather than settling, and there is no admin function to evict an APR0 receipt.

### Recommendation

Apply the same fix pattern as the Nouns recommendation — reconcile scheduled entitlements inside the setter, or forbid the change while entitlements are open:

- In `setAprs`/`setAprsWithBuffer`/`setApr`, revert when `apr0TotalPrincipal != 0` (or when any `apr0Users` principal is open), forcing operators to only change APR at epoch boundaries after `prepareStopEpochWithApr0` has closed the bucket; or
- Convert open APR0 receipts into normal `withdrawsRequestsByEpoch` receipts at the moment `unscaledApr` transitions away from 0, so `stopEpoch` never depends on the request-time APR.

Add a regression test: deposit at APR0 → `requestWithdraw` → manager `setAprs(1e18, …)` → `stopEpoch` must not revert (or the setter itself must revert cleanly instead of bricking the epoch).

### Proof of Concept

Conceptual Foundry fork PoC against `test/foundry/IdleCreditVault.t.sol` harness (mirroring `testMaxWithdrawable`/`testClaimInstantWithdrawRequest` setup with `unscaledApr = 0`):

```solidity
function test_AprUpdateWithOpenApr0RequestBricksStopEpoch() public {
    // 1. Vault configured with unscaledApr == 0; manager deposits/starts epoch as usual.
    // 2. Attacker (KYC'd lender) deposits dust and requests withdraw while APR is 0.
    uint256 dust = 1; // 1 wei of underlying
    address attacker = makeAddr('attacker');
    _depositWithUser(attacker, dust);
    vm.prank(manager);
    cdoEpoch.startEpoch();
    _requestWithdrawWithUser(attacker, dust); // routed into apr0Users via _requestWithdrawApr0
    assertGt(strategy.apr0TotalPrincipal(), 0);

    // 3. Honest operator raises APR for the vault mid-epoch (legitimate rate change).
    vm.prank(manager); // or orchestrator.setStrategyAprsRaw
    IdleCreditVault(address(strategy)).setAprs(5e18, 5e18);

    // 4. Epoch end: every stopEpoch attempt reverts inside prepareStopEpochWithApr0
    //    -> pendingWithdraws never funded, epochNumber never bumped, all claims frozen.
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.expectRevert(NotAllowed.selector);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0); // reverts: unscaledApr != 0 && apr0TotalPrincipal != 0

    // 5. Recovery requires operator to set APR back to 0; attacker re-requests each epoch
    //    to keep the vault pinned at APR0 indefinitely.
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0); // now succeeds
}
```

**Uncertainty note:** I could not fully read `IdleCDOEpochVariant.stopEpoch` to confirm `prepareStopEpochWithApr0` is invoked unconditionally on every stop path (grep showed 6 call sites in that file, consistent with it being called in `stopEpoch`/`stopEpochWithDuration`). If the CDO only calls it when APR0 accounting is expected, the freeze applies to those paths; the fix and PoC should verify the exact invocation condition.

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

**File:** contracts/IdleCreditVaultManagerOrchestrator.sol (L121-124)
```text
  function setStrategyAprsRaw(address _cdo, uint256 _unscaledApr, uint256 _apr) external {
    _checkOnlyOperator();
    IdleCreditVault(_creditVault(_cdo).strategy()).setAprs(_unscaledApr, _apr);
  }
```
