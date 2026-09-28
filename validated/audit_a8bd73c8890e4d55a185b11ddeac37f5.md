### Title
Mid-epoch withdraw requests do not invalidate the stale `epochPendingWithdraws` liquidity reservation, letting the borrower drain funds reserved for receipts and forcing a borrower default / LP haircut - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
Like the IMA bug (missing `file_truncate`/`path_truncate` hooks leave a stale measurement cache after the underlying object changes), `ProgrammableBorrower` snapshots the epoch's withdraw-liquidity reservation once in `onStartEpoch` and never re-invalidates it. `IdleCreditVault.requestWithdraw` grows `pendingWithdraws` with no epoch-running gate, while `availableToBorrow()` keeps lending against the stale reservation. A tranche holder can request a withdrawal mid-epoch; the honest borrower then draws cash that should have been reserved, and `stopEpoch` cannot source the receipt funds, forcing `_handleBorrowerDefault` and a pro-rata haircut on all pending receipts plus a BB-first NAV loss.

### Finding Description
- In `onStartEpoch`, the borrower-side reserve is written once: `epochPendingWithdraws = _pendingWithdraws` [1](#0-0) 
- `availableToBorrow()` subtracts only that cached value from live assets [2](#0-1) 
- `IdleCreditVault.requestWithdraw` increments `pendingWithdraws` for any non-closed pool — there is no `isEpochRunning` check, and `_ensureDefaultRecoveryInitialized`/loss-receipt guards do not gate on epoch phase [3](#0-2) 
- At `stopEpoch`, `IdleCDOEpochVariant` reads the live `pendingWithdraws` and requires `onStopEpoch(_amountToPullFromBorrower + _pendingWithdraws, ...)` to free that cash [4](#0-3) 
- `onStopEpoch` only withdraws from the vault up to what shares cover; if the drawn borrower principal makes the shortfall exceed `_currentVaultAssets()`, it returns `true` and the subsequent `transferFrom` fails → `_handleBorrowerDefault` [5](#0-4) 

Nothing updates `epochPendingWithdraws` between `onStartEpoch` and `onStopEpoch`, so the reservation is a stale cache of the obligation at epoch start.

### Impact Explanation
A lender holding tranche tokens (unprivileged attacker) calls `requestWithdraw` during a running epoch. The honest borrower, using `availableToBorrow()`, draws funds that now economically back the new receipt. At `stopEpoch`, the contract cannot pull `pendingWithdraws`, triggering `_handleBorrowerDefault`: all pending withdraw receipts are haircut pro-rata via `defaultRecoveryPrice`/`_claimDefaultedWithdrawRequest` [6](#0-5) , and remaining NAV takes a BB-first loss. Loss equals the drawn shortfall amount (up to the full withdraw request) socialized across receipt holders and BB LPs; the attacker accepts a haircut on their own receipt but forces losses on other users' receipts and NAV disproportionate to their cost — and can additionally hold AA tranches, which are shielded until BB is wiped, converting the attack into net extraction when their receipt is small relative to their AA position.

### Likelihood Explanation
Requires only: programmable-borrower mode with `isInterestMinted`, a running epoch, and an attacker with a tranche position who files a withdraw request before the borrower draws. No privileged misbehavior is needed — the borrower acts exactly as designed against a stale reservation. The missing invalidation is structural, not a timing edge case.

### Recommendation
Keep the borrower-side reservation in sync with live obligations: either have `IdleCDOEpochVariant.requestWithdraw` push the new pending amount into `ProgrammableBorrower` (increment `epochPendingWithdraws`), or have `availableToBorrow()` read `IdleCreditVault.pendingWithdraws()` directly instead of the cached value. Alternatively, gate `requestWithdraw` to non-running phases in programmable mode.

### Proof of Concept
```solidity
// Foundry fork-style PoC outline (ProgrammableBorrowerCreditVault.t.sol harness)
// Setup: minted-interest mode, programmable borrower, APR0 strategy (apr=0).
vm.prank(owner); cdoEpoch.setIsInterestMinted(true);
idleCDO.depositAA(10_000e18);           // LP + attacker both deposit
idleCDO.depositAA(attackerDeposit);     // attacker holds AA
_startEpochAndCheckPrices(0);
// onStartEpoch cached epochPendingWithdraws = 0

// Attacker requests a withdrawal mid-epoch; requestWithdraw has no epoch-running gate
vm.prank(attacker);
cdoEpoch.requestWithdraw(0, address(aaTranche));   // pendingWithdraws += R in strategy
// epochPendingWithdraws is still 0 -> availableToBorrow() unchanged (stale cache)

// Honest borrower draws funds that should have been reserved for the receipt
uint256 draw = programmableBorrower.availableToBorrow();
vm.prank(revolvingBorrower);
programmableBorrower.borrow(draw);      // drains cash incl. R-worth

vm.warp(cdoEpoch.epochEndDate() + 1);
// stopEpoch asks onStopEpoch for pendingWithdraws; vault+onHand < required
// -> transferFrom fails -> _handleBorrowerDefault
vm.prank(manager);
cdoEpoch.stopEpoch(0, 0);
assertTrue(cdoEpoch.defaulted());       // forced default despite solvent borrower
// All pending receipts are haircut via defaultRecoveryPrice; BB NAV absorbs residual loss.
```

Caveat: I could not confirm within the iteration budget whether `requestWithdraw` in `IdleCDOEpochVariant` adds its own `isEpochRunning` gate above the strategy call — if it does, the same stale-reservation gap still applies to requests made during the buffer that are not reflected in the `_pendingWithdraws` snapshot passed to `onStartEpoch`.

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L201-205)
```text
  function onStartEpoch(uint256 _pendingWithdraws) external nonReentrant {
    _checkOnlyIdleCDO();
    _accrueBorrowerInterest();
    // Reserve the amount IdleCDO expects to pull back at stopEpoch before the real borrower can draw again.
    epochPendingWithdraws = _pendingWithdraws;
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L239-254)
```text
    uint256 onHand = underlyingToken.balanceOf(address(this));
    if (_amountRequired > onHand) {
      uint256 shortfall = _amountRequired - onHand;
      // If the vault shares do not economically cover the shortfall, let IdleCDO's later
      // transferFrom fail and use the existing default path. Only an otherwise-covered ERC4626
      // withdrawal failure should make stopEpoch retryable.
      if (shortfall > _currentVaultAssets()) return true;
      try vault.withdraw(shortfall, address(this), address(this)) returns (uint256 shares) {
        if (epochAccountingActive) {
          epochWithdrawnFromVault += shortfall;
        }
        emit WithdrawnFromVault(shortfall, shares, address(this));
      } catch {
        revert StopEpochVaultLiquidityUnavailable();
      }
    }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L350-355)
```text
  function availableToBorrow() public view returns (uint256) {
    uint256 totalAssets = underlyingToken.balanceOf(address(this)) + _currentVaultAssets();
    // Interest is minted (not pulled as cash), so only pending withdraw requests need reservation.
    uint256 reserved = epochPendingWithdraws;
    return totalAssets <= reserved ? 0 : totalAssets - reserved;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L259-280)
```text
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L772-784)
```text
  function _claimDefaultedWithdrawRequest(address _user) internal returns (uint256 amount) {
    uint256 defaultEpoch = defaultRecoveryEpoch;
    (uint256 claimBasis, uint256 burnAmount) = _clearWithdrawClaimForEpoch(_user, defaultEpoch, true);
    if (claimBasis == 0) return amount;

    // pendingWithdraws stores the claim basis owed by the borrower, including APR0 interest.
    pendingWithdraws -= claimBasis;
    // Only receipt principal exists as strategy tokens. APR0 interest is included in claimBasis
    // but was never minted as a user strategy-token receipt.
    _burn(_user, burnAmount);
    amount = (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL;
    _transferDefaultRecovery(_user, amount);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L362-398)
```text
    (_expectedInterest, _pendingWithdrawFees) = _strategy.prepareStopEpochWithApr0(_interest);
    // Pending withdraws may be increased during APR0 settlement, so read after prepareStopEpochWithApr0.
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();

    // special case where we get everything back from the borrower
    if (_isRequestingAllFunds) {
      // Recall gross strategy-token principal, including fee backing excluded from net CDO NAV.
      // Prefunded variants also add queue deposits already sent directly to the borrower.
      _totBorrowed += _contractTokenBalance(strategyToken);
      _expectedInterest += _totBorrowed;
    }
    uint256 _grossInterest = _isRequestingAllFunds ? _expectedInterest - _totBorrowed : _expectedInterest;
    bool _mintInterest = isInterestMinted && !_isRequestingAllFunds;
    // In minted mode IdleCDO only needs cash for withdraw requests, not for epoch interest itself.
    uint256 _amountToPullFromBorrower = _mintInterest ? 0 : _expectedInterest;
    if (_mintInterest && _interest > 1) {
      uint256 _maxApr = _strategy.maxApr();
      _checkNotAllowed(_maxApr != 0 && _grossInterest > _calcInterestWithApr(getContractValue(), _maxApr) + _pendingWithdrawFees);
    }

    // Checkpoint management fees before borrower funds are pulled in so the elapsed-period
    // accrual applies only to the pre-stop live NAV, not to newly received epoch interest.
    _accrueManagementFee();

    // Persist only resolved epoch interest for recovery accounting. In close-pool mode `_interest == 1`
    // is a sentinel: `_grossInterest` excludes the principal added to `_expectedInterest` above.
    expectedEpochInterest = _grossInterest;
    pendingWithdrawFees = _pendingWithdrawFees;

    // Pending receipts have no tranche identity, so their aggregate loss is pro rata across all
    // receipts. The remaining active loss is applied BB-first after the stop succeeds.
    (_pendingWithdraws, _lossAmount) = _strategy.previewLossAdjustedWithdrawFunds(_lossAmount);

    if (isProgrammableBorrower) {
      // Ask the programmable borrower to recall ERC4626 liquidity before IdleCDO pulls funds.
      // Hook reverts bubble so transient ERC4626 liquidity failures can be retried.
      if (!IProgrammableBorrower(_borrower()).onStopEpoch(_amountToPullFromBorrower + _pendingWithdraws, _isRequestingAllFunds)) {
```
