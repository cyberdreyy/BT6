### Title
Withdraw-request interest is locked at the stale pre-change APR, letting a lender front-run a manager APR cut and extract interest the borrower never agreed to pay - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`requestWithdraw` converts tranche tokens into a fixed underlying-denominated receipt whose interest component is computed at the strategy's *current* APR at request time. The manager can change that APR at any moment during the buffer window via `IdleCreditVault.setApr`/`setAprs`/`setAprsWithBuffer` with no timelock and no epoch check. A KYC'd lender can therefore submit `requestWithdraw` immediately before a pending APR-decrease transaction, permanently baking the old, higher APR into an irrevocable claim (`withdrawsRequests` / `pendingWithdraws`) that the borrower is forced to fund at the next `stopEpoch`. The delta between the locked receipt rate and the renegotiated rate is paid by the borrower/pool with no compensating adjustment, or — if the borrower only funded the agreed amount — the resulting underfunding reverts `getFundsFromBorrower` and trips `_handleBorrowerDefault`, freezing the vault.

### Finding Description
The interest embedded in a withdraw receipt is calculated inside `requestWithdraw` at request time: [1](#0-0) 

`_calcInterestWithdrawRequest` derives `totInterest` from `_calcInterest(_managedContractValue())`, which uses `_getStrategyApr()` — i.e. `IdleCreditVault.lastApr` — at the moment of the call: [2](#0-1) [3](#0-2) 

Once `creditVault.requestWithdraw(_underlyings, msg.sender, principal)` executes, the principal-plus-interest amount is immutable: it is added to `withdrawsRequests[_user]`, `withdrawsRequestsByEpoch[_user][epoch]`, and the global `pendingWithdraws`, and minted to the user as receipt strategy tokens: [4](#0-3) 

Meanwhile APR is mutable outside the epoch state machine. `setApr` only requires `msg.sender == idleCDO || msg.sender == manager` and an optional `maxApr` bound — there is no `isEpochRunning`/`epochEndDate` gate and no delay: [5](#0-4) [6](#0-5) 

During the buffer phase (`isEpochRunning == false`, `allowAAWithdrawRequest/allowBBWithdrawRequest == true`), a lender watches the mempool for the manager's APR update (`setApr`, `setAprs`, `setAprsWithBuffer`, or an orchestrator call) and front-runs it with `requestWithdraw`. Their receipt is priced with the soon-to-be-replaced higher APR, while every depositor who stays, and every request submitted after the change, is priced at the new lower APR. At the next `stopEpoch`, the strategy pulls `pendingWithdraws` from the borrower in full: [7](#0-6) [8](#0-7) 

`interestForOverUnderPerformance` only corrects the AA/BB split asymmetry; nothing reconciles the request-time APR with the APR actually in force for the epoch in which the interest was supposedly earned. The only mitigation, `_isInstantWithdrawEnabled()` + `lastEpochApr > currentApr + instantWithdrawAprDelta`, compares the *previous* epoch's APR to the current one and merely reroutes the request to the instant path — it does not reprice the receipt, and it is entirely bypassed when `disableInstantWithdraw == true` (the default set in `_additionalInit`).

### Impact Explanation
Direct theft / insolvency. Let `P` be the attacker's requested principal, `r_old` the stale APR, `r_new` the APR set for the upcoming epoch, and `d = epochDuration / 365 days`. The attacker's receipt locks `P * r_old * d` of interest while the pool's economics (and the borrower's negotiated obligation) only support `P * r_new * d`. The difference `P * (r_old - r_new) * d`, net of fees, is extracted from `pendingWithdraws` funding at `stopEpoch`. Example: with `epochDuration = 30 days`, a 1,000,000 USDC request locking 20% APR instead of 5% overpays ≈ `1e6 * 0.15 * 30/365` ≈ 12,329 USDC per epoch, repeatable every buffer window where APR is revised downward. If the borrower only approves/funds the agreed (lower) liability, `getFundsFromBorrower` reverts and `_handleBorrowerDefault` pauses the vault and blocks all withdraw requests — a protocol-wide freeze triggered by the attacker's stale-priced claim.

### Likelihood Explanation
High whenever off-chain APR renegotiation happens, which is the designed flow for a private credit vault: the manager sets APR via `setApr`/`setAprsWithBuffer` between epochs, and the buffer phase exists precisely so lenders can react to new terms. Any mempool-observing, KYC'd lender (the only requirement is `isWalletAllowed`, i.e. a credential check, not privileged status) can sandwich the manager's transaction: `requestWithdraw` before, claim at the next epoch boundary. No capital at risk beyond the normal deposit, no privileged role needed, and the receipt cannot be repriced or revoked afterward — `requestWithdraw` even forbids replacing the request once a loss epoch is recorded, and claims are guaranteed by `pendingWithdraws` funding or the default path.

### Recommendation
- Compute withdraw-request interest at `stopEpoch`/claim time using the APR that actually governed the epoch (store the request epoch's APR in `withdrawsRequestsByEpoch` or `apr0RateByEpoch`-style per-epoch rates), rather than pricing the receipt at request time.
- Alternatively, restrict `setApr`/`setAprs`/`setAprsWithBuffer` so the manager cannot change APR during the buffer window while withdraw requests are being accepted (e.g. only inside `stopEpoch`/`startEpoch`), or add a timelock/announcement delay on APR changes.
- Snapshot the APR at `startEpoch` into an immutable per-epoch variable and use it for both `_calcInterest` and `_calcInterestWithdrawRequest` for that epoch.

### Proof of Concept
Foundry test sketch (fork context per `test/foundry/IdleCreditVault.t.sol` setup with `idleCDO`, `cdoEpoch`, `strategy`, `borrower`, `manager`, `defaultUnderlying`):

```solidity
function testFrontRunAprCut() external {
    uint256 amount = 1_000_000 * ONE_SCALE;
    // Attacker is a normal KYC'd lender
    idleCDO.depositAA(amount);
    _transferBurnedTrancheTokens(attacker, true); // attacker holds AA tranche tokens

    // Epoch 1 runs and stops; manager sets 20% APR for epoch 2 inside stopEpoch
    vm.startPrank(manager);
    cdoEpoch.setEpochParams(30 days, 5 days);
    cdoEpoch.startEpoch();
    vm.warp(cdoEpoch.epochEndDate());
    cdoEpoch.stopEpoch(20e18, 0); // reopen buffer, next-epoch APR = 20%
    vm.stopPrank();

    // Buffer phase: manager will renegotiate APR down to 5%.
    // Attacker front-runs the setAprsWithBuffer/setApr tx with requestWithdraw.
    vm.prank(attacker);
    uint256 requested = cdoEpoch.requestWithdraw(0, address(AAtranche));
    // receipt embeds interest at the STALE 20% APR
    uint256 staleInterest = amount * 20e18 / 100 * 30 days / (365 days * 1e18);
    assertApproxEqRel(requested, amount + staleInterest - _fees(amount, staleInterest), 0.01e18);

    // Honest manager lands the APR cut for the upcoming epoch
    vm.prank(manager);
    IdleCreditVault(address(strategy)).setAprs(5e18, 5e18 * 35 days / 30 days);

    // Epoch 2 runs; borrower owes only 5% interest on active TVL,
    // but pendingWithdraws still contains the attacker's 20%-priced receipt.
    vm.prank(manager);
    cdoEpoch.startEpoch();
    // Attacker's claim is immutable and must be funded at stopEpoch:
    assertEq(IdleCreditVault(address(strategy)).withdrawsRequests(attacker), requested);

    // If borrower only approved interest at 5% + principal, stopEpoch reverts in
    // getFundsFromBorrower -> _handleBorrowerDefault (vault frozen).
    // If borrower fully funds pendingWithdraws, attacker claims ~staleInterest - newInterest
    // more than warranted: ~1e6 * 15% * 30/365 ≈ 12,329 underlying.
    vm.warp(cdoEpoch.epochEndDate());
    vm.prank(manager);
    cdoEpoch.stopEpoch(5e18, 0);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    // attacker received `requested`, not the re-priced 5% amount
    assertEq(IERC20Detailed(defaultUnderlying).balanceOf(attacker), requested);
}
```

Key assertion: `requested` is fixed at request time and equals principal + interest at the pre-change APR; no code path recomputes it after `setApr`, and `stopEpoch` either over-charges the borrower or defaults the vault.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L408-410)
```text
    try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
      // transfer in strategy and decrease pendingWithdraws
        _strategy.collectWithdrawFunds(_pendingWithdraws);
```

**File:** contracts/IdleCDOEpochVariant.sol (L550-553)
```text
  function getFundsFromBorrower(uint256 _amount) external {
    _checkNotAllowed(msg.sender != address(this));
    _transferUnderlyingsFrom(_borrower(), address(this), _amount);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L772-790)
```text
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
```

**File:** contracts/IdleCDOEpochVariant.sol (L800-809)
```text
  function _calcInterest(uint256 _amount) internal view returns (uint256) {
    return _calcInterestWithApr(_amount, _getStrategyApr());
  }

  /// @notice Calculate the interest of an epoch for the given amount and apr
  /// @param _amount Amount of underlyings
  /// @param _apr Apr used for the calculation
  function _calcInterestWithApr(uint256 _amount, uint256 _apr) internal view returns (uint256) {
    return _amount * (_apr / 100) * epochDuration / (365 days * ONE_TRANCHE_TOKEN);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L856-878)
```text
  function _calcInterestWithdrawRequest(uint256 _amount, address _tranche) internal view returns (uint256 _interest, int256 _diff) {
    uint256 _duration = epochDuration;
    if (_duration == 0) {
      return (_interest, _diff);
    }

    uint256 _buffer = bufferPeriod;
    // calculate total vault interest (they don't get the interest for the buffer period for withdraw requests so 
    // we scale it back since _calcInterest is scaling the interest with tht buffer period),
    uint256 totInterest = _calcInterest(_managedContractValue()) * _duration / (_duration + _buffer);
    // calculate total tranche interest for the whole tranche supply
    uint256 totTrancheInterest = _calcTrancheInterestShare(totInterest, _tranche);
    // calculate interest for the given tranche and given amount
    uint256 _trancheBal = _lastSavedNAV(_tranche);
    _interest = _trancheBal == 0 ? 0 : _amount * totTrancheInterest / _trancheBal;
    // calculate the interest that the _amount would have received if there was no split ratio (ie interest split based only on tvl).
    // This is used to calculate the interest that should be added to the expectedEpochInterest when 
    // withdrawing an AA tranche or the interest that should be removed from expectedEpochInterest when
    // withdrawing a BB tranche
    uint256 interestWithoutSplitRatio = _calcInterest(_amount) * _duration / (_duration + _buffer);
    // difference between total interest and tranche interest (positive for AA, negative for BB)
    _diff = int256(interestWithoutSplitRatio) - int256(_interest);
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L272-295)
```text
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
