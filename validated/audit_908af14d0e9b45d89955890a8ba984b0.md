### Title
Unprivileged ERC4626 vault user can freeze `stopEpoch` by starving the programmable borrower's withdrawal, indefinitely blocking lender redemptions - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
The external report describes a client that blindly merges unauthenticated remote payloads into security-relevant local state, bypassing configured settings. The closest idle-tranches analog is `ProgrammableBorrower.onStopEpoch`: `IdleCDOEpochVariant.stopEpoch` blindly merges unauthenticated external ERC4626 vault state (live liquidity / `convertToAssets`) into the epoch-settlement path with no local fallback. When the vault reports enough economic coverage (`_currentVaultAssets()` ≥ shortfall) but the actual `vault.withdraw()` reverts — e.g., because another vault user drained available cash or the vault's withdrawal queue is closed — the hook reverts with `StopEpochVaultLiquidityUnavailable` and the entire `stopEpoch` transaction fails. Any unprivileged user of the same ERC4626 vault (an allowed attacker class: vault depositor or direct token sender) can repeatedly induce this condition, permanently suspending epoch settlement while the CDO remains in "running" state and all lender withdraw requests stay frozen.

### Finding Description
`IdleCDOEpochVariant.stopEpoch` calls `IProgrammableBorrower(borrower).onStopEpoch(amountRequired, isRequestingAllFunds)` before pulling funds. In `ProgrammableBorrower.onStopEpoch` (lines 231–268):

```solidity
// contracts/strategies/idle/ProgrammableBorrower.sol:239-253
uint256 onHand = underlyingToken.balanceOf(address(this));
if (_amountRequired > onHand) {
  uint256 shortfall = _amountRequired - onHand;
  // If the vault shares do not economically cover the shortfall, let IdleCDO's later
  // transferFrom fail and use the existing default path.
  if (shortfall > _currentVaultAssets()) return true;
  try vault.withdraw(shortfall, address(this), address(this)) returns (uint256 shares) {
    ...
  } catch {
    revert StopEpochVaultLiquidityUnavailable();   // <-- bubbles up, aborts stopEpoch
  }
}
```

The "economically covered" branch (`shortfall <= _currentVaultAssets()`) hard-reverts on any vault withdrawal failure. `_currentVaultAssets()` is `vault.convertToAssets(balanceOf(this))` — a pure function of the attacker-influenceable vault share price — while `vault.withdraw` additionally depends on the vault's *free liquidity / withdrawal limits*, which any other vault shareholder controls by withdrawing first. The CDO never falls back to its local settings (e.g., partial stop, default accounting on shortfall); it treats the vault's transient withdrawal failure as fatal to the whole epoch transition.

The code even acknowledges the distinction — returning `true` when shares don't cover the shortfall so the default path runs — but provides no equivalent handling when shares do cover it yet liquidity is unavailable, even though that is precisely the state a third-party vault user can force cheaply and repeatedly.

### Impact Explanation
Temporary freezing of the entire pool's funds with quantified impact:

- While `stopEpoch` reverts, `isEpochRunning` stays `true`: no `requestWithdraw` fulfillment, no `claimWithdrawRequest`/`claimInstantWithdrawRequest` funding, no new `startEpoch`. All AA/BB lender principal and accrued interest is frozen for the duration of the griefing.
- Cost to the attacker is only gas plus temporarily holding a vault withdrawal position; the attack is repeatable every time the honest manager/keeper retries `stopEpoch` (the attacker can deposit back into the vault between attempts to keep `convertToAssets` coverage high so the revert path is always taken instead of the default path).
- Quantified loss: entire credit-vault TVL frozen per retry window; e.g., on a vault with P pending withdraw receipts and T total NAV, all T is unclaimable until the attacker stops. This is not a mere gas/DoS: it directly blocks settlement of matured withdraw receipts whose funds sit trapped in the strategy/vault.

### Likelihood Explanation
- Attacker needs only to be a depositor (or able to acquire shares) in the same ERC4626 vault the `ProgrammableBorrower` uses — explicitly an in-scope unprivileged actor. No privileged role, no KYC on Idle's side needed.
- Precondition: the deployment uses the programmable-borrower mode (`isProgrammableBorrower`, `isInterestMinted`, `disableInstantWithdraw`), which is a supported, actively deployed configuration per the factory tasks.
- The vault must have a withdrawal path whose success depends on liquidity/limits independent of `convertToAssets` — true of real credit/RWA-style ERC4626 wrappers (e.g., Metamorpho-style curated vaults with queued withdrawals) that this integration targets.
- Existing guards do not prevent it: `_checkOnlyIdleCDO` gates the caller, not the vault state; `nonReentrant` is irrelevant; the only alternative is the default path, which requires `shortfall > _currentVaultAssets()` — a state the attacker avoids by keeping share value high.

### Recommendation
Make `stopEpoch` robust to untrusted external vault state instead of reverting on withdrawal failure:

1. On `catch`, do not revert — treat the unfunded shortfall as a real shortfall: return `false` (or `true` with a shortfall signal) so `IdleCDOEpochVariant` applies its existing loss/default accounting (`stopEpochWithDuration` loss or default flow) rather than freezing the epoch.
2. Alternatively, allow partial settlement: pull whatever `onHand` is available and account the remainder as loss, matching the `collectWithdrawFunds` loss-recovery machinery.
3. At minimum, add an owner/manager escape hatch to force `stopEpoch` past the vault hook (e.g., a `forceStop` flag that skips `onStopEpoch`) so a stuck external vault cannot hold the epoch state machine hostage indefinitely.

### Proof of Concept
Reproducible Foundry fork PoC outline (programmable-borrower credit vault, e.g., a live deployment per `tasks/cdo-factory.js` with `programmableBorrowerConfig`):

```solidity
function test_VaultUserFreezesStopEpoch() public {
    // Setup: running epoch N, ProgrammableBorrower holds vault shares,
    // pendingWithdraw receipts exist; borrower principal is small/0.

    // 1) Attacker (any vault shareholder) withdraws near-all liquidity
    //    from the ERC4626 vault so vault.withdraw() will revert on the
    //    borrower's shortfall while convertToAssets still covers it.
    vm.prank(attacker);
    vault.redeem(vault.balanceOf(attacker), attacker, attacker);
    // Optionally attacker re-deposits dust/keeps price high so that
    // shortfall <= _currentVaultAssets() (covered-economically branch).

    // 2) Honest manager/keeper tries to stop the epoch -> revert.
    vm.prank(manager);
    vm.expectRevert(StopEpochVaultLiquidityUnavailable.selector);
    idleCDO.stopEpoch(0);

    // 3) Invariant broken: epoch stuck running -> all claims frozen.
    assertTrue(idleCDO.isEpochRunning());
    vm.prank(lender);
    vm.expectRevert(); // epoch not matured / request not funded
    idleCDO.claimWithdrawRequest();

    // 4) Repeatability: attacker loops withdraw/deposit around each
    //    stopEpoch retry to keep it failing indefinitely.
}
```

Uncertainties: the exact revert path inside `vault.withdraw` depends on the specific integrated ERC4626 vault's liquidity semantics (the PoC assumes a vault where withdrawal can fail despite positive `convertToAssets`, e.g., a queued/credit vault). If the targeted deployment's vault guarantees instant full liquidity, this reduces to a non-issue; that property is not enforced anywhere in `ProgrammableBorrower` or `IdleCDOEpochVariant`, so the fragility is in-repo regardless.