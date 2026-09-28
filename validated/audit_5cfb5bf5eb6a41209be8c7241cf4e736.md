### Title
Unauthenticated APR setter during the `idleCDO == address(0)` setup window lets any EOA fix the vault interest rate - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external report describes a sensitive credential operation (retrieve/recreate/delete an API key) being reachable without reauthentication — a standing privileged action with no fresh authorization check. The closest analog in idle-tranches is `IdleCreditVault.setApr`/`setAprs`/`setAprsWithBuffer`, which intentionally skip the caller check whenever `idleCDO` is unset. During the deployment/configuration window (after `initialize` but before `setWhitelistedCDO`), any unprivileged EOA can write `lastApr`/`unscaledApr` — a privileged economic parameter that owner/manager later rely on as already-configured state, exactly like OctoPrint's API-key surface reachable without re-proving authority.

### Finding Description
`setApr` gates on `msg.sender != idleCDO && msg.sender != manager` only when `idleCDO != address(0)`:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:225-235
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

`setAprs` and `setAprsWithBuffer` write `unscaledApr` through the same path (`IdleCreditVault.sol:206-220`). `idleCDO` is only set later via `setWhitelistedCDO` (`IdleCreditVault.sol:954-957`), so there is a window — front-running the configuration transaction, or any upgraded/idle vault where `idleCDO` is zero — in which the APR set by an attacker persists as trusted state. The only bound is `maxApr` (default `DEFAULT_MAX_APR = 20e18`, `IdleCreditVault.sol:89-90,143`).

Once `idleCDO` is wired, the attacker-set `lastApr`/`unscaledApr` feed `getApr()` and `expectedEpochInterest`, which is the accounting basis used in `stopEpoch` (`IdleCDOEpochVariant.sol:357-388`), `prepareStopEpochWithApr0` (`IdleCreditVault.sol:490-541`), and `finalizeDefaultRecovery` (`IdleCreditVault.sol:661-710`). An APR of 20e18 (20%/yr scaled over epoch+buffer) inflates `expectedEpochInterest`: the borrower is expected to repay far more than agreed, `getFundsFromBorrower` fails, and `_handleBorrowerDefault` is triggered (`IdleCDOEpochVariant.sol:398-404, 504`) — converting a forged parameter into a real borrower default with BB-first loss socialization. Conversely the attacker could set APR to near-zero to silently strip lender yield expectations for the epoch, or craft an intermediate value to distort `apr0RateByEpoch`/fee splits.

### Impact Explanation
Direct fund impact: a forced borrower default caused by an inflated interest liability makes the CDO pull more than the borrower agreed; failure routes into `_handleBorrowerDefault`, and eventual `finalizeDefaultRecovery` applies a recovery haircut (`defaultRecoveryPrice < RECOVERY_FULL`) to all active tranche holders and pending receipts — i.e., permanent loss of principal proportional to the shortfall between real borrower repayment capacity and the forged APR-implied liability. Alternatively, a zeroed APR permanently steals one epoch of lender yield (theft of unclaimed yield). Loss magnitude is bounded by `maxApr` but defaults to 20%/yr on the full TVL.

### Likelihood Explanation
Exploitation requires a deployed (or upgrade-reset) vault sitting with `idleCDO == address(0)` while holding or about to hold funds — a configuration race rather than steady state. The attacker needs only to be an unprivileged EOA monitoring deployments; no stolen session or privileged key is needed because the auth check is skipped entirely. Likelihood is moderate-low: the window is short for correct deployments, but the guard explicitly exists "only during the setup," so any vault paused mid-configuration or upgraded without re-setting `idleCDO` is exposed, and the forged value persists indefinitely once stored.

### Recommendation
Remove the `idleCDO == address(0)` bypass and gate `setApr` on `msg.sender == manager || msg.sender == owner()` when no CDO is set, or add a one-time `initialize`-time APR lock so post-initialization APR writes always require authentication. Re-derive `unscaledApr`/`lastApr` atomically in the same transaction that calls `setWhitelistedCDO`.

### Proof of Concept
Foundry fork PoC sketch:

```solidity
// test/foundry/PoCSetAprNoAuth.t.sol
function test_AnyoneCanSetAprBeforeCdoWired() public {
  // Deploy a fresh IdleCreditVault implementation + proxy, call initialize(...)
  // but do NOT call setWhitelistedCDO yet (idleCDO == address(0)).
  IdleCreditVault vault = IdleCreditVault(address(proxy));
  vault.initialize(address(underlying), owner, manager, borrower, "B", 5e18);

  address attacker = address(0xbabe);
  uint256 forged = 20e18; // DEFAULT_MAX_APR cap

  vm.prank(attacker);
  vault.setAprs(forged, forged); // or setApr(forged)

  assertEq(vault.lastApr(), forged);
  assertEq(vault.unscaledApr(), forged);

  // Owner wires the CDO; forged APR is now the epoch interest basis.
  vm.prank(owner);
  vault.setWhitelistedCDO(address(idleCDO));

  // Start epoch normally; at stopEpoch the borrower's repayment obligation
  // is computed from forged APR -> getFundsFromBorrower fails ->
  // _handleBorrowerDefault triggers; finalizeDefaultRecovery then
  // crystallizes a haircut on AA/BB holders and pending receipts.
}
```

Caveat: I could not fully verify whether production deployments always call `setWhitelistedCDO` atomically with `initialize` (e.g., via `IdleCreditVaultFactory`); if the factory does so in one transaction, the exploitable window narrows to upgrade/reset paths where `idleCDO` is zeroed, which still leaves the missing auth check as a latent defect worth fixing.