### Title
A single dust `requestWithdraw` during an APR=0 epoch permanently pins the vault to 0% APR or freezes `stopEpoch` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external report is a low-privileged, network-reachable denial of service. The in-scope analog is in the APR=0 withdraw-request flow of `IdleCreditVault`: once `apr0TotalPrincipal` is non-zero, `prepareStopEpochWithApr0` hard-reverts if `unscaledApr != 0`. Any KYC-passing lender can open a dust withdraw request while APR is 0, after which `stopEpoch`/`stopEpochWithDuration` cannot succeed under any non-zero APR, so the attacker can indefinitely pin the vault to 0% APR or block epoch rollover.

### Finding Description
In `IdleCreditVault.requestWithdraw`, when `unscaledApr == 0` and the pool is not closed, the request principal is recorded in `apr0Users[_user]` and aggregated into `apr0TotalPrincipal`. [1](#0-0) [2](#0-1) 

At epoch close, `IdleCDOEpochVariant._stopEpoch` calls `prepareStopEpochWithApr0`, which reverts whenever `apr0TotalPrincipal != 0 && unscaledApr != 0`. [3](#0-2) [4](#0-3) 

The sequence:

1. Pool runs a 0-APR epoch (a supported configuration: `apr0Users`/`apr0RateByEpoch` exist precisely for fixed-APR-0 pools).
2. During the buffer, an attacker (any KYC'd lender with a dust tranche balance) calls `IdleCDOEpochVariant.requestWithdraw(dust, tranche)`. This mints a receipt and sets `apr0TotalPrincipal > 0`. There is no minimum amount. [5](#0-4) 
3. For the next epoch, the manager wants a non-zero APR (calls `setAprsWithBuffer(_newApr, ...)`, which writes `unscaledApr`). [6](#0-5) 
4. Every `stopEpoch` call now reverts in `prepareStopEpochWithApr0` — the epoch can never close while APR is non-zero.
5. The bucket only clears inside `prepareStopEpochWithApr0` itself (`apr0TotalPrincipal = 0` at the end of a *successful* call), so the only escape is restoring `unscaledApr = 0` — and the attacker can re-open a dust request every subsequent 0-APR buffer, pinning APR at 0 forever. [7](#0-6) 

The `lossRecoveryPrice`-based re-request guard does not help, and there is no `skipped`/forgiveness path: the revert is unconditional.

### Impact Explanation
- **Temporary freezing of all vault funds**: while `unscaledApr != 0`, `stopEpoch` always reverts, so the running epoch cannot be closed — no borrower repayment, no withdraw funding, no new epoch. This lasts as long as the manager leaves APR non-zero (at minimum forcing an extra failed-stop epoch delay).
- **Permanent denial of non-zero APR / theft of yield**: the attacker can re-request a dust withdraw in every 0-APR buffer at negligible cost, forcing the vault to run at 0% APR indefinitely. All lenders' yield for those epochs is destroyed; in minted-interest mode the fee receivers' share is equally zeroed. For a pool with TVL `T` and intended APR `a`, the quantified loss is `T * a` per pinned epoch.
- This matches the bug class (unprivileged hang/crash → availability) with concrete fund impact.

### Likelihood Explanation
Requires only: (a) an epoch configured at 0% APR — a first-class supported mode with dedicated accounting — and (b) any `isWalletAllowed` lender holding dust tranche tokens, obtainable via a minimal `depositAA`/`depositBB`. No privileged cooperation is needed; the trigger is timing the honest manager's normal `setAprsWithBuffer`/`stopEpochWithDuration` call. The guard that would "fix" it (revert APR to 0) is itself the attacker's win condition.

### Recommendation
In `prepareStopEpochWithApr0`, do not revert when `unscaledApr != 0` while `apr0TotalPrincipal > 0`. Instead settle the pending APR0 bucket with `apr0RateByEpoch[epochNumber] = 0` (or with pro-rata interest computed from the realized `_expInterest`) and clear `apr0TotalPrincipal`, so the epoch can close under the new APR. Alternatively, settle APR0 requests lazily at claim time regardless of the new epoch's APR, removing the coupling between `unscaledApr` and open requests.

### Proof of Concept
Foundry fork test (extends `test/foundry/IdleCreditVault.t.sol` setup):

```solidity
function testApr0DustRequestBlocksAprIncrease() external {
    // epoch configured at 0% APR
    vm.startPrank(manager);
    cdoEpoch.setEpochParams(365 days, 0);
    IdleCreditVault(address(strategy)).setAprsWithBuffer(0, 365 days, 0);
    vm.stopPrank();

    idleCDO.depositAA(1000 * ONE_SCALE);
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, 0, _expectedFundsEndEpoch()); // stop with newApr = 0

    // attacker: KYC'd lender holding dust tranche tokens
    address attacker = makeAddr('apr0Griefer');
    _depositWithUser(attacker, 1 * ONE_SCALE, true);
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(1, address(AAtranche)); // dust request, sets apr0TotalPrincipal = 1-ish
    assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

    // manager schedules a normal 10% APR epoch and runs it
    vm.startPrank(manager);
    IdleCreditVault(address(strategy)).setAprsWithBuffer(10e18, 365 days, 0);
    cdoEpoch.startEpoch();
    vm.stopPrank();

    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.warp(cdoEpoch.epochEndDate() + 1);

    // stopEpoch permanently reverts while unscaledApr != 0 && apr0TotalPrincipal != 0
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 0);

    // epoch cannot close: borrower repayment, pending funding, and rollover all frozen
    // until APR is forced back to 0 — which the attacker re-griefs next buffer.
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L217-220)
```text
  function setAprsWithBuffer(uint256 _unscaledApr, uint256 _duration, uint256 _buffer) external {
    unscaledApr = _unscaledApr;
    setApr(_duration == 0 ? _unscaledApr : _unscaledApr * (_duration + _buffer) / _duration);
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L539-540)
```text
    // Close current APR0 bucket so it cannot accrue again on later stopEpoch calls.
    apr0TotalPrincipal = 0;
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

**File:** contracts/IdleCDOEpochVariant.sol (L360-364)
```text
    uint256 _expectedInterest;
    // Strategy finalizes APR0 bucket state and returns adjusted stopEpoch values.
    (_expectedInterest, _pendingWithdrawFees) = _strategy.prepareStopEpochWithApr0(_interest);
    // Pending withdraws may be increased during APR0 settlement, so read after prepareStopEpochWithApr0.
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();
```

**File:** contracts/IdleCDOEpochVariant.sol (L739-791)
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
  }
```
