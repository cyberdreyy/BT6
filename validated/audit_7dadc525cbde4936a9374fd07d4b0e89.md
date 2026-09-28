### Title
APR0 withdraw request permanently bricks `stopEpoch` after any APR increase, freezing all vault funds - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
A lender's `requestWithdraw` during an APR0 epoch leaves `apr0TotalPrincipal > 0`. If the honest manager later raises the APR (a normal configuration change), every subsequent `stopEpoch` reverts `NotAllowed` inside `prepareStopEpochWithApr0`, so the epoch can never be stopped and all vault funds, pending receipts, and borrower repayments are frozen until APR is manually reset to 0. An unprivileged KYC-passing lender can therefore arm a permanent DoS that triggers on an unrelated honest configuration change.

### Finding Description
`prepareStopEpochWithApr0` is called by the `IdleCDOEpochVariant` during `stopEpoch`. When `apr0TotalPrincipal != 0` it requires `unscaledApr == 0`, otherwise it reverts `NotAllowed` [1](#0-0) . `apr0TotalPrincipal` is only cleared on a *successful* `prepareStopEpochWithApr0` call [2](#0-1) , on default recovery initialization, or in the default-claim cleanup path — never by user action while `unscaledApr != 0`.

An unprivileged lender triggers `requestWithdraw` through the CDO. When `unscaledApr == 0` and the pool is not closed, `_requestWithdrawApr0` grows `apr0TotalPrincipal` [3](#0-2) . `setApr`/`setAprs` is open to the manager (and CDO) with only a `maxApr` cap, so raising APR mid-epoch is a permitted honest action [4](#0-3) . Because `stopEpoch` calls `prepareStopEpochWithApr0` and the revert precedes the reset of `apr0TotalPrincipal`, the revert is self-perpetuating: no subsequent `stopEpoch` succeeds while `unscaledApr != 0`.

### Impact Explanation
Epoch phase: running, APR0-mode vault. Sequence: (1) lender deposits via `depositAA`/`depositBB`, (2) lender calls `requestWithdraw` while `unscaledApr == 0` → `apr0TotalPrincipal > 0`, (3) manager raises APR via `setAprs`/`setAprsWithBuffer` (normal operation), (4) owner calls `stopEpoch` → reverts `NotAllowed` every time. Result: `epochNumber` never advances, so `claimWithdrawRequest` reverts (`epochNumber <= lastWithdrawRequest[_user]`) [5](#0-4) , `epochEndDate` never moves, borrower funds cannot be recalled, and the entire TVL plus all pending withdraw receipts are frozen. The freeze is recoverable only if the manager sets the APR back to 0, which the attacker cannot rely on being noticed quickly and which itself may be undesirable mid-epoch.

### Likelihood Explanation
Requires an APR0 epoch and a subsequent APR change, both legitimate configurations. The attacker needs only a KYC-passing lender position and a single `requestWithdraw` of any size — even dust-level principal suffices since `apr0TotalPrincipal` is only compared to zero [6](#0-5) . Cost is limited to the deposited principal (recoverable after APR is lowered again) plus gas, so the attack is cheap and can be re-armed each APR0 epoch.

### Recommendation
In `prepareStopEpochWithApr0`, do not revert when `unscaledApr != 0` and `apr0TotalPrincipal > 0`. Instead, settle the outstanding APR0 bucket at a rate computed from the already-realized data (or at zero rate, documenting that APR0 requests earn nothing if the APR was raised before their epoch ended), then clear `apr0TotalPrincipal`. Alternatively, block `setApr` from raising APR above 0 while `apr0TotalPrincipal != 0`, forcing the manager to complete the APR0 epoch first; this converts the freeze into an explicit, visible configuration constraint.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-foundry/Test.sol";
import "../../contracts/strategies/idle/IdleCreditVault.sol";
import "../../contracts/IdleCDOEpochVariant.sol";
import "../../contracts/IdleCDOTranche.sol";
import "../../contracts/mocks/MockERC20.sol"; // adjust to repo mock path

// Scenario: APR0-mode credit vault, epoch running.
// 1. Owner/manager deploy vault with _apr = 0 (APR0 mode).
// 2. Lender (KYC'd, unprivileged) deposits and calls CDO.requestWithdraw ->
//    IdleCreditVault.requestWithdraw -> _requestWithdrawApr0 -> apr0TotalPrincipal = X > 0.
// 3. Manager calls setAprs(5e18, 5e18) (or setAprsWithBuffer) -> unscaledApr = 5e18.
// 4. Borrower repays; owner calls cdo.stopEpoch().
// 5. stopEpoch -> strategy.prepareStopEpochWithApr0 -> revert NotAllowed()
//    because apr0TotalPrincipal != 0 && unscaledApr != 0.
// 6. Repeat step 4 forever: epochNumber never increments, epochEndDate stays set,
//    claimWithdrawRequest reverts (epochNumber <= lastWithdrawRequest[user]),
//    all TVL + pending receipts are frozen until APR is reset to 0.

contract Apr0StopEpochDoSTest is Test {
    function test_stopEpoch_bricked_after_apr_raise() public {
        // setup: vault.initialize(underlying, owner, manager, borrower, "B", 0)
        //        cdo deposit -> strategy.mint -> epoch started via startEpoch
        // lender requests withdraw while unscaledApr == 0
        vm.prank(address(cdo));
        vault.requestWithdraw(amount, lender, principal);
        assertGt(vault.apr0TotalPrincipal(), 0);

        // honest manager raises APR mid-epoch (allowed, only capped by maxApr)
        vm.prank(manager);
        vault.setAprs(5e18, 5e18);

        // borrower repays; owner attempts stopEpoch -> always reverts
        vm.expectRevert(NotAllowed.selector);
        vm.prank(address(cdo));
        vault.prepareStopEpochWithApr0(0);

        // even repeated attempts revert; apr0TotalPrincipal is never cleared
        vm.expectRevert(NotAllowed.selector);
        vm.prank(address(cdo));
        vault.prepareStopEpochWithApr0(expectedInterest);
    }
}
```

Uncertainty: I could not fully trace `stopEpoch` in `IdleCDOEpochVariant.sol` in this pass to confirm `prepareStopEpochWithApr0` is invoked unconditionally and that no fallback path exists; the caller is gated by `_onlyIdleCDO`, so the revert propagates into whichever CDO path calls it. Also unverified whether `setApr` is additionally restricted elsewhere (e.g., orchestrator); `setApr` itself only checks `msg.sender` is CDO or manager and the `maxApr` cap [7](#0-6) .

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L206-235)
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

  /// @notice set the fixed apr
  /// @dev only cdo and manager can set the apr. If manager manually set apr from 
  /// here it will not be scaled to include the buffer period
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L326-328)
```text
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L499-508)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L539-541)
```text
    // Close current APR0 bucket so it cannot accrue again on later stopEpoch calls.
    apr0TotalPrincipal = 0;
  }
```
