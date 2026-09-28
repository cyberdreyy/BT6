### Title
Unauthenticated APR manipulation during the setup window — `setApr`/`setAprs`/`setAprsWithBuffer` accept any caller while `idleCDO == 0` - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`IdleCreditVault.setApr` skips its only access check whenever `idleCDO` is unset, with the comment "this can happen only during the setup". Like `.setdistillerkeys` in Ghostscript — a command meant only for the startup phase but accepted during normal processing — these setters remain callable by any EOA in the window between vault initialization and `setWhitelistedCDO`, letting an attacker write arbitrary `unscaledApr`/`lastApr` state. [1](#0-0) 

### Finding Description
`setApr`, `setAprs`, and `setAprsWithBuffer` all funnel into `setApr`, whose authorization is conditional on `idleCDO != address(0)`:

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

`initialize` does not set `idleCDO`; it is wired later via `setWhitelistedCDO` (owner-only). Between `initialize` and `setWhitelistedCDO` — or on a vault whose CDO link is ever cleared/upgraded — any unprivileged caller can:

- Call `setAprsWithBuffer(x, 0, 0)`, which writes `unscaledApr = x` directly and then calls `setApr(x)`. `unscaledApr` is never compared to `maxApr`; only the scaled value is.
- If `maxApr == 0` (cap disabled, a supported configuration used in the test suite via `strategy.setMaxApr(0)`), even `lastApr` can be set to any value.
- Front-run the owner's `setWhitelistedCDO` transaction in the same block, so the poisoned APR is already in place when the CDO starts consuming it.

Once wired, `startEpoch` computes `expectedEpochInterest` from the current stored APR (`_calcInterest(getContractValue())`), and `stopEpoch` attempts to pull that amount from the borrower. `requestWithdraw` also branches on `unscaledApr == 0` to route requests into the APR0 bucket, so a poisoned `unscaledApr` changes receipt accounting (`_requestWithdrawApr0`, `prepareStopEpochWithApr0`, `apr0RateByEpoch`). [2](#0-1) [3](#0-2) 

### Impact Explanation
An attacker who sets an inflated APR before the CDO is linked causes the vault to book interest the borrower never agreed to:

- Inflated `lastApr` → inflated `expectedEpochInterest` → `stopEpoch` pulls (or demands) more from the honest borrower than owed. If the borrower cannot/won't pay the fabricated amount, the `getFundsFromBorrower` try/catch path triggers `_handleBorrowerDefault`, forcing a spurious default and freezing/halting the pool with real loss socialization to follow.
- If the borrower does honor the inflated pull (borrower contract auto-repays), the attacker — holding tranche positions or APR0 receipts — claims the excess as yield, i.e., direct theft from the borrower.
- Setting `unscaledApr` to a nonzero value while the pool was configured for APR0 (or vice versa) corrupts the `apr0Users`/`apr0TotalPrincipal` accounting and can make `prepareStopEpochWithApr0` revert (`unscaledApr != 0` → `NotAllowed`), permanently bricking `stopEpoch` while APR0 principal is outstanding — a freezing-of-funds condition until the owner intervenes, and owner cannot change `unscaledApr` without `setAprs` access (which only CDO/manager have once `idleCDO` is set; owner is neither).

### Likelihood Explanation
The window is narrow — it exists only while `idleCDO == address(0)` — but it is a real deployment phase: `initialize` never sets `idleCDO`, so every vault passes through this state, and `setWhitelistedCDO` is a separate owner transaction an attacker can front-run. No privilege is needed; the only mitigations are deployment ordering discipline and a nonzero `maxApr` (which still leaves `unscaledApr` uncapped and does not protect APR0 bookkeeping). Likelihood is low-to-medium; impact when hit is high (forced default / stolen yield / frozen stop).

### Recommendation
Add an explicit authorization check that does not depend on `idleCDO` being set, e.g. allow only `owner()`/`manager` before the CDO is wired:

```solidity
function setApr(uint256 _apr) public {
    address _cdo = idleCDO;
    if (_cdo != address(0)) {
      if (msg.sender != _cdo && msg.sender != manager) revert NotAllowed();
    } else {
      if (msg.sender != owner() && msg.sender != manager) revert NotAllowed();
    }
    ...
}
```

Alternatively set `idleCDO` atomically inside `initialize`/factory deployment so the unauthenticated window never exists, and apply the `maxApr` bound to `unscaledApr` as well.

### Proof of Concept
Foundry fork PoC sketch (requires existing test harness, e.g. `test/foundry/IdleCreditVault.t.sol` setup):

```solidity
function test_UnauthAprBeforeCdoLink() external {
    // strategy.initialize(...) has run; setWhitelistedCDO has NOT been called yet
    // idleCDO == address(0), maxApr == 0 (cap disabled)

    address attacker = makeAddr("attacker");
    vm.prank(attacker);
    // no revert: access check skipped while idleCDO == 0
    strategy.setAprsWithBuffer(1000e18, 0, 0); // arbitrary unscaledApr + lastApr

    assertEq(strategy.unscaledApr(), 1000e18);
    assertEq(strategy.lastApr(), 1000e18);

    // owner then wires the CDO; poisoned APR is consumed by startEpoch's
    // expectedEpochInterest and requestWithdraw's unscaledApr==0 branch.
    vm.prank(owner);
    strategy.setWhitelistedCDO(address(cdoEpoch));

    // With APR0 buckets outstanding, stopEpoch now reverts in
    // prepareStopEpochWithApr0 (unscaledApr != 0), freezing epoch close.
}
```

Note: validity depends on whether production deployments always wire `idleCDO` atomically in the same transaction as `initialize` (e.g., inside `IdleCreditVaultFactory`). If the factory does so, the exploitable window reduces to front-running the factory/upgrade path only, which lowers likelihood but does not remove the latent unauthenticated path on any vault whose `idleCDO` is unset or reset.

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

**File:** contracts/IdleCDOEpochVariant.sol (L260-262)
```text
    int256 adjustedActiveInterest = int256(_calcInterest(getContractValue())) + interestForOverUnderPerformance;
    if (adjustedActiveInterest < 0) adjustedActiveInterest = 0;
    expectedEpochInterest = pendingWithdrawFees + uint256(adjustedActiveInterest);
```
