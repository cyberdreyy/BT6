### Title
External ERC4626 losses remain unpriced, letting earlier withdraw requests consume remaining backing - (File: `contracts/strategies/idle/ProgrammableBorrower.sol`)

### Summary
`ProgrammableBorrower` detects losses in its external ERC4626 position, but only nets them against epoch interest and floors the reported amount at zero. If the loss exceeds accrued interest, no tranche NAV or strategy-token principal is burned. Withdrawal requests made during the following buffer are therefore priced using stale pre-loss tranche values. A tranche holder can reserve a withdrawal for more than the borrower’s remaining external-vault assets and receive the remaining backing first, leaving later holders undercollateralized or defaulted.

### Finding Description
`_vaultNetInterest` measures the current vault position plus withdrawals against the epoch baseline and returns either positive interest or a loss. However, `totalInterestDueNow` returns zero whenever the loss is larger than borrower and vault gains; it does not report the principal deficit itself. During a normal `stopEpoch(0, 0)` in minted-interest mode, this means `_amountToPullFromBorrower` is zero when there are no pending withdrawals. `onStopEpoch` leaves the impaired vault shares in place and records their reduced value as the next buffer baseline, while the CDO’s strategy-token accounting remains unchanged.

After the stop, withdrawal requests use the stale tranche price because `_updateAccounting` sees unchanged CDO-held strategy tokens. The request burns the CDO’s strategy-token principal and creates a full-sized pending receipt. On the next stop, `onStopEpoch` withdraws up to the requested amount from the ERC4626 vault and the CDO pulls that cash into `IdleCreditVault`. If the request equals the remaining vault value, the first requester is paid in full even though aggregate tranche liabilities exceed actual backing.

Example with two 50-unit holders and 100 units parked in the external vault:

1. A 50-unit external-vault loss occurs.
2. `vaultLoss()` is 50, but `totalInterestDueNow()` is 0.
3. The epoch stops successfully without burning 50 strategy tokens.
4. Attacker requests withdrawal of their 50 tranche tokens at the stale price, creating a 50-unit receipt.
5. The next stop withdraws and transfers the vault’s remaining 50 units to fund the attacker’s receipt.
6. The victim still has a 50-unit tranche claim, but the programmable borrower has no remaining vault backing. A later withdrawal request leads to an unpaid receipt or borrower default.

The relevant accounting is in `ProgrammableBorrower._vaultNetInterest` and `totalInterestDueNow`, while the successful-stop path only funds pending receipts and never automatically maps `vaultLoss()` to `_lossAmount`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

### Impact Explanation
This breaks the solvency and fair withdrawal invariant. Earlier requesters can be paid at pre-loss value while later holders inherit the entire deferred principal loss. In the 100-unit example, the attacker receives 50 units representing all remaining external-vault backing, and the other 50-unit holder’s claim is effectively worthless or enters default recovery. The stolen amount is the stale-price excess over the attacker’s fair share: with a 50% vault loss, a holder of half the tranches has a fair claim of 25 units but can reserve and receive 50, extracting 25 units from other holders.

### Likelihood Explanation
The bug requires a real loss in the selected ERC4626 vault larger than accrued borrower/vault yield. This is expected to be uncommon, but the protocol expressly accounts for such losses through `vaultLoss()` and tests the zero-floor behavior. Once that state exists, any KYC-passing tranche holder can call `requestWithdraw` during the buffer. No privileged actor needs to misbehave; the ordinary manager `stopEpoch` path preserves the stale principal accounting. The first sufficiently large requester receives preferential treatment.

### Recommendation
When stopping an epoch, propagate the ERC4626 principal deficit rather than flooring the entire result to zero. For example:

- Extend the programmable-borrower stop hook or resolver so `vaultLoss()` is converted into the `_lossAmount` used by `stopEpochWithDuration`.
- Burn the active-loss portion of CDO-held strategy tokens and haircut pending receipts through `previewLossAdjustedWithdrawFunds`.
- Ensure the pool-facing interest remains distinct from contractual borrower debt so the borrower still owes `borrowerInterestAccrued` even when vault losses reduce pool yield.
- Alternatively, make `onStopEpoch` return both a success flag and the realized principal loss, and force `IdleCDOEpochVariant` into the existing loss-adjusted stop path.
- Add a regression test where a two-user pool suffers a sub-total external-vault loss, one user withdraws first, and both users receive the same haircut.

### Proof of Concept
A Foundry fork test can reuse the programmable-borrower Morpho setup from `test/foundry/ProgrammableBorrowerCreditVault.t.sol` and represent an external vault loss by reducing the borrower’s live ERC4626 share position:

```solidity
function testVaultLossLetsFirstWithdrawerDrainRemainingBacking() external {
    uint256 amount = 10_000 * oneScale;

    vm.prank(owner);
    cdoEpoch.setIsInterestMinted(true);

    // Attacker and victim each own half the pool.
    _depositWithUser(attacker, amount / 2, true);
    _depositWithUser(victim, amount / 2, true);

    _startEpochAndCheckPrices(0);

    // Simulate a 50% loss in the real external ERC4626 vault.
    uint256 shares = morphoVault.balanceOf(address(programmableBorrower));
    programmableBorrower.rescueTokens(
        address(morphoVault),
        makeAddr("externalVaultLoss"),
        shares / 2
    );

    assertGt(programmableBorrower.vaultLoss(), 0);
    assertEq(programmableBorrower.totalInterestDueNow(), 0);

    // Normal stop succeeds without burning impaired principal.
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    uint256 stalePrice = cdoEpoch.virtualPrice(address(aaTranche));
    assertGt(stalePrice, morphoVault.convertToAssets(oneScale));

    // Attacker reserves the whole remaining vault position at stale price.
    vm.startPrank(attacker);
    uint256 attackerReceipt =
        cdoEpoch.requestWithdraw(0, address(aaTranche));
    vm.stopPrank();

    assertEq(attackerReceipt, amount / 2);
    assertEq(strategy.pendingWithdraws(), amount / 2);

    _startEpochAndCheckPrices(1);
    vm.warp(cdoEpoch.epochEndDate() + 1);

    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    uint256 attackerBefore = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();

    // Attacker receives all remaining vault backing, not the haircuted 25%.
    assertEq(underlying.balanceOf(attacker) - attackerBefore, amount / 2);
    assertEq(_programmableVaultAssets(), 0);

    // Victim's equal claim is now unbacked and can only enter default accounting.
    vm.prank(victim);
    uint256 victimReceipt =
        cdoEpoch.requestWithdraw(0, address(aaTranche));
    assertEq(victimReceipt, amount / 2);
}
```

The key assertions are that `vaultLoss()` is positive while `totalInterestDueNow()` is zero, the first receipt is created at the stale 50-unit basis, and claiming it consumes all remaining external-vault backing.

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L239-249)
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
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L259-265)
```text
    bufferedVaultDelta = 0;
    bufferInterest = 0;
    bufferStartVaultAssets = _currentVaultAssets();

    // Stop reserving epoch-end withdraw liquidity once IdleCDO has started the stop flow.
    epochPendingWithdraws = 0;
    epochAccountingActive = false;
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L330-333)
```text
  function totalInterestDueNow() external view returns (uint256) {
    (uint256 vaultInterest, uint256 loss) = _vaultNetInterest();
    uint256 totalGain = vaultInterest + borrowerInterestAccruedNow() + bufferInterest;
    return totalGain > loss ? totalGain - loss : 0;
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L339-345)
```text
    uint256 earnedAssets = _currentVaultAssets() + epochWithdrawnFromVault;
    uint256 principalAssets = epochStartVaultAssets + epochDepositedToVault;
    int256 netDelta = bufferedVaultDelta + int256(earnedAssets) - int256(principalAssets);
    if (netDelta > 0) {
      interest = uint256(netDelta);
    } else {
      loss = uint256(-netDelta);
```

**File:** contracts/IdleCDOEpochVariant.sol (L395-410)
```text
    if (isProgrammableBorrower) {
      // Ask the programmable borrower to recall ERC4626 liquidity before IdleCDO pulls funds.
      // Hook reverts bubble so transient ERC4626 liquidity failures can be retried.
      if (!IProgrammableBorrower(_borrower()).onStopEpoch(_amountToPullFromBorrower + _pendingWithdraws, _isRequestingAllFunds)) {
        // Emit the exact cash liability requested from the borrower, including recalled principal
        // in close-pool mode and excluding interest fronted through minted accounting.
        _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
        return;
      }
    }

    // accrue interest to idleCDO, this will increase tranche prices.
    // Send also tot withdraw requests amount to the IdleCreditVault contract
    try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
      // transfer in strategy and decrease pendingWithdraws
        _strategy.collectWithdrawFunds(_pendingWithdraws);
```
