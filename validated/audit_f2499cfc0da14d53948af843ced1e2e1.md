### Title
`depositDuringEpoch` and `requestWithdraw` ignore the `paused()` state, so the guardian/owner pause cannot actually prevent new trades - (File: contracts/IdleCDOEpochVariant.sol)

### Summary

The external report describes an `isPaused` flag that cannot prevent new trades because nothing enforces it. The strongest analog exists in `IdleCDOEpochVariant`: `IdleCDOCreditVault.pause()` sets the OpenZeppelin `_paused` flag, and ordinary deposits via `IdleCDO._deposit` are gated by `whenNotPaused` [1](#0-0) . However, the mid-epoch deposit path `depositDuringEpoch` never checks `paused()` — its gate only checks `skipDefaultCheck`, tranche flags, `isEpochRunning`, `epochEndDate`, and KYC [2](#0-1) . Likewise `requestWithdraw` only checks `allowAAWithdrawRequest`/`allowBBWithdrawRequest`, which a plain `pause()` does not clear [3](#0-2) . The pause mechanism therefore fails exactly as in the report: the "paused" state exists, is settable, but is not consulted on the active-epoch trading surface.

### Finding Description

`pause()` (callable by owner or guardian) only executes `_pause()` [4](#0-3) . During a running epoch the contract is already paused for ordinary deposits (`startEpoch` calls `_pause()` [5](#0-4) ), but `depositDuringEpoch` is intentionally the deposit channel that remains open — and it has no `whenNotPaused` modifier and no `paused()` check anywhere in its body [6](#0-5) .

Two consequences follow:

1. **Emergency pause during a running epoch is bypassable.** `emergencyShutdown()` does set `skipDefaultCheck`, which blocks `depositDuringEpoch` [7](#0-6) . But `restoreOperations()` deliberately clears `skipDefaultCheck` while leaving the epoch running and the contract paused, with the comment that only the dedicated `depositDuringEpoch` path is restored [8](#0-7) . If the guardian then calls `pause()` again to halt that channel (e.g., suspicious borrower activity, oracle/anomaly signal, Hypernative trigger), nothing enforces it: a KYC'd lender can keep calling `depositDuringEpoch`, mint tranche shares at the prorated-expected-price formula, and push new underlyings to the borrower, growing `expectedEpochInterest` obligations despite the pause [9](#0-8) .

2. **Pause during the buffer period does not stop exit-queue entries.** `pause()` does not reset `allowAAWithdrawRequest`/`allowBBWithdrawRequest`, and `requestWithdraw` never reads `paused()` [10](#0-9) . A guardian pausing to freeze activity still lets any allowed wallet burn tranche tokens and enqueue a fixed-price withdrawal receipt in `IdleCreditVault`, locking in a claim at the current `_tranchePrice` while the operator believes the book is frozen.

### Impact Explanation

The documented security control — "Pauses deposits" — is ineffective on the only deposit path that is live most of the time (mid-epoch deposits) and on withdrawal-request creation during buffer periods. An unprivileged KYC'd lender can:

- Open new positions during an active pause, minting shares priced on `expectedFinal` NAV and forwarding principal to the borrower, enlarging `expectedEpochInterest` that honest managers must now fund at `stopEpoch`. If the pause was triggered because of suspected over-commitment or deteriorating borrower conditions, each bypassed deposit increases the shortfall the pool must socialize (loss borne pro-rata by existing tranche holders).
- Queue withdrawal receipts during a pause meant to freeze exits, converting live-NAV tranche tokens into fixed `requestWithdraw` receipts that must be funded at epoch end regardless of subsequent events.

Loss is bounded by the deposit size and the incremental `expectedEpochInterest`, i.e., direct dilution/insolvency pressure on existing holders rather than an accounting error — the pause invariant itself ("no new trades while paused") is broken.

### Likelihood Explanation

Likelihood is moderate: it requires a running epoch (or buffer period for withdrawals) plus a guardian `pause()` without `emergencyShutdown`/`skipDefaultCheck`, and an attacker who is a KYC-passing allowed wallet (`isWalletAllowed`). Both conditions are realistic — guardians pause for monitoring/anomaly reasons distinct from full emergency shutdowns, and every legitimate lender is already in the allowed set. No privileged-role misbehavior is required.

### Recommendation

- Add `whenNotPaused` (or an explicit `paused()` check) to `depositDuringEpoch`, or have `pause()`/`_emergencyShutdown` semantics explicitly cover the mid-epoch channel (e.g., set `isDepositDuringEpochDisabled`/`skipDefaultCheck` consistently and document which flag is authoritative).
- Have `pause()` also disable `allowAAWithdrawRequest`/`allowBBWithdrawRequest` (with `restoreOperations`/`unpause` re-enabling them when no epoch is running), or gate `requestWithdraw` on `whenNotPaused`.
- Follow the OpenZeppelin Pausable convention uniformly across all user-facing entry points, as the external report recommends.

### Proof of Concept

Foundry fork test sketch (running epoch, non-programmable, non-AYS vault, mirroring `test/foundry/IdleCreditVault.t.sol` setup):

```solidity
function testPauseDoesNotStopDepositDuringEpoch() external {
    uint256 amount = 10_000 * ONE_SCALE;
    idleCDO.depositAA(amount);                       // seed pool (buffer phase)
    vm.startPrank(owner);
    cdoEpoch.setIsAYSActive(false);
    cdoEpoch.setIsDepositDuringEpochDisabled(false);
    vm.stopPrank();
    _startEpochAndCheckPrices(0);                    // epoch running, paused for normal deposits

    // Guardian pauses mid-epoch to stop new trades
    vm.prank(guardian);
    cdoEpoch.pause();
    assertTrue(cdoEpoch.paused());

    // Attacker: a KYC-allowed EOA still deposits mid-epoch
    address attacker = allowedLender;
    uint256 dep = 5_000 * ONE_SCALE;
    deal(defaultUnderlying, attacker, dep);
    vm.startPrank(attacker);
    underlying.approve(address(cdoEpoch), dep);
    uint256 minted = cdoEpoch.depositDuringEpoch(dep, address(AAtranche)); // succeeds: BUG
    vm.stopPrank();
    assertGt(minted, 0, "pause failed to block new trade");
}

function testPauseDoesNotStopRequestWithdraw() external {
    uint256 amount = 10_000 * ONE_SCALE;
    idleCDO.depositAA(amount);                       // buffer phase, requests allowed

    vm.prank(guardian);
    cdoEpoch.pause();                                // does NOT clear allowAAWithdrawRequest
    assertTrue(cdoEpoch.paused());
    assertTrue(cdoEpoch.allowAAWithdrawRequest());   // flag untouched

    vm.startPrank(allowedLender);
    cdoEpoch.requestWithdraw(0, address(AAtranche)); // succeeds: BUG — receipt locked in while paused
    vm.stopPrank();
}
```

Note: `_updateAccounting` inside `requestWithdraw` and `depositDuringEpoch` can still crystallize a pending loss, which mitigates stale-price exploitation but does not restore the pause invariant — the trades still execute while `paused() == true`. If the intended design is that `pause()` is meaningless mid-epoch (since `_pause` is already set by `startEpoch`), then the real gap is narrower: the guardian has *no* way to disable `depositDuringEpoch` short of `emergencyShutdown`, which conflates "stop new trades" with a full emergency state change (`skipDefaultCheck`, `allowBBWithdrawRequest = false`). Either way, the pause primitive does not deliver its documented guarantee on this surface.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L244-246)
```text
    isEpochRunning = true;
    // prevent deposits
    _pause();
```

**File:** contracts/IdleCDOEpochVariant.sol (L602-615)
```text
  function _emergencyShutdown(bool isAAWithdrawAllowed) internal override {
    // prevent deposits
    if (!paused()) {
      _pause();
    }
    // Preserve AA requests only if they were already open. This keeps a forced mid-epoch loss or
    // prior emergency from reopening them, while a normal explicit-loss stop can leave them open.
    if (!isAAWithdrawAllowed) {
      allowAAWithdrawRequest = false;
    }
    allowBBWithdrawRequest = false;
    // Persist the emergency state and let authorized forced accounting crystallize the loss.
    skipDefaultCheck = true;
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L619-632)
```text
  function restoreOperations() external override {
    _checkOnlyOwner();
    // Check if the pool was defaulted
    _checkNotAllowed(defaulted || priceAA == 0);
    skipDefaultCheck = false;
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

**File:** contracts/IdleCDOEpochVariant.sol (L644-650)
```text
  function _deposit(uint256 _amount, address _tranche) internal override whenNotPaused returns (uint256) {
    _checkNotAllowed(!isWalletAllowed(msg.sender));
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();
    // do the inherited deposit flow
    return super._deposit(_amount, _tranche);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L656-733)
```text
  function depositDuringEpoch(uint256 _amount, address _tranche) external virtual returns (uint256 _minted) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == BBTranche && !isBBDepositEnabled) ||
      isDepositDuringEpochDisabled ||
      skipDefaultCheck ||
      // programmable borrowers use APR=0 so mid-epoch deposits would dilute existing depositors
      isProgrammableBorrower ||
      // check if AYS is active as we don't support deposits during epoch in that case
      isAYSActive ||
      // check if epoch is still running even if not manually stopped yet
      !isEpochRunning || block.timestamp >= epochEndDate ||
      !isWalletAllowed(msg.sender)
    );

    if (_amount == 0) {
      return _minted;
    }

    uint256 _trancheTotSupply = _trancheSupply(_tranche);
    // Avoid pricing discontinuities for the first mid-epoch deposit in a tranche
    _checkNotAllowed(_trancheTotSupply == 0);

    _skimDonatedAssets();
    // Check that limit is not exceeded after removing skimmable raw donations.
    _guarded(_amount);
    _updateAccounting();

    // Get underlyings from user
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);

    // interest for the remaining epoch plus the full buffer period
    // NOTE: _calcInterest already gives full‑epoch + full‑buffer interest for the whole epoch
    //   So when a user joins mid‑epoch, we take the fraction of that full‑period interest 
    //   that matches the time they actually remain plus the entire buffer
    uint256 buffer = bufferPeriod;
    uint256 remaining = epochEndDate - block.timestamp;
    uint256 interest = _calcInterest(_amount) *
      // the time the depositor actually participates (remaining epoch + full buffer)
      (remaining + buffer) /
      // the total time baked into the scaled APR (epoch + buffer).
      (epochDuration + buffer);

    uint256 expectedInt = expectedEpochInterest;
    uint256 pendingFees = pendingWithdrawFees;
    uint256 trancheExpected;
    // existing holders' share of net expected interest for the epoch (pre-deposit)
    // (exclude pendingWithdrawFees since they go to fee receivers, not tranche holders)
    if (expectedInt > pendingFees) {
      trancheExpected = _calcTrancheInterestShare(
        _netGainAfterFees(expectedInt - pendingFees, _calculateManagementFee(lastNAVAA + lastNAVBB, remaining)),
        _tranche
      );
    }
    // interest this deposit will earn for the tranche over the remaining time (net of fees)
    uint256 trancheInterest = _calcTrancheInterestShare(
      _netGainAfterFees(interest, _calculateManagementFee(_amount, remaining)),
      _tranche
    );
    // pre-deposit expected final NAV for existing holders.
    // This won't ever be zero as we checked _trancheTotSupply and we seed initial NAV at tranche creation
    uint256 expectedFinal = _lastSavedNAV(_tranche) + trancheExpected;

    // mint at a discounted price so depositor gets principal + its prorated interest at epoch end
    // A mid‑epoch depositor should get _amount + trancheInterest at epoch end.
    // So they need minted = (amount + trancheInterest) / priceEnd.
    // priceEnd = expectedFinal / _trancheTotSupply
    // so minted = (amount + trancheInterest) * _trancheTotSupply / expectedFinal
    _minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal;
    _mintShares(_tranche, msg.sender, _minted, _amount);

    // update expected epoch interest
    expectedEpochInterest += interest;
    // mint strategy tokens to this contract
    IdleCreditVault(strategy).mintStrategyTokens(_amount);
    // transfer underlyings to the borrower
    _transferUnderlyings(_borrower(), _amount);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L739-790)
```text
  function requestWithdraw(uint256 _amount, address _tranche) external returns (uint256 _underlyings) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == AATranche ? !allowAAWithdrawRequest : !allowBBWithdrawRequest) ||
      !isWalletAllowed(msg.sender)
    );
  
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();

    // we trigger an update accounting to check for eventual losses
    _updateAccounting();

    IdleCreditVault creditVault = IdleCreditVault(strategy);
    if (_amount == 0) {
      _amount = _userTrancheBal(msg.sender, _tranche);
    }
    _underlyings = _trancheToUnderlyings(_amount, _tranche);

    // Programmable borrower deployments do not support instant withdrawals.
    // If apr decresed wrt last epoch, request instant withdraw and burn tranche tokens directly
    // we compare unscaled aprs
    if (_isInstantWithdrawEnabled()) {
      uint256 currentApr = creditVault.unscaledApr();
      if (lastEpochApr > (currentApr + instantWithdrawAprDelta)) {
        // burn strategy tokens from cdo and mint an equal amount to msg.sender as receipt
        creditVault.requestInstantWithdraw(_underlyings, msg.sender);
        // burn tranche tokens and decrease NAV
        _withdrawOps(_amount, _underlyings, _tranche);
        return _underlyings;
      }
    }

    uint256 principal = _underlyings;
    (uint256 interest, int256 diff) = _calcInterestWithdrawRequest(_underlyings, _tranche);
    uint256 totalFees = _totalWithdrawFees(principal, interest);
    // user is requesting principal + interest minus upfront management fee and net performance fee
    _underlyings = principal + interest - totalFees;
    // add expected fees to pending withdraw fees counter
    pendingWithdrawFees += totalFees;

    /// if there is an AA withdrawal the overperformance that the amount withdrawed would have generated for BB tranches
    /// is saved in interestForOverUnderPerformance. This is used to calculate the interest that should be added to the
    /// expectedEpochInterest at the startEpoch.
    /// If there is a BB withdrawal this amount is subtracted from the expectedEpochInterest
    interestForOverUnderPerformance += diff;

    // The receipt is fixed now and leaves live NAV. Charge management fees upfront
    // for the time it waits outside live NAV: remaining buffer plus the next epoch.
    creditVault.requestWithdraw(_underlyings, msg.sender, principal);
    // burn tranche tokens and decrease NAV without interest for the next epoch as it was not yet counted in NAV
    _withdrawOps(_amount, principal, _tranche);
```

**File:** contracts/IdleCDOCreditVault.sol (L518-521)
```text
  function pause() external  {
    _checkOnlyOwnerOrGuardian();
    _pause();
  }
```
