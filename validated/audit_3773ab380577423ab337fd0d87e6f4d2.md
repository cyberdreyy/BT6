### Title
Unprivileged ERC4626 vault user can permanently freeze the credit vault by bricking `vault.deposit`/`convertToAssets` used inside epoch hooks - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
`IdleCDOEpochVariant` in programmable-borrower mode delegates epoch lifecycle to `ProgrammableBorrower`, which makes unguarded external calls into a shared ERC4626 `vault` during `onStartEpoch` (a `vault.deposit` of all idle cash) and during `stopEpoch` (`totalInterestDueNow()` → `vault.convertToAssets`). Any user of that external vault can drive it into a state where `deposit` or `convertToAssets` reverts (e.g., filling the vault's `maxDeposit`/deposit cap, or a vault whose share valuation can be forced to revert/overflow). Because these calls sit on the critical epoch path and the escape hatch `setVault` is gated on `vault.balanceOf(address(this)) == 0 && !epochAccountingActive`, the credit vault's epoch state machine is dead — the analog of a "dead call used as a DepGroup" crashing the node.

### Finding Description
- `onStartEpoch` unconditionally deposits the contract's full idle balance via `_depositToVault(underlyingToken.balanceOf(address(this)), 0)`, which calls `vault.deposit` with no try/catch and no `maxDeposit` check. A revert propagates into `IdleCDOEpochVariant.startEpoch`, so the epoch can never start [1](#0-0) [2](#0-1) .
- `stopEpoch` resolves interest via `IProgrammableBorrower(_borrower()).totalInterestDueNow()`, which calls `_vaultNetInterest()` → `_currentVaultAssets()` → `vault.convertToAssets(shares)` — an unguarded view on the external vault [3](#0-2) [4](#0-3) [5](#0-4) .
- An unprivileged attacker who is an ordinary depositor of the shared ERC4626 vault can (a) fill the vault's deposit capacity so `vault.deposit` reverts, permanently blocking `startEpoch` during the buffer phase, or (b) push the vault into a state where `convertToAssets`/`withdraw` reverts, blocking `stopEpoch`. `onStopEpoch` wraps `vault.withdraw` in try/catch only when the shortfall is economically covered, but `totalInterestDueNow` is read *before* the hook and is not wrapped [6](#0-5) .
- The documented recovery paths are blocked precisely in the state where the attack lands: `setVault` reverts while `epochAccountingActive` or while the contract still holds old vault shares, which is exactly the parked-funds configuration between epochs [7](#0-6) . Only the trusted owner calling `rescueTokens` on the vault-share token can unwind it, i.e. there is no non-privileged or routine-operator recovery [8](#0-7) .

### Impact Explanation
While `startEpoch` is bricked, all pooled underlying (parked in the vault plus new queue deposits) is frozen: no epoch can run, `requestWithdraw` maturation requires epoch turnover, and `claimWithdrawRequest`/`claimInstantWithdrawRequest` can never settle. While `stopEpoch` is bricked mid-epoch, all tranche holders' principal and pending withdraw receipts are frozen indefinitely. Loss = 100% of vault TVL temporarily frozen (permanent if the external vault cannot return to a callable state or if owner rescue is unavailable). The broken invariant is liveness of the epoch state machine, caused by a "dead" external dependency used on the critical path.

### Likelihood Explanation
Requires a programmable-borrower deployment whose ERC4626 vault admits outside depositors and has any deposit limit, pause, or valuation path an attacker can influence — common for capacity-limited institutional vaults. The attacker needs only to be an ordinary vault depositor (explicitly in scope). Cost is bounded by the deposit needed to hit the vault's cap.

### Recommendation
- In `onStartEpoch`, cap the deposit to `vault.maxDeposit(address(this))` and keep excess on hand, or wrap `vault.deposit` in try/catch and park undeployed funds.
- Wrap `convertToAssets` reads (`_currentVaultAssets`, `totalInterestDueNow`, `availableToBorrow`) so a reverting vault valuation degrades gracefully instead of bricking `stopEpoch`/`startEpoch`.
- Allow `setVault`/`emergencyExitVault` to be invoked by owner/manager even while shares are held, so a dead vault can be swapped out without relying on `rescueTokens`.

### Proof of Concept
Foundry fork outline (programmable variant):
1. Deploy pool with `ProgrammableBorrower` pointing at a capacity-limited ERC4626 vault (e.g., `maxDeposit` = C); deposit TVL `T` via `idleCDO.depositAA/BB`, run `startEpoch`/`stopEpoch` once so funds park in the vault during the buffer.
2. Attacker (unprivileged) calls `vault.deposit(C - vault.maxDeposit(ProgrammableBorrower) + dust)` or otherwise saturates the vault's deposit cap so `vault.deposit(anything > 0)` reverts.
3. Buffer ends; manager calls `cdoEpoch.startEpoch()` → `onStartEpoch` → `_depositToVault(idleBalance)` → `vault.deposit` reverts → `startEpoch` reverts. Every subsequent attempt reverts identically.
4. Owner attempts `setVault(newVault)` → reverts `NotAllowed` because `vault.balanceOf(this) != 0`. Assert all user `claimWithdrawRequest`/`claimInstantWithdrawRequest` remain unsettleable; TVL frozen.

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L158-169)
```text
  function setVault(address _vault) external {
    _checkOnlyOwnerOrManager();
    if (_vault == address(0) || IERC4626(_vault).asset() != address(underlyingToken)) {
      revert InvalidAddress();
    }
    // Switching the accounting source is only safe once the current epoch is fully settled and the
    // old vault position has been unwound.
    if (epochAccountingActive || vault.balanceOf(address(this)) != 0) revert NotAllowed();
    underlyingToken.safeApprove(address(vault), 0);
    vault = IERC4626(_vault);
    _allowUnlimitedSpend(address(underlyingToken), _vault);
    emit VaultUpdated(_vault);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L201-222)
```text
  function onStartEpoch(uint256 _pendingWithdraws) external nonReentrant {
    _checkOnlyIdleCDO();
    _accrueBorrowerInterest();
    // Reserve the amount IdleCDO expects to pull back at stopEpoch before the real borrower can draw again.
    epochPendingWithdraws = _pendingWithdraws;
    uint256 currentVaultAssets = _currentVaultAssets();
    uint256 bufferStartAssets = bufferStartVaultAssets;
    // Carry vault PnL generated while the pool was in the buffer into the new active epoch so it
    // is eventually realized in tranche prices at the next stopEpoch.
    bufferedVaultDelta = int256(currentVaultAssets) - int256(bufferStartAssets);
    bufferStartVaultAssets = 0;
    // Snapshot total assets before re-depositing idle cash so the epoch principal baseline uses the
    // exact pre-deposit amount instead of a post-deposit share-conversion round-down.
    uint256 startAssets = underlyingToken.balanceOf(address(this)) + currentVaultAssets;
    // Any idle balance left on the contract between epochs is parked immediately into the vault.
    _depositToVault(underlyingToken.balanceOf(address(this)), 0);
    // Reset the epoch accounting baseline from the exact pre-start total assets so the new epoch
    // does not treat the just-deposited idle balance as fresh vault profit or loss.
    epochStartVaultAssets = startAssets;
    epochDepositedToVault = 0;
    epochWithdrawnFromVault = 0;
    epochAccountingActive = true;
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L239-253)
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
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L330-347)
```text
  function totalInterestDueNow() external view returns (uint256) {
    (uint256 vaultInterest, uint256 loss) = _vaultNetInterest();
    uint256 totalGain = vaultInterest + borrowerInterestAccruedNow() + bufferInterest;
    return totalGain > loss ? totalGain - loss : 0;
  }

  /// @notice Compute the net vault delta split into interest and loss (mutually exclusive).
  function _vaultNetInterest() internal view returns (uint256 interest, uint256 loss) {
    if (!epochAccountingActive) return (0, 0);
    uint256 earnedAssets = _currentVaultAssets() + epochWithdrawnFromVault;
    uint256 principalAssets = epochStartVaultAssets + epochDepositedToVault;
    int256 netDelta = bufferedVaultDelta + int256(earnedAssets) - int256(principalAssets);
    if (netDelta > 0) {
      interest = uint256(netDelta);
    } else {
      loss = uint256(-netDelta);
    }
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L378-385)
```text
  function _depositToVault(uint256 _assetAmount, uint256 _principalAssets) internal {
    if (_assetAmount == 0) return;
    uint256 shares = vault.deposit(_assetAmount, address(this));
    if (epochAccountingActive && _principalAssets != 0) {
      epochDepositedToVault += _principalAssets;
    }
    emit DepositedIntoVault(_assetAmount, shares);
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L545-549)
```text
  /// @notice Convert the current vault share position into underlying terms.
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L602-605)
```text
  function rescueTokens(address _token, address _to, uint256 _amount) external onlyOwner {
    if (_to == address(0)) revert InvalidAddress();
    IERC20Detailed(_token).safeTransfer(_to, _amount);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L1001-1008)
```text
  function _resolveStopEpochInterest(uint256 _interest) internal view returns (uint256 _resolvedInterest) {
    if (isProgrammableBorrower) {
      _checkNotAllowed(_interest > 1);
      return IProgrammableBorrower(_borrower()).totalInterestDueNow();
    }

    _resolvedInterest = _interest;
  }
```
