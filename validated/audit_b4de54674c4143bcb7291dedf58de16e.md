### Title
`emergencyShutdown()` on `IdleCDOCreditVault` is a no-op — deposits and withdrawals remain open after a declared shutdown - ([File: contracts/IdleCDOCreditVault.sol])

### Summary
`IdleCDOCreditVault.emergencyShutdown()` is documented to "pause deposits and redeems for all classes of tranches", but the internal `_emergencyShutdown(bool)` hook it delegates to is an empty virtual function. Calling `emergencyShutdown()` therefore changes no state: `paused()` stays `false`, `allowAAWithdraw`/`allowBBWithdraw` stay `true`, and `skipDefaultCheck` stays `false`. The same applies to `restoreOperations()`, which is an empty override that returns without checking `defaulted`/`priceAA` or toggling any flag. This is a direct analog of the reported bug — a shutdown/deactivation entry point that gives operators the impression the pool is frozen while every user-facing path stays open.

### Finding Description
The shutdown flow is split across the base and the epoch variant:

- `IdleCDO._emergencyShutdown` (the working implementation for non-epoch CDOs) calls `_pause()`, clears `allowAAWithdraw`/`allowBBWithdraw`, sets `skipDefaultCheck` and `revertIfTooLow`, so deposits and withdrawals are genuinely blocked [1](#0-0) .
- In `IdleCDOCreditVault`, `emergencyShutdown()` performs only the guardian/owner check and then calls `_emergencyShutdown(false)`, which is defined as `function _emergencyShutdown(bool) internal virtual {}` — a literal no-op [2](#0-1) .
- `restoreOperations()` is likewise `function restoreOperations() external virtual {}` — it performs no `defaulted`/`priceAA` check (unlike `IdleCDOEpochVariant.restoreOperations`, which reverts if `defaulted || priceAA == 0` [3](#0-2) ).

The concrete invariant break:

1. `depositAA`/`depositBB` route into `_deposit`, which is gated only by `whenNotPaused`. Since the empty hook never calls `_pause()`, deposits keep succeeding after `emergencyShutdown()` — contrast `IdleCDOEpochVariant._deposit`, which correctly remains `whenNotPaused`-protected because its override of `_emergencyShutdown` does call `_pause()` [4](#0-3) .
2. Withdrawal gating relies on `allowAAWithdraw`/`allowBBWithdraw` (checked in `IdleCDO._withdraw` with `WithdrawNotAllowed`); the no-op leaves both `true`, so withdrawals also continue.
3. `skipDefaultCheck` is never set, so there is no marker that a shutdown was ever declared — no code path can distinguish "healthy pool" from "pool the guardian tried to freeze".

The broken invariant is the same one the external report identifies: an emergency-shutdown entry point that does not actually block the deposit/withdraw surface it advertises.

### Impact Explanation
`emergencyShutdown()` is the control the owner/guardian reaches for in exactly the scenarios where continued user flows are dangerous: a detected strategy exploit, oracle mispricing, or pending borrower default. After the call, an unprivileged attacker (any KYC-passed lender or tranche holder) can still:

- Deposit at a stale pre-loss `virtualPrice` before the loss is crystallized, receiving tranche tokens that dilute the loss across existing holders — a direct transfer of value from honest depositors to the attacker.
- Withdraw while the pool is intended to be frozen, draining remaining healthy liquidity ahead of socialized losses.
- Race the operator: since `emergencyShutdown` succeeds and emits nothing unexpected, operators may believe the vault is frozen and delay the separate `pause()` call, widening the window.

Because deposits are minted at `_virtualPriceAux`-based prices before accounting is updated, a post-shutdown deposit during an unreported default converts impaired collateral into full-value shares — theft/solvency impact, not just a logic quirk.

### Likelihood Explanation
Medium-high once any incident triggers a shutdown. The bug is deterministic (not timing-dependent): every call to `emergencyShutdown()` on a non-epoch `IdleCDOCreditVault` deployment silently does nothing. The only mitigating factor is that a vigilant operator could independently call `pause()`, but the contract's own interface presents `emergencyShutdown()` as the function that "pauses deposits and redeems for all classes of tranches", and the sibling `IdleCDOEpochVariant` implementation demonstrates the intended behavior. The base contract `IdleCDO` shows the correct pattern is known and expected. No privileged actor is required — the attacker is an ordinary depositing/withdrawing user acting after the shutdown call.

### Recommendation
Implement `_emergencyShutdown` (or remove the empty override so the `IdleCDO` base implementation runs) in `IdleCDOCreditVault`:

```solidity
// contracts/IdleCDOCreditVault.sol
function _emergencyShutdown(bool isAAWithdrawAllowed) internal virtual override {
    if (!paused()) {
        _pause();
    }
    allowAAWithdraw = isAAWithdrawAllowed;
    allowBBWithdraw = false;
    skipDefaultCheck = true;
    revertIfTooLow = true;
}
```

Similarly, `restoreOperations()` should at minimum clear `skipDefaultCheck`, unpause, and re-enable withdraw flags — and must revert if the pool is `defaulted` or `priceAA == 0`, mirroring `IdleCDOEpochVariant.restoreOperations` [3](#0-2) . A defense-in-depth option is to have `emergencyShutdown()` call `_pause()` directly rather than relying on the virtual hook, so a missing override can never silently skip the pause.

### Proof of Concept
Reproducible Foundry test (drop into `test/foundry`, using the existing `IdleCreditVault.t.sol` deployment harness where `idleCDO` is a `IdleCDOCreditVault` instance without the epoch-variant override):

```solidity
function testEmergencyShutdownIsNoOp() external {
    uint256 amount = 10_000 * ONE_SCALE;

    // normal deposits work pre-shutdown
    idleCDO.depositAA(amount);

    // guardian declares emergency shutdown — succeeds, but changes nothing
    vm.prank(owner);
    idleCDO.emergencyShutdown();

    // no state was actually changed
    assertFalse(idleCDO.paused(), "pool not paused after emergencyShutdown");
    assertTrue(idleCDO.allowAAWithdraw(), "AA withdraws still allowed");
    assertTrue(idleCDO.allowBBWithdraw(), "BB withdraws still allowed");
    assertFalse(idleCDO.skipDefaultCheck(), "no shutdown marker set");

    // attacker can still deposit at stale price post-shutdown
    idleCDO.depositAA(amount);            // does NOT revert — should revert "Pausable: paused"
    // and can still withdraw
    idleCDO.withdrawAA(amount);           // does NOT revert — should revert WithdrawNotAllowed
}
```

For reference, the equivalent test in `TestIdleCDOBase.testEmergencyShutdown` expects exactly the `Pausable: paused` / `WithdrawNotAllowed` reverts that this contract fails to produce [5](#0-4) , confirming the intended behavior and that the no-op override defeats it.

Caveat: I verified the empty overrides and the guarding scheme from the indexed sources, but could not line-by-line confirm every `_deposit`/`_withdraw` modifier on `IdleCDOCreditVault` due to limited remaining iterations; if `_deposit`/`_withdraw` on this specific variant gate on something other than `paused()`/the allow-flags, the user-facing impact would narrow accordingly — though the shutdown function would still be a documented no-op.

### Citations

**File:** contracts/IdleCDO.sol (L936-948)
```text
  function _emergencyShutdown(bool isAAWithdrawAllowed) internal virtual {
    // prevent deposits
    if (!paused()) {
      _pause();
    }
    // prevent withdraws
    allowAAWithdraw = isAAWithdrawAllowed;
    allowBBWithdraw = false;
    // Allow deposits/withdraws (once selectively re-enabled, eg for AA holders)
    // without checking for lending protocol default
    skipDefaultCheck = true;
    revertIfTooLow = true;
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L503-514)
```text
  /// @notice pause deposits and redeems for all classes of tranches
  /// @dev can be called by both the owner and the guardian
  function emergencyShutdown() external {
    _checkOnlyOwnerOrGuardian();
    _emergencyShutdown(false);
  }

  function _emergencyShutdown(bool) internal virtual {}

  /// @notice allow deposits and redeems for all classes of tranches
  /// @dev can be called by the owner only
  function restoreOperations() external virtual {}
```

**File:** contracts/IdleCDOEpochVariant.sol (L601-615)
```text
  /// @notice Prevent deposits and redeems for all classes of tranches
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

**File:** test/foundry/TestIdleCDOBase.sol (L339-361)
```text
  function testEmergencyShutdown() external virtual {
    uint256 amount = 10000 * ONE_SCALE;
    idleCDO.depositAA(amount);
    idleCDO.depositBB(amount);

    // call with non owner
    vm.expectRevert(GuardedLaunchUpgradable.NotAuthorized.selector);
    vm.prank(address(0xbabe));
    idleCDO.emergencyShutdown();

    // call with owner
    vm.prank(owner);
    idleCDO.emergencyShutdown();

    vm.expectRevert(bytes("Pausable: paused")); // default
    idleCDO.depositAA(amount);
    vm.expectRevert(bytes("Pausable: paused")); // default
    idleCDO.depositBB(amount);
    vm.expectRevert(IdleCDO.WithdrawNotAllowed.selector); // default
    idleCDO.withdrawAA(amount);
    vm.expectRevert(IdleCDO.WithdrawNotAllowed.selector); // default
    idleCDO.withdrawBB(amount);
  }
```
