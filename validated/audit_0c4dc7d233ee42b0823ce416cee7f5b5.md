### Title
APR0 withdraw receipts permanently poison `stopEpoch` once APR is raised — pending requests make every subsequent epoch settlement revert - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The external bug is a special-flow value (rollover bid price) being forced to a sentinel that later corrupts aggregate settlement (`_calculateClearingPrice` overflow/revert). The analog in idle-tranches is the APR=0 withdrawal flow: `IdleCreditVault.requestWithdraw` stores the requester's principal in the global `apr0TotalPrincipal` bucket, and `prepareStopEpochWithApr0` — invoked by `IdleCDOEpochVariant._stopEpoch` — hard-reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0` but the vault's `unscaledApr` is no longer zero. An unprivileged lender's APR0 receipt thus becomes a "poisoned entry" in the aggregate settlement path, exactly like the `type(uint256).max` rollover bid, and it makes every subsequent `stopEpoch`/`stopEpochWithDuration` revert while it remains uncleared. [1](#0-0) [2](#0-1) 

### Finding Description
`requestWithdraw` routes requests into the APR0 bucket when `unscaledApr == 0` at request time, incrementing `apr0TotalPrincipal` and the per-user `Apr0UserData.principal`/`principalEpoch` [3](#0-2) . `prepareStopEpochWithApr0` then reverts if that bucket is non-empty while `unscaledApr != 0` [4](#0-3) . There is no path that clears `apr0TotalPrincipal` outside `prepareStopEpochWithApr0` itself or default-epoch claim clearing (`_clearWithdrawClaimForEpoch`), so the revert is not self-healing: any single open APR0 receipt permanently bricks `stopEpoch` for as long as the APR stays non-zero [5](#0-4) . The receipt's "request lifecycle" is pinned to the APR at request time, but the check reads the *current* APR — the submitted-versus-stored-value mismatch mirrors the rollover bid price being overwritten.

Sequence:
1. Manager configures `unscaledApr == 0` (APR0 mode, `setAprs(0, 0)`).
2. Attacker (KYC-passed lender) calls `IdleCDOEpochVariant.requestWithdraw` → `IdleCreditVault.requestWithdraw` → `_requestWithdrawApr0`, recording `apr0TotalPrincipal += amount` with `principalEpoch = epochNumber`.
3. During the following epoch, manager (honest) raises APR via `setAprs`/`setAprsWithBuffer` — a routine parameter update.
4. `stopEpoch` → `prepareStopEpochWithApr0` → `apr0TotalPrincipal != 0 && unscaledApr != 0` → `revert NotAllowed()`. Every retry reverts identically; `stopEpochWithDuration` inherits the same revert via `_stopEpoch` [6](#0-5) .

### Impact Explanation
While the epoch cannot be stopped, `epochNumber` never advances, so `_settleApr0` skips settlement (`_reqEpoch >= epochNumber`) [7](#0-6)  and `_claimFundedWithdrawRequest` reverts on `epochNumber <= lastWithdrawRequest` [8](#0-7) . All pending withdraw receipts and new requests are frozen — the attacker's own small APR0 receipt freezes the entire pending-withdrawal queue plus blocks epoch rollover for all LPs. This is a temporary freeze of user funds with direct fund impact (whole pending-withdrawal basis plus inability to settle interest). Duration is unbounded from the user's side: recovery requires the honest manager to notice and return `unscaledApr` to 0 via `setAprs`, which defeats the intended APR change and can itself be re-triggered by the attacker for any future non-zero APR era as long as any stale APR0 principal survives.

### Likelihood Explanation
The attacker needs only a normal `requestWithdraw` while APR is 0 — a permissionless action for a KYC-passed lender, subject to `allowAAWithdrawRequest`/`allowBBWithdrawRequest` and `isWalletAllowed` [9](#0-8) . The second precondition — an APR raise while an APR0 receipt is pending — is an ordinary honest-manager action, not attacker-controlled, so the bug manifests organically on any APR0→fixed-APR transition with outstanding receipts rather than being purely adversarial. No existing guard stops it: `_checkTranche`, KYC, and the `lossRecoveryPriceByEpoch` claim-before-new-request guard all pass; `_ensureDefaultRecoveryInitialized` does not apply [10](#0-9) .

### Recommendation
Decouple the stored receipt's lifecycle from the current APR, mirroring "process rollover bids with their submitted prices": snapshot the APR (or a per-epoch APR0 flag/rate) at request time in `apr0Users`/`apr0RateByEpoch`, and have `prepareStopEpochWithApr0` settle a stale APR0 bucket at zero interest (or at the rate snapshotted for its request epoch) instead of reverting. At minimum, degrade gracefully — treat `apr0TotalPrincipal` as zero-interest principal and clear the bucket — so a settled-price mismatch can never block `stopEpoch` and freeze the receipt queue.

### Proof of Concept
Foundry fork test sketch (extend `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testApr0ReceiptFreezesStopEpochAfterAprRaise() external {
    // APR0 mode
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);
    vm.prank(owner);
    cdoEpoch.setIsAYSActive(false);

    uint256 amount = 10_000 * ONE_SCALE;
    idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(address(this), true);

    // epoch 0 runs at APR 0, ends normally
    _startEpochAndCheckPrices(0);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, cdoEpoch.expectedEpochInterest());
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    // buffer: attacker files an APR0 withdraw request
    uint256 receipt = cdoEpoch.requestWithdraw(
        IERC20(AAtranche).balanceOf(address(this)) / 2, address(AAtranche));
    assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

    _startEpochAndCheckPrices(1);

    // honest manager raises APR mid-epoch
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(5e18, 5e18);

    // epoch settlement permanently reverts -> receipts and NAV frozen
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, type(uint256).max);
    vm.prank(manager);
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.stopEpoch(0, 0);

    // claim path also blocked (epochNumber never advanced)
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.claimWithdrawRequest();
}
```

Caveats I could not fully verify within this pass: whether `IdleCDOEpochVariant._stopEpoch` wraps `prepareStopEpochWithApr0` inside the borrower-funding `try`/`catch` (if so, the revert may be caught and routed to `_handleBorrowerDefault` instead of propagating — which would convert the freeze into a spurious-default path with different but still harmful loss-accounting consequences), and whether any owner-side escape (`stopEpochWithDuration` variants, forced accounting) bypasses `prepareStopEpochWithApr0`. The PoC should be run to confirm which revert surface is hit.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L263-271)
```text
    if (
      lossRecoveryPrice != 0 &&
      (withdrawsRequestsByEpoch[_user][lossEpoch] != 0 ||
      (apr0Users[_user].principal != 0 && apr0Users[_user].principalEpoch == lossEpoch))
    ) {
      // A loss-adjusted receipt must be claimed before opening a later request, otherwise
      // `lastWithdrawRequest` would stop pointing to the epoch that stores its haircut.
      revert NotAllowed();
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L551-554)
```text
    uint256 _reqEpoch = _apr0User.principalEpoch;
    // Settle only after stopEpoch bumped epochNumber (ie after one full wait epoch).
    if (_reqEpoch >= epochNumber) {
      return;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L811-837)
```text
  function _clearWithdrawClaimForEpoch(address _user, uint256 _claimEpoch, bool _isClearingApr0) internal returns (uint256 claimBasis, uint256 burnAmount) {
    (claimBasis, burnAmount) = _withdrawClaimAmountsForEpoch(_user, _claimEpoch);
    if (claimBasis == 0) return (claimBasis, burnAmount);

    uint256 normalAmount = withdrawsRequestsByEpoch[_user][_claimEpoch];
    if (normalAmount != 0) {
      withdrawsRequestsByEpoch[_user][_claimEpoch] = 0;
      // The aggregate may also include older funded receipts; clear only this epoch's piece.
      withdrawsRequests[_user] -= normalAmount;
    }
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
    if (lastWithdrawRequest[_user] == _claimEpoch) {
      // The cleared epoch was the latest request marker. Any remaining normal/APR0 receipt
      // is older and already funded, so it can continue to the funded-claim path.
      lastWithdrawRequest[_user] = 0;
    }
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L520-529)
```text
  function stopEpochWithDuration(uint256 _newApr, uint256 _interest, uint256 _duration, uint256 _lossAmount) public {
    // stop epoch checks that msg.sender is allowed
    _stopEpoch(_newApr, _interest, _lossAmount);
    if (_interest != 1 && !defaulted) {
      // buffer period is not changed
      setEpochParams(_duration, bufferPeriod);
      // scale the apr with the new duration and buffer
      _setScaledApr(_newApr);
    }
    _afterStopEpochWithDuration();
```

**File:** contracts/IdleCDOEpochVariant.sol (L739-744)
```text
  function requestWithdraw(uint256 _amount, address _tranche) external returns (uint256 _underlyings) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == AATranche ? !allowAAWithdrawRequest : !allowBBWithdrawRequest) ||
      !isWalletAllowed(msg.sender)
    );
```
