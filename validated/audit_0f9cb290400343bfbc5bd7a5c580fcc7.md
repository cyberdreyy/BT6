### Title
Unset `idleCDO` disables the caller check in `setApr`, letting any EOA inflate the vault APR to `maxApr` before the CDO is wired - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.setApr` enforces `msg.sender == idleCDO || msg.sender == manager` only when `idleCDO != address(0)`. This is the direct analog of CVE-2016-9386: a "NULL segment" (the unset `idleCDO`) is treated as usable, so the access-control guard is skipped entirely. Between `initialize` and `setWhitelistedCDO`, any unprivileged caller can set `lastApr` up to `maxApr` (20e18, i.e. 2000% scaled) and `unscaledApr` arbitrarily via `setApr`/`setAprs`/`setAprsWithBuffer`, because `unscaledApr` is assigned *before* the guarded `setApr` is even reached.

### Finding Description
```solidity
// contracts/strategies/idle/IdleCreditVault.sol:225
function setApr(uint256 _apr) public {
    address _cdo = idleCDO;
    // if cdo is not yet set we skip the check
    if (_cdo != address(0)) {
        if (msg.sender != _cdo && msg.sender != manager) revert NotAllowed();
    }
    if (_maxApr != 0 && _apr > _maxApr) revert NotAllowed();
    lastApr = _apr;
}
``` [1](#0-0) 

`setAprs` writes `unscaledApr` unconditionally on line 207 before delegating to the same bypassable `setApr`, and `setAprsWithBuffer` does the same on line 218. [2](#0-1) 

`lastApr` is exposed via `getApr()` and consumed by `IdleCDOEpochVariant.expectedEpochInterest` / the borrower's `totalInterestDueNow` path to determine the interest the borrower must fund at `stopEpoch`. `unscaledApr == 0` is the discriminator for the entire APR0 withdraw-request flow (`requestWithdraw` line 285, `prepareStopEpochWithApr0` line 506); corrupting it silently misroutes withdraw accounting. [3](#0-2) [4](#0-3) 

`idleCDO` starts as `address(0)` at `initialize` and is only set by the owner calling `setWhitelistedCDO`. Unless deployment wiring is atomic, the vault sits in a state where the "unusable" guard operand is accepted and the only remaining bound is `maxApr`. [5](#0-4) 

### Impact Explanation
- An attacker who is also a KYC-passing lender sets `lastApr = maxApr` (20e18). When the epoch stops, `expectedEpochInterest` is computed at the inflated APR; in programmable-borrower mode the repayment is pulled from the borrower ERC4626 vault, so the attacker extracts inflated yield paid by honest vault depositors — direct theft quantified as `(maxApr − realApr) × TVL × epochDuration/YEAR`.
- If the borrower cannot fund the inflated obligation, the epoch defaults and the loss is socialized through `finalizeDefaultRecovery`, crystallizing insolvency up to the full active basis.
- Setting `unscaledApr` nonzero breaks the APR0 settle path (`prepareStopEpochWithApr0` reverts at line 507 once `apr0TotalPrincipal != 0`), permanently freezing APR0 withdraw receipts — a separate fund-freeze impact.

### Likelihood Explanation
Exploitation requires the pre-`setWhitelistedCDO` window to exist on a live deployment (non-atomic wiring, upgrade redeploy, or a newly initialized vault). Within that window the attack is permissionless and requires no capital beyond a lender position to profit from the inflated APR. `maxApr` caps but does not prevent the inflation (20e18 is far above any legitimate APR). The guard code itself is trivially triggerable — one call.

### Recommendation
Always enforce the caller check; revert with `NotAllowed()` when `idleCDO == address(0)` and `msg.sender != manager`, or restrict the unset-CDO path to `onlyOwner`. Apply the same check ordering inside `setAprs`/`setAprsWithBuffer` so `unscaledApr` is not written before authorization.

### Proof of Concept
```solidity
// test/foundry/SetAprNullCdo.t.sol — fork mainnet, deploy IdleCreditVault proxy
function test_anyoneSetsAprBeforeCdoWired() public {
    IdleCreditVault impl = new IdleCreditVault();
    ERC1967Proxy proxy = new ERC1967Proxy(address(impl), "");
    IdleCreditVault vault = IdleCreditVault(address(proxy));
    vault.initialize(address(usdc), owner, manager, borrower, "BORR", 5e16);

    // owner has NOT called setWhitelistedCDO yet — idleCDO == address(0)
    address attacker = makeAddr("attacker");
    vm.prank(attacker);
    vault.setApr(20e18); // succeeds: check skipped, only maxApr bound applies
    assertEq(vault.getApr(), 20e18);

    vm.prank(attacker);
    vault.setAprs(1e18, 20e18); // unscaledApr corrupted too, APR0 flow broken
    assertEq(vault.unscaledApr(), 1e18);
}
```
Expected: both calls succeed today; after the fix they must revert `NotAllowed()`. A full fork variant would then run `startEpoch`/`stopEpoch` against a funded `ProgrammableBorrower` vault and assert the attacker's tranche redemption includes the inflated interest.

Caveat: I could not fully verify the exact `expectedEpochInterest`/`totalInterestDueNow` arithmetic (grep hit counts only), so the precise interest-inflation multiplier should be confirmed in `IdleCDOEpochVariant.sol`/`ProgrammableBorrower.sol` during PoC development.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L179-181)
```text
  function getApr() external view returns (uint256) {
    return lastApr;
  }
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L505-508)
```text
    // APR0 principal is only valid while APR is 0 for that request lifecycle.
    if (unscaledApr != 0) {
      revert NotAllowed();
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L954-957)
```text
  function setWhitelistedCDO(address _cdo) external onlyOwner {
    require(_cdo != address(0), "IS_0");
    idleCDO = _cdo;
  }
```
