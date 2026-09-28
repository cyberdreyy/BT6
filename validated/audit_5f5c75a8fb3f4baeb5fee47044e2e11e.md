### Title
Attacker-supplied ERC4626 share-price swings forge fake "vault interest" in `ProgrammableBorrower._vaultNetInterest`, minting unbacked strategy tokens at `stopEpoch` - (contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
The external bug class is *trusting an externally fetched resource whose content an attacker can tamper with*. The on-chain analog is `IdleCDOEpochVariant.stopEpoch` blindly trusting `IProgrammableBorrower.totalInterestDueNow()` [1](#0-0) , which is derived from a live `convertToAssets` reading of an external ERC4626 vault [2](#0-1) . An unprivileged user of that vault can depress the share price across the buffer window, let the baseline snapshots commit the low value, then restore the price inside the next epoch so the recovery is booked as fresh profit and minted as unbacked interest.

### Finding Description
In minted-interest + programmable-borrower mode, `stopEpoch` resolves `_interest` via `_resolveStopEpochInterest`, which returns `IProgrammableBorrower.totalInterestDueNow()`. That value is `vaultInterest + borrowerInterestAccruedNow + bufferInterest - vaultLoss`, where `vaultInterest`/`vaultLoss` come from `_vaultNetInterest()` [3](#0-2) :

```
netDelta = bufferedVaultDelta + (currentVaultAssets + epochWithdrawnFromVault)
         - (epochStartVaultAssets + epochDepositedToVault)
```

`_currentVaultAssets()` is `vault.convertToAssets(vault.balanceOf(this))`, an externally supplied, manipulable number. Two snapshots matter:

- `onStopEpoch` sets `bufferStartVaultAssets = _currentVaultAssets()` [4](#0-3) .
- `onStartEpoch` sets `bufferedVaultDelta = currentVaultAssets - bufferStartVaultAssets` and `epochStartVaultAssets = startAssets` [5](#0-4) .

Attack sequence (all unprivileged actions against the ERC4626 vault):

1. **Running epoch ends.** Attacker pushes `convertToAssets` down for the programmable borrower's share position (e.g., withdraw/donate to compress price per share in a vault whose price is movable by a large shareholder, or drain the liquidity sources the vault reads). Cost is temporary, not a real loss, because the attacker holds offsetting vault shares.
2. **Manager (honest) calls `stopEpoch`.** `totalInterestDueNow` is read, then `onStopEpoch` snapshots `bufferStartVaultAssets` at the *depressed* value. Epoch accounting ends with the low baseline.
3. **Buffer period.** Attacker keeps the share price depressed until `startEpoch`.
4. **Manager calls `startEpoch`.** `onStartEpoch` records `bufferedVaultDelta ≈ 0` and `epochStartVaultAssets` at the depressed value [6](#0-5) .
5. **Attacker restores the price** (repays/redeposits/rebalances). `epochStartVaultAssets` was already committed low, so no `_depositToVault` principal baseline absorbs the recovery.
6. **Manager calls `stopEpoch` again.** `_vaultNetInterest` now reports `netDelta = +F` (the full restored amount) as vault interest. `totalInterestDueNow` returns `borrowerInterest + F`. Since `isInterestMinted` is forced true in programmable mode [7](#0-6) , `stopEpoch` executes `_strategy.mintStrategyTokens(_grossInterest)` including the phantom `F` [8](#0-7) , raises tranche prices via `_updateAccounting`, and mints fee shares to `feeReceiver`/owner on top.

The minted `F` strategy tokens have no corresponding cash pull (`_amountToPullFromBorrower` excludes minted interest) and no borrower debt (`settleBorrowerInterest` only settles `borrowerInterestAccrued`, contractually capped by `borrowerApr`, never vault PnL [9](#0-8) ). The attacker, holding tranche tokens, owns a pro-rata claim on `F` that was never funded.

### Impact Explanation
Broken invariant: **solvency / fair mint**. `mintStrategyTokens` creates 1:1 underlying claims with no backing for the phantom-profit component. If the attacker holds all AA supply, the entire `F` accrues to them; they exit via `requestWithdraw`/`claimWithdrawRequest` in the next epoch, and the vault/strategy becomes insolvent by up to `F` — paid from later lenders' principal or realized as a shortfall when the borrower only repays genuine obligations. Loss is bounded only by how far the attacker can swing the external vault's `convertToAssets` during the buffer window.

### Likelihood Explanation
Requires a programmable-borrower deployment (the mode where interest is minted, not pulled), an ERC4626 vault whose share price an unprivileged depositor can move, and two honest manager calls (`stopEpoch`, `startEpoch`) landing while the price is depressed — both are callable by anyone-facing managers on a schedule, and the attacker controls the price timing, not the callers. No privileged role is malicious; existing guards (`_checkOnlyIdleCDO`, `nonReentrant`, skim, default paths) do not validate that vault PnL is real, and `onDefault`'s comment even acknowledges the valuation view is untrusted for availability, but not for correctness.

### Recommendation
Treat `totalInterestDueNow`/`_vaultNetInterest` as untrusted external input: cap recognized vault profit per epoch (e.g., clamp `netDelta > 0` to a configurable `maxVaultInterestPerEpoch` or to `convertToAssets` evaluated against a TWAP/last-saved share price stored at `onStartEpoch`), or require manager-supplied interest confirmation (like the non-programmable `_interest` parameter) before minting strategy tokens for vault-sourced gains.

### Proof of Concept
Foundry fork PoC sketch (extend `test/foundry/ProgrammableBorrowerCreditVault.t.sol` setup which already wires a real MetaMorpho vault, `isProgrammableBorrower`, and `isInterestMinted`):

```solidity
function testForgedVaultInterestMintsUnbackedShares() external {
    uint256 amount = 10_000 * oneScale;
    vm.prank(owner); cdoEpoch.setIsInterestMinted(true);
    idleCDO.depositAA(amount);            // attacker is sole/majority AA holder
    _startEpochAndCheckPrices(0);

    // epoch 0 ends; attacker depresses vault price per share
    vm.warp(cdoEpoch.epochEndDate() + 1);
    uint256 shares = morphoVault.balanceOf(address(programmableBorrower));
    // depress convertToAssets: e.g. redeem-then-redeposit to strip accrued yield,
    // or move vault liquidity so PB's share valuation drops by F
    _depressVaultPrice(F);

    vm.prank(manager); cdoEpoch.stopEpoch(0, 0);   // bufferStartVaultAssets = low
    vm.warp(cdoEpoch.epochEndDate() + cdoEpoch.bufferPeriod() + 1);
    vm.prank(manager); cdoEpoch.startEpoch();       // epochStartVaultAssets = low

    _restoreVaultPrice(F);                          // price back to fair value
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager); cdoEpoch.stopEpoch(0, 0);    // mints borrowerInterest + F

    // F worth of strategy tokens were minted with zero cash pull and zero debt
    assertGt(cdoEpoch.lastEpochInterest(),
             programmaticBorrowerBorrowerInterestOnly(), "phantom vault interest minted");
    // attacker claims; residual NAV cannot cover all claims => insolvency of F
}
```

The key assertions: `lastEpochInterest` exceeds genuine contractual borrower interest by `F`, `borrowerInterestDebt` only reflects the contractual part, and after the attacker redeems, remaining tranche holders' `virtualPrice × supply` exceeds `getContractValue()` plus recoverable borrower debt by ≈ `F`.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L166-169)
```text
  function setIsInterestMinted(bool _isMinted) external {
    _checkOnlyOwner();
    _checkNotAllowed(!_isMinted && isProgrammableBorrower);
    isInterestMinted = _isMinted;
```

**File:** contracts/IdleCDOEpochVariant.sol (L428-432)
```text
      if (_mintInterest) {
        // if interest is not transferred we mint strategy tokens equal to the full epoch interest
        if (_grossInterest != 0) _strategy.mintStrategyTokens(_grossInterest);
        // and increase unclaimedFees by pending withdraw fees before _updateAccounting
        unclaimedFees += _pendingWithdrawFees;
```

**File:** contracts/IdleCDOEpochVariant.sol (L1001-1005)
```text
  function _resolveStopEpochInterest(uint256 _interest) internal view returns (uint256 _resolvedInterest) {
    if (isProgrammableBorrower) {
      _checkNotAllowed(_interest > 1);
      return IProgrammableBorrower(_borrower()).totalInterestDueNow();
    }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L206-219)
```text
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
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L259-261)
```text
    bufferedVaultDelta = 0;
    bufferInterest = 0;
    bufferStartVaultAssets = _currentVaultAssets();
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L275-283)
```text
  function settleBorrowerInterest() external nonReentrant {
    _checkOnlyIdleCDO();
    uint256 settled = borrowerInterestAccrued;
    if (settled != 0) {
      borrowerInterestAccrued = 0;
      borrowerInterestDebt += settled;
    }
    emit BorrowerInterestSettled(settled);
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
