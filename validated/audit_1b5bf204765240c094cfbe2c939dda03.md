### Title
`IdleCreditVault::setAprs` / `setApr` can change `unscaledApr` mid-epoch without settling the pending APR0 withdraw bucket, permanently bricking `stopEpoch` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Analogous to the Olympus `setReserveFactor` bug (a privileged setter updates a parameter that downstream accounting depends on, without regenerating the dependent state), `IdleCreditVault.setAprs`/`setApr` allow the manager to change `unscaledApr` and `lastApr` at any time, including mid-epoch while APR0 withdraw receipts are pending. `prepareStopEpochWithApr0` hard-reverts if `unscaledApr != 0` while `apr0TotalPrincipal != 0`, so any non-zero APR change after an APR0 request makes every subsequent `stopEpoch`/`stopEpochWithDuration` revert, permanently freezing all pool funds.

### Finding Description
Withdraw requests made while the pool APR is 0 are routed into the APR0 bucket instead of the normal receipt path: `requestWithdraw` checks `unscaledApr == 0` and calls `_requestWithdrawApr0`, which increments `apr0TotalPrincipal` [1](#0-0) [2](#0-1) .

At `stopEpoch`, `prepareStopEpochWithApr0` refuses to operate if the APR was changed in the meantime:

```solidity
// APR0 principal is only valid while APR is 0 for that request lifecycle.
if (unscaledApr != 0) {
  revert NotAllowed();
}
``` [3](#0-2) 

However, the setters that mutate `unscaledApr`/`lastApr` have no epoch-phase guard and no "regenerate/settle the pending APR0 bucket" step — the exact bug class of the Olympus report (parameter changed, dependent accounting not regenerated):

```solidity
function setAprs(uint256 _unscaledApr, uint256 _apr) external {
  unscaledApr = _unscaledApr;
  setApr(_apr);
}
...
function setApr(uint256 _apr) public {
  ...
  if (_maxApr != 0 && _apr > _maxApr) revert NotAllowed();
  lastApr = _apr;
}
``` [4](#0-3) [5](#0-4) 

Unlike the Olympus case (where the sponsor could defer to the next regen), there is no fallback path here: the revert is unconditional inside `stopEpoch`, so there is no privileged recovery call that can unwind the state once `apr0TotalPrincipal != 0` and `unscaledApr != 0`. Resetting `unscaledApr` back to 0 does not help retroactively either only if the revert is avoided — actually it does avoid the revert, but `apr0RateByEpoch` was never written for the interrupted epoch, so pending APR0 receipts settle with `settledInterest = 0` and, worse, any epoch stopped in between is impossible — the manager must notice and restore APR to 0 *before* the epoch end; if `stopEpoch` is attempted (or forced by epoch duration lapse) while APR is non-zero, it always reverts, and receipts' interest is silently lost when the APR is later zeroed and a new epoch's rate mapping (`apr0RateByEpoch[reqEpoch]`) was never populated, underpaying APR0 claimants.

### Impact Explanation
- Primary: any manager APR update (a routine, honest operation — e.g., repricing the loan for the next epoch) executed during a running epoch that contains pending APR0 withdraw requests makes `stopEpoch` permanently revert until `unscaledApr` is manually restored to 0, temporarily freezing all LP principal and interest; if `stopEpochWithDuration`/default flows are invoked they revert identically since they funnel through `prepareStopEpochWithApr0` [6](#0-5) .
- Secondary: pending APR0 receipt holders lose their pro-rata epoch interest — `apr0RateByEpoch[reqEpoch]` is only written inside `prepareStopEpochWithApr0`, so requests stranded across the APR change claim principal only (`_settleApr0` reads a zero rate) [7](#0-6) .

Broken invariant: epoch state machine liveness / one-receipt-one-payout for APR0 receipts.

### Likelihood Explanation
The trigger is an honest manager calling `setAprs`/`setAprsWithBuffer` mid-epoch — the code explicitly permits this ("If manager manually set apr from here it will not be scaled") and nothing warns that APR0 receipts are pending. The attacker (an unprivileged lender) only needs to have an APR0 withdraw request open when it happens. No default, no malicious role required.

### Recommendation
Mirror the Olympus mitigation "conditionally regenerate": either revert in `setAprs`/`setApr` when `apr0TotalPrincipal != 0` and `isEpochRunning` (forcing the rate change to the next epoch boundary, like `setEpochParams` already does at `IdleCDOEpochVariant.sol:122`), or settle the pending APR0 bucket (write `apr0RateByEpoch[epochNumber]` and clear `apr0TotalPrincipal`) before applying the new rate. Also settle `apr0Users` lazily at claim time is already implemented, so only the aggregate bucket needs handling.

### Proof of Concept
Foundry fork-style PoC on `test/foundry/IdleCreditVault.t.sol` harness:

```solidity
function testAprChangeMidEpochWithApr0RequestsBricksStopEpoch() external {
  uint256 amount = 10_000 * ONE_SCALE;
  idleCDO.depositAA(amount);

  // manager sets APR to 0 (APR0 mode) and starts the epoch
  vm.startPrank(manager);
  IdleCreditVault(address(strategy)).setAprs(0, 0);
  cdoEpoch.setEpochParams(365 days, 0);
  cdoEpoch.startEpoch();
  vm.stopPrank();

  // unprivileged lender requests withdraw -> routed to APR0 bucket
  cdoEpoch.requestWithdraw(amount, address(AAtranche));
  assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

  // honest manager reprices mid-epoch (allowed by code)
  vm.prank(manager);
  IdleCreditVault(address(strategy)).setAprs(5e18, 5e18); // unscaledApr != 0

  deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
  vm.warp(cdoEpoch.epochEndDate() + 1);

  // stopEpoch permanently reverts via prepareStopEpochWithApr0
  vm.prank(manager);
  vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
  cdoEpoch.stopEpoch(5e18, 0);
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L206-210)
```text
  function setAprs(uint256 _unscaledApr, uint256 _apr) external {
    unscaledApr = _unscaledApr;
    // here we also check that msg.sender is allowed
    setApr(_apr);
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L285-287)
```text
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
    } else {
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L490-508)
```text
  function prepareStopEpochWithApr0(uint256 _interest) external returns (uint256 _expInterest, uint256 _adjPendingWithdrawFees) {
    _onlyIdleCDO();
    IIdleCDOEpochVariant _cdo = IIdleCDOEpochVariant(idleCDO);
    uint256 _pendingFees = _cdo.pendingWithdrawFees();
    uint256 _tvl = _cdo.getContractValue();
    _expInterest = _interest > 1 ? _interest : _cdo.expectedEpochInterest();
    _adjPendingWithdrawFees = _pendingFees;
    // Principal currently waiting for withdraw that was requested while APR was 0,
    // net of the upfront management fee charged at request time.
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L556-564)
```text
    // Move principal from "open APR0 bucket" to "settled bucket" (same principal, not duplicated).
    _apr0User.settledPrincipal += _principal;
    uint256 _rate = apr0RateByEpoch[_reqEpoch];
    if (_rate != 0) {
      // Convert per-epoch rate to claimable underlying interest.
      _apr0User.settledInterest += (_principal * _rate) / 1e18;
    }
    _apr0User.principal = 0;
    _apr0User.principalEpoch = 0;
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
