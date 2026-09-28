### Title
A single APR0 withdraw request permanently DoSes `stopEpoch` whenever the pool returns to a non-zero APR — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.prepareStopEpochWithApr0` reverts with `NotAllowed` whenever `apr0TotalPrincipal != 0` and `unscaledApr != 0` [1](#0-0) . Any unprivileged, KYC-passing lender can open a dust-sized APR0 withdraw request via `IdleCDOEpochVariant.requestWithdraw` → `IdleCreditVault.requestWithdraw` → `_requestWithdrawApr0`, which sets `apr0TotalPrincipal` [2](#0-1) [3](#0-2) . From then on, every attempt to stop an epoch while the strategy APR is non-zero reverts inside `stopEpoch`, freezing the whole pool — all lender principal and pending withdraw claims — for as long as the attacker keeps an open APR0 receipt. This mirrors CVE-2020-7219: an unauthenticated actor imposing a denial of service on the core service path.

### Finding Description
- In APR0 mode (`unscaledApr == 0`), `requestWithdraw` routes the request into `_requestWithdrawApr0`, which accumulates `_apr0User.principal` and the global `apr0TotalPrincipal` [4](#0-3) [5](#0-4) .
- `apr0TotalPrincipal` is only cleared by a successful `prepareStopEpochWithApr0` (`apr0TotalPrincipal = 0` at line 540) or by a user claim path [6](#0-5) .
- `prepareStopEpochWithApr0` is invoked by the CDO during `stopEpoch`/`stopEpochWithDuration` (enforced by `_onlyIdleCDO` at line 491) and hard-reverts when `unscaledApr != 0` while the bucket is open [7](#0-6) .
- The manager sets the new epoch APR via `setAprsWithBuffer`/`setApr` around epoch transitions [8](#0-7) . The existing test `test…expectRevert(NotAllowed)` confirms `cdoEpoch.stopEpoch(0,0)` reverts when APR was moved to `1e18` with an open APR0 bucket [9](#0-8) .
- The attacker needs no privilege: deposit → wait for an APR0 epoch → `requestWithdraw(1 wei)` → hold the receipt. As long as the attacker never calls `claimWithdrawRequest` (claims are user-initiated at `claimWithdrawRequest`/`_claimFundedWithdrawRequest` [10](#0-9) ), the bucket stays open and every `stopEpoch` that carries a positive APR reverts. The attacker can even repeat the request after forced settlement, re-arming the DoS each epoch.

Broken invariant: liveness of the epoch state machine — an unprivileged user can indefinitely prevent the honest manager from stopping an epoch under any non-zero APR.

### Impact Explanation
Temporary (practically indefinite) freezing of all vault funds: no `stopEpoch` with positive APR can execute, so deposits cannot earn the intended yield, pending withdraw receipts cannot be funded via `collectWithdrawFunds` [11](#0-10) , and queued `processWithdrawRequests`/`processWithdrawalClaims` flows stall [12](#0-11) . The only escape is for the manager to keep `unscaledApr == 0` forever (denying the pool any yield and effectively ending the product) — the freeze persists as long as the attacker refrains from claiming and can be re-armed each epoch for dust cost.

### Likelihood Explanation
- Attacker profile: any KYC-passing lender/tranche holder (`_checkAllowed`/`isWalletAllowed` only gate deposits; withdraw requests require just tranche balance) [13](#0-12) .
- Cost: 1 wei of tranche principal plus gas; no timing race, no privileged cooperation.
- Trigger condition: an APR0 epoch (a supported mode: `unscaledApr == 0` flow at lines 285–286) followed by the honest manager raising APR — the normal, expected operational path.
- Guard check: no existing guard stops it — the revert is the intended safety check itself, and it fires on every stop while the receipt is open.

### Recommendation
Make `prepareStopEpochWithApr0` tolerant to APR changes instead of reverting: e.g., settle the open APR0 bucket at a zero rate (`apr0RateByEpoch[epochNumber] = 0`) and clear `apr0TotalPrincipal` when `unscaledApr != 0`, or gate `_requestWithdrawApr0` accounting so receipts recorded under APR0 settle at the rate of their request epoch regardless of subsequent APR. Alternatively, force-settle outstanding APR0 principals at APR-change time in `setAprsWithBuffer`.

### Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
// Foundry fork test; reuses repo harness helpers (_depositWithUser, _startEpochAndCheckPrices, etc.)
function testApr0DustRequestDoSesStopEpochOnAprRaise() external {
    uint256 amount = 10000 * ONE_SCALE;
    idleCDO.depositAA(amount);                 // honest liquidity
    _transferBurnedTrancheTokens(address(this), true);

    // Epoch 0 runs at positive APR, then stops normally
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

    // Manager sets APR to 0 -> APR0 mode
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(0, 0);

    // Attacker (this contract / any KYC'd user) opens a dust APR0 withdraw request
    cdoEpoch.requestWithdraw(1, address(AAtranche));   // apr0TotalPrincipal = dust
    assertGt(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), 0);

    // Honest manager later wants to resume a positive APR and stop the epoch
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(1e18, 1e18);
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 0);                  // reverts forever while attacker holds receipt
}
```

Notes/uncertainty: I verified the revert site, the APR0 request path, and the clearing sites in `IdleCreditVault.sol`, plus the existing revert-expectation test. I could not fully trace the exact call order inside `IdleCDOEpochVariant.stopEpoch`/`_beforeStopEpoch` in this iteration (line-level view of `prepareStopEpochWithApr0`'s call site), but the `_onlyIdleCDO` guard and the test confirm it runs inside `stopEpoch` and aborts the epoch transition.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L217-235)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L301-330)
```text
  function claimWithdrawRequest(address _user) external returns (uint256 amount) {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized) {
      // Post-default requests are already priced after the haircut and backed by the reserve,
      // so they must not fall through to the defaulted-epoch receipt logic.
      amount = _claimPostDefaultWithdrawRequest(_user);
      if (amount != 0) return amount;
      // Only receipts created in the defaulted epoch are haircutted here; old fulfilled
      // receipts are handled below at par if they were already funded before default.
      amount = _claimDefaultedWithdrawRequest(_user);
    }
    amount += _claimLossAdjustedWithdrawRequest(_user);
    return amount + _claimFundedWithdrawRequest(_user);
  }

  /// @notice Claim a funded non-default withdraw request at par.
  /// @param _user address of the user
  /// @return amount amount claimed
  function _claimFundedWithdrawRequest(address _user) internal returns (uint256 amount) {
    // User should wait at least an epoch before claiming the withdraw. Once the epoch is over user can withdraw 
    // at any time even if a new epoch started. 
    // So if epochNumber is the same as the last withdraw request then we revert. Epoch number is increased at stopEpoch
    // NOTE: If a user does not claim a withdraw request and instead requests another withdraw, he will have to wait
    // for another epoch to claim both requests.
    // NOTE 2: if borrower defaults, old withdraw requests can still be claimed
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
    // settle APR=0 requests once the related epoch has ended
    _settleApr0(_user);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-430)
```text
  function collectWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    uint256 pendingBasis = pendingWithdraws;
    if (_amount < pendingBasis) {
      // Legacy receipts do not have per-epoch ownership data, so they can only be fully funded.
      if (!defaultRecoveryInitialized) revert NotAllowed();
      uint256 lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis;
      // Avoid storing a zero price, which is indistinguishable from "no loss-adjusted epoch".
      if (lossRecoveryPrice == 0) revert NotAllowed();
      pendingWithdraws = 0;
      lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;
    } else {
      // A plain implementation upgrade may leave legacy normal receipts pending. Their next
      // successful stop can fully fund the aggregate before lazy initialization occurs.
      pendingWithdraws = pendingBasis - _amount;
    }
    if (_amount != 0) {
      underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
    }
  }
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

**File:** test/foundry/IdleCreditVault.t.sol (L3331-3337)
```text
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(1e18, 1e18);

    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(0, 0);
```

**File:** contracts/IdleCDOEpochQueue.sol (L298-369)
```text
  function processWithdrawRequests() external {
    IdleCDOEpochVariant _cdo = IdleCDOEpochVariant(idleCDOEpoch);
    IdleCreditVault _strategy = IdleCreditVault(strategy);
    uint256 _epoch = _strategy.epochNumber();
    // only owner or strategy manager can call this
    _checkOnlyOwnerOrManager();
    // we revert if the are claims that needs to be processed
    _checkNotAllowed(pendingClaims);

    uint256 _pending = epochPendingWithdrawals[_epoch];
    if (_pending == 0) {
      return;
    }

    uint256 _instantWithdraws = _strategy.instantWithdrawsRequests(address(this));
    // here we receive strategyTokens for the queue contract, strategyTokens are 1:1 with underlyings
    // Instant requests always increase the queue-specific instant receipt ledger.
    uint256 _underlyingsRequested = _cdo.requestWithdraw(_pending, tranche);
    isEpochInstant[_epoch] = _strategy.instantWithdrawsRequests(address(this)) > _instantWithdraws;
    // save current implied tranche price for this epoch based on underlyings that will be received on claim
    uint256 _epochPrice = _underlyingsRequested * ONE_TRANCHE / _pending;
    if (_epochPrice == 0) {
      revert Is0();
    }
    epochWithdrawPrice[_epoch] = _epochPrice;
    // set pending withdraw requests to 0
    epochPendingWithdrawals[_epoch] = 0;
    // set pending claims to the amount of underlyings requested
    epochPendingClaims[_epoch] = _underlyingsRequested;
    // set flag for pending claims to true
    pendingClaims = true;
  }

  /// @notice process withdrawal claims. Claims can be done during the epoch for instant withdrawals
  /// an only in the buffer or after one epoch for the normal withdrawals
  /// @param _epoch epoch to claim, should be the epoch in which processWithdrawRequests is called
  function processWithdrawalClaims(uint256 _epoch) external {
    IdleCDOEpochVariant _cdo = IdleCDOEpochVariant(idleCDOEpoch);
    // only owner or strategy manager can call this
    _checkOnlyOwnerOrManager();

    uint256 _pending = epochPendingClaims[_epoch];
    if (_pending == 0) {
      return;
    }
    uint256 _balPre = IERC20Detailed(underlying).balanceOf(address(this));

    // check if the epoch is an instant withdraw epoch.
    // These calls will transfer underlyings to this contract and burn strategyTokens
    if (isEpochInstant[_epoch]) {
      _cdo.claimInstantWithdrawRequest();
    } else {
      _cdo.claimWithdrawRequest();
    }
    uint256 _received = IERC20Detailed(underlying).balanceOf(address(this)) - _balPre;
    // In APR=0 flow the final claimed amount can differ from request-time pending claims.
    // Rebase the withdraw price to the realized amount so users claim the correct final value.
    if (_received != _pending) {
      uint256 _updatedPrice = epochWithdrawPrice[_epoch] * _received / _pending;
      epochWithdrawPrice[_epoch] = _updatedPrice;
      // A valid aggregate recovery can still round this queue's small share to zero. Record that
      // terminal outcome explicitly so users can clear claims and later epochs are not blocked.
      if (_updatedPrice == 0) {
        isEpochWithdrawZero[_epoch] = true;
      }
    }

    // reset epoch pending claims
    epochPendingClaims[_epoch] = 0;
    // reset pending claims flag
    pendingClaims = false;
  }
```

**File:** contracts/IdleCDOEpochQueue.sol (L415-421)
```text
  function _checkAllowed(address wallet) internal view {
    IdleCDOEpochVariant cdoEpoch = IdleCDOEpochVariant(idleCDOEpoch);
    if (!cdoEpoch.isEpochRunning()) {
      revert EpochNotRunning();
    }
    _checkNotAllowed(!cdoEpoch.isWalletAllowed(wallet));
  }
```
