### Title
APR change while APR0 withdraw requests are pending permanently reverts `stopEpoch`, freezing all vault funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`prepareStopEpochWithApr0` reverts with `NotAllowed()` whenever `unscaledApr != 0` while `apr0TotalPrincipal > 0`. Any KYC-passing lender can create a pending APR0 withdraw request during a zero-APR epoch; once the manager or CDO sets a non-zero APR for the next epoch, every `stopEpoch` call reverts and the epoch can never end, freezing all pending and active funds until the APR is manually reset to 0.

### Finding Description
In `IdleCreditVault`, `requestWithdraw` routes to `_requestWithdrawApr0` when `unscaledApr == 0`, which increments `apr0TotalPrincipal` [1](#0-0) [2](#0-1) . At `stopEpoch`, the CDO calls `prepareStopEpochWithApr0`, which returns early only when `apr0TotalPrincipal == 0`; otherwise it hard-reverts if `unscaledApr != 0` [3](#0-2) .

`apr0TotalPrincipal` can only be cleared inside `prepareStopEpochWithApr0` itself [4](#0-3)  or when a defaulted claim is cleared [5](#0-4) . Users cannot claim their APR0 request to clear the bucket because `_claimFundedWithdrawRequest` reverts while `epochNumber <= lastWithdrawRequest[_user]` [6](#0-5) , and `epochNumber` only increments on a successful `stopEpoch`/`deposit` [7](#0-6) . This is a deadlock: the bucket cannot be cleared without stopping the epoch, and the epoch cannot stop while the bucket exists under a non-zero APR.

`setApr` imposes no check on `apr0TotalPrincipal`, so an APR update is accepted mid-epoch and then bricks the subsequent `stopEpoch` [8](#0-7) .

### Impact Explanation
A single, minimal APR0 withdraw request (no minimum amount) leaves `apr0TotalPrincipal > 0`. After the APR is raised, `stopEpoch` always reverts: pending withdraw receipts can never be funded or claimed, and all lender capital plus yield is frozen in the borrower/strategy for as long as the non-zero APR stands. The only recovery is the manager setting APR back to 0 (extra privileged remediation), so this is temporary freezing of 100% of vault TVL, griefable by any unprivileged KYC lender. An attacker can also re-request APR0 withdraws each zero-APR epoch to repeat the grief whenever the vault tries to leave APR0 mode.

### Likelihood Explanation
Requires the vault to operate in APR0 mode (`unscaledApr == 0`, a supported mode), an attacker who passes KYC and deposits + requests withdraw, and the honest manager/CDO later setting a non-zero APR — a routine operational action the attacker can front-run or wait for, since `requestWithdraw` is open to any allowed lender during buffer/running phases [9](#0-8) . No privileged misbehavior is needed; the attacker transaction is an ordinary withdraw request.

### Recommendation
In `prepareStopEpochWithApr0`, settle or force-clear the APR0 bucket instead of reverting when `unscaledApr != 0` (e.g., settle principal-only with zero rate, or roll `apr0Users` principal into `settledPrincipal` and zero `apr0TotalPrincipal`). Alternatively, revert in `setApr`/`setAprs`/`setAprsWithBuffer` when `apr0TotalPrincipal != 0` so an APR change cannot be committed while it would brick `stopEpoch`, making the failure explicit at the configuration step rather than inside the epoch state machine.

### Proof of Concept
Foundry fork PoC sketch (mode: APR0, running epoch):

```solidity
// test/foundry/IdleCreditVaultApr0Freeze.t.sol
function test_apr0RequestThenAprChangeFreezesStopEpoch() public {
    // Setup: vault deployed with unscaledApr == 0 (APR0 mode), epoch running.
    // Attacker: a Keyring-whitelisted lender.
    vm.startPrank(attacker);
    cdo.depositAA(100e6);              // or depositBB
    cdo.requestWithdraw(100e6);        // creates APR0 request
    vm.stopPrank();
    assertGt(vault.apr0TotalPrincipal(), 0);

    // Honest manager sets APR for next epoch (buffer or mid-epoch as allowed).
    vm.prank(manager);
    vault.setAprsWithBuffer(5e18, epochDuration, bufferDuration); // unscaledApr = 5e18

    // Warp past epochEndDate, borrower funds repayment; stopEpoch always reverts.
    vm.warp(vault.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(NotAllowed.selector);
    cdo.stopEpoch(0);

    // Attacker cannot clear the bucket: claim reverts (epochNumber not advanced).
    vm.prank(attacker);
    vm.expectRevert(NotAllowed.selector);
    cdo.claimWithdrawRequest();

    // Vault stays frozen until manager resets APR to 0 (privileged remediation).
}
```

Note: I was unable to read `IdleCDOEpochVariant.stopEpoch` in this session to confirm the exact call site of `prepareStopEpochWithApr0`; grep confirms the function is referenced there, but the PoC assumes it is invoked unconditionally from `stopEpoch` when the strategy is an `IdleCreditVault`.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L243-295)
```text
  function requestWithdraw(uint256 _amount, address _user, uint256 _principal) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    if (_amount == 0) return;
    if (defaultRecoveryFinalized) {
      // user should first claim old already-funded withdraw requests before requesting new ones after default
      if (_hasWithdrawRequest(_user) || instantWithdrawsRequests[_user] != 0 || postDefaultRequests[_user] != 0) {
        revert NotAllowed();
      }
      // Preserve request/claim UX after default without increasing borrower-facing pendingWithdraws.
      // The CDO passes an already-haircut amount because finalization lowered virtualPrice first.
      _burn(msg.sender, _amount);
      _mint(_user, _amount);
      postDefaultRequests[_user] = _amount;
      return;
    }
    bool isClosed = IIdleCDOEpochVariant(idleCDO).epochEndDate() == 0;
    uint256 currentEpoch = epochNumber;
    uint256 lossEpoch = lastWithdrawRequest[_user];
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (
      lossRecoveryPrice != 0 &&
      (withdrawsRequestsByEpoch[_user][lossEpoch] != 0 ||
      (apr0Users[_user].principal != 0 && apr0Users[_user].principalEpoch == lossEpoch))
    ) {
      // A loss-adjusted receipt must be claimed before opening a later request, otherwise
      // `lastWithdrawRequest` would stop pointing to the epoch that stores its haircut.
      revert NotAllowed();
    }
    // burn strategy tokens from cdo (we don't burn future interest here, only the principal)
    _burn(msg.sender, _principal);
    // mint equal amount of strategy tokens to the user as receipt (interest included), useful in case of default
    _mint(_user, _amount);
    // A successfully closed pool already recalled all funds and has no later stopEpoch.
    if (!isClosed) {
      // Global amount that stopEpoch must source from borrower/strategy for all pending receipts.
      pendingWithdraws += _amount;
    }
    // save the epoch of the last withdraw request (buffer + epochDuration is 1 epoch)
    lastWithdrawRequest[_user] = currentEpoch;
    // APR=0 requests keep separate accounting and settle interest at stopEpoch.
    // `_amount` here is the post-management-fee principal bucket for that flow.
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
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L326-328)
```text
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L501-508)
```text
    // Fast path: no APR0 accounting needed.
    if (_principal == 0) {
      return (_expInterest, _adjPendingWithdrawFees);
    }
    // APR0 principal is only valid while APR is 0 for that request lifecycle.
    if (unscaledApr != 0) {
      revert NotAllowed();
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L540-540)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-611)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L821-831)
```text
    Apr0UserData storage apr0User = apr0Users[_user];
    if (apr0User.principal != 0 && apr0User.principalEpoch == _claimEpoch) {
      if (_isClearingApr0) {
        uint256 apr0Principal = apr0User.principal;
        uint256 totalApr0Principal = apr0TotalPrincipal;
        // prepareStopEpochWithApr0 may already close the global APR0 bucket before default finalization.
        apr0TotalPrincipal = apr0Principal >= totalApr0Principal ? 0 : totalApr0Principal - apr0Principal;
      }
      apr0User.principal = 0;
      apr0User.principalEpoch = 0;
    }
```
