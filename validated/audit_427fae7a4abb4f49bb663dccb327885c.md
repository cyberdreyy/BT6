I found a promising analog — the programmable-borrower interest settlement being silently skipped on the non-minted path. Let me verify the accounting.### Title
Programmable borrower interest is paid in cash but never settled in non-minted mode, causing it to be charged twice and inflating tranche yield - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
The external bug is a silently-skipped post-processing step: `osh-encrypt-rsync` encrypts ttyrec files but quietly never signs them, leaving the promised integrity step undone while the main step succeeds. The analog in this codebase is in `_stopEpoch`: when `isProgrammableBorrower` is set but `isInterestMinted` is false, `IdleCDOEpochVariant` pulls the full epoch interest (which includes `borrowerInterestAccrued` via `totalInterestDueNow()`) from the borrower in cash, but silently skips the borrower-side interest settlement/clearing step. `settleBorrowerInterest()` is only invoked on the `_mintInterest && isProgrammableBorrower` branch, so the cash-paid interest remains in `borrowerInterestAccrued` and is included again in the *next* epoch's `totalInterestDueNow()` — the borrower is double-charged and the excess accrues to tranche holders.

### Finding Description
In `IdleCDOEpochVariant._stopEpoch`, the resolved interest for a programmable borrower comes from `IProgrammableBorrower.totalInterestDueNow()`, which returns `vaultInterest + borrowerInterestAccruedNow() + bufferInterest - vaultLoss` [1](#0-0) . In non-minted mode, `_amountToPullFromBorrower = _expectedInterest`, so `getFundsFromBorrower` transfers that interest in cash [2](#0-1) .

However, `settleBorrowerInterest()` — the only routine that clears `borrowerInterestAccrued` — is gated behind `if (_mintInterest && isProgrammableBorrower)` [3](#0-2) . On the non-minted programmable path neither `settleBorrowerInterest` nor any equivalent clearing is executed. `borrowerInterestAccrued` therefore persists into the next epoch. When the borrower re-borrows and the next `stopEpoch` runs, `totalInterestDueNow()` again includes that already-paid accrued interest, so `transferFrom` pulls it from the borrower a second time. The extra cash lands in the CDO, is folded into `expectedEpochInterest`/`_updateAccounting`, and permanently inflates tranche virtual prices [4](#0-3) .

This mirrors the advisory's shape exactly: the main action (encrypt ≙ pull epoch interest in cash) succeeds, while the accompanying integrity/settlement step (sign ≙ clear/settle `borrowerInterestAccrued`) is silently skipped, leaving state that no longer matches what was actually paid.

### Impact Explanation
Direct theft from the borrower, realized as yield by tranche holders. An attacker is any KYC-passing lender/tranche-token holder: they deposit AA/BB during the buffer, an epoch runs in non-minted programmable mode, the borrower pays interest I in cash at `stopEpoch`, and because `borrowerInterestAccrued` is never cleared, the next `stopEpoch` pulls I again and bakes it into tranche prices. The attacker redeems at the inflated virtual price, extracting up to I per repeated epoch. Loss is quantified: the full borrower contractual interest each cycle after the first. This breaks the solvency/fair-accounting invariant that each unit of borrower interest is paid once.

### Likelihood Explanation
Likelihood is moderate: it requires the programmable-borrower mode combined with `isInterestMinted = false`. Tests (`testProgrammableBorrowerBufferVaultYieldIsRealizedAtNextStop`) consistently call `setIsInterestMinted(true)` before running programmable epochs, suggesting minted mode is the intended pairing, but nothing in `_stopEpoch`, `_checkProgrammableBorrowerMode`, or `setIsInterestMinted` prevents the non-minted programmable configuration — if it is genuinely unsupported, the finding reduces to a missing configuration guard rather than a live accounting bug. If the configuration is reachable, exploitation requires no malicious privileged actor: the owner/manager simply runs the normal `startEpoch`/`stopEpoch` sequence, and the double charge happens automatically.

### Recommendation
On the non-minted programmable path of `_stopEpoch`, after `getFundsFromBorrower` succeeds, clear the just-paid interest on the borrower side (e.g., call a settlement hook that zeroes `borrowerInterestAccrued` without converting it to `borrowerInterestDebt`, since it was paid in cash rather than fronted). Alternatively, revert or disallow `isProgrammableBorrower && !isInterestMinted` configurations in `setIsInterestMinted`/initialization so the unhandled path cannot be reached.

### Proof of Concept
Foundry fork PoC sketch:

```solidity
// Setup: IdleCDOEpochVariant with ProgrammableBorrower strategy, isInterestMinted = false
idleCDO.depositAA(amount);            // attacker LP deposits during buffer
cdoEpoch.startEpoch();                // onStartEpoch: epochAccountingActive = true
// real borrower draws via programmableBorrower.borrow(...)
vm.warp(cdoEpoch.epochEndDate() + 1);
cdoEpoch.stopEpoch(0, 0);             // pulls totalInterestDueNow() in cash; accrued NOT cleared
assertGt(programmableBorrower.borrowerInterestAccrued(), 0, "paid interest still tracked");
cdoEpoch.startEpoch();
// borrower draws again; interest keeps accruing on top of stale accrued
vm.warp(cdoEpoch.epochEndDate() + 1);
uint256 due = programmableBorrower.totalInterestDueNow();
// `due` includes the previous epoch's already-cash-paid interest -> double charge
cdoEpoch.stopEpoch(0, 0);
// attacker LP redeems tranches at inflated virtualPrice, profiting the re-charged interest
```

Uncertainty note: I was unable to fully enumerate every guard around `setIsInterestMinted`/`isProgrammableBorrower` within the available iterations; if a configuration check elsewhere forbids non-minted programmable mode, this degrades to a missing/defense-in-depth check rather than a reachable theft.

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L330-334)
```text
  function totalInterestDueNow() external view returns (uint256) {
    (uint256 vaultInterest, uint256 loss) = _vaultNetInterest();
    uint256 totalGain = vaultInterest + borrowerInterestAccruedNow() + bufferInterest;
    return totalGain > loss ? totalGain - loss : 0;
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L360-408)
```text
    uint256 _expectedInterest;
    // Strategy finalizes APR0 bucket state and returns adjusted stopEpoch values.
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
        // Emit the exact cash liability requested from the borrower, including recalled principal
        // in close-pool mode and excluding interest fronted through minted accounting.
        _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
        return;
      }
    }

    // accrue interest to idleCDO, this will increase tranche prices.
    // Send also tot withdraw requests amount to the IdleCreditVault contract
    try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
```

**File:** contracts/IdleCDOEpochVariant.sol (L413-415)
```text
      if (_mintInterest && isProgrammableBorrower) {
        IProgrammableBorrower(_borrower()).settleBorrowerInterest();
      }
```
