### Title
`depositDuringEpoch` mints tranche shares priced on assumed full-epoch interest; a lower realized stop-epoch interest crystallizes un-backed shares and dilutes LPs - ([File: contracts/IdleCDOEpochVariant.sol])

### Summary
`IdleCDOEpochVariant.depositDuringEpoch` mints tranche tokens using a projected end-of-epoch price that *assumes* the borrower pays the full fixed-APR interest for the remaining epoch plus the entire buffer period (`trancheInterest` at lines 711–714, mint at line 724). This is the same bug class as the Balancer `getBPTExpected` report: a deposit/withdraw sizing calculation that bakes in an assumption about realized value (there: 1:1 ETH parity and fresh oracle prices; here: that the configured scaled APR will actually be realized for the full remaining `epochDuration + bufferPeriod`). When the epoch is later stopped with a lower realized `_interest` override or a `_lossAmount` via `stopEpoch`/`stopEpochWithDuration` — both normal, honest manager operations — the excess minted shares are never clawed back. The attacker holds a claim on interest that was never earned, which is paid out of the NAV owed to honest tranche holders.

### Finding Description
The mint math is [1](#0-0) :

```
trancheInterest = _calcTrancheInterestShare(_netGainAfterFees(interest, mgmtFee), _tranche)
expectedFinal   = _lastSavedNAV(_tranche) + trancheExpected   // projected NAV at epoch end
_minted         = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal
```

`interest = _calcInterest(_amount) * (remaining + buffer) / (epochDuration + buffer)` is a *projection* at the current scaled APR [2](#0-1) . The deposited underlyings are forwarded to the borrower and strategy tokens are minted 1:1 with principal only (`mintStrategyTokens(_amount)`, line 730) — the `trancheInterest` component of the minted shares has no backing unless the epoch actually realizes it.

Realized interest is fixed at `stopEpoch` time: `_interest` is resolved via `_resolveStopEpochInterest`/`prepareStopEpochWithApr0` and an explicit `_interest > 1` override is supported (with `_newApr == 0`), and `stopEpochWithDuration` additionally accepts `_lossAmount` burned against active basis [3](#0-2) , [4](#0-3) . Nothing reconciles the already-minted shares with the realized outcome. After `_updateAccounting`/`_forceUpdateAccounting`, the shortfall is socialized across all holders of that tranche through the NAV waterfall [5](#0-4) .

Concretely, if realized epoch interest is `I_real < I_expected`, the attacker's fair mint would have been `_amount * supply / actualFinal`, but they hold `(_amount + trancheInterest) * supply / expectedFinal`. Since `expectedFinal > actualFinal` by the tranche's share of the missing interest while the numerator still contains the full `trancheInterest`, the attacker owns a strictly larger supply fraction than honest accounting permits. They convert it via `requestWithdraw`/`claimWithdrawRequest` in the next buffer at the post-stop `tranchePrice`.

The only guards are `isDepositDuringEpochDisabled` (a flag, explicitly settable to false by design), `skipDefaultCheck`, KYC `isWalletAllowed` (a KYC-passing lender is an in-scope attacker), and the `_trancheSupply != 0` check [6](#0-5) . None of them stops the over-mint.

### Impact Explanation
Direct theft / broken fair-mint invariant: the attacker extracts up to `trancheInterest` worth of underlying per mid-epoch deposit from the tranche's NAV, paid by honest AA/BB holders (BB-first in the loss case). For a 20%-max-APR vault, depositing late in an epoch still embeds roughly `amount * APR * (remaining + buffer) / (epoch + buffer)` of un-backed claim; against a 30d/5d epoch a deposit at mid-epoch carries ~2% of principal in phantom interest, and repeated deposits compound the drain. If the epoch is instead defaulted/finalized, the inflated shares also enlarge the attacker's `activeBasis` recovery claim in `finalizeDefaultRecovery` [7](#0-6) , stealing recovery reserve from other claimants.

### Likelihood Explanation
Requires `isDepositDuringEpochDisabled == false` (supported mode) and a subsequent epoch stop whose realized interest is below the projection — an ordinary event: `stopEpoch` supports interest overrides precisely because realized yield can differ, and `stopEpochWithDuration(_lossAmount)` exists for realized losses. Any KYC'd lender can be the depositor; the triggering `stopEpoch` call is by the honest manager, so no privileged misbehavior is needed. Demand deposits made early in the epoch maximize the embedded phantom interest.

### Recommendation
- Mint mid-epoch deposits at the *current* tranche price for principal only (`_amount * supply / lastNAV`-equivalent, i.e. `_mintSharesAtCurrPrice`), and let the depositor accrue interest naturally through NAV growth — this removes the assumed-yield term from the mint entirely.
- Alternatively, escrow the `trancheInterest` portion of minted shares (or mint them to the contract) and release only the fraction matching realized interest at `stopEpoch`, burning the rest in the same transaction that finalizes interest.
- If the projected-price design is retained, cap `remaining` accrual to realized-time at stop and reconcile over-mints in `stopEpoch`/`stopEpochWithDuration` before `_updateAccounting` crystallizes prices.

### Proof of Concept
Reproducible Foundry fork PoC (structure; extend `test/foundry/IdleCreditVault.t.sol` mid-epoch deposit helpers):

```solidity
function testDepositDuringEpochOvermintOnLowerInterest() public {
    // Running epoch: epochDuration=30d, buffer=5d, APR>0, isDepositDuringEpochDisabled=false
    uint256 snap = vm.snapshot();

    // Honest AA holders already in; tranche supply > 0, lastNAVAA = N
    vm.warp(epochStart + 10 days); // mid-epoch, remaining=20d
    uint256 amt = 1_000_000e6;
    deal(USDC, attacker, amt);
    vm.startPrank(attacker); // KYC-passed lender
    IERC20(USDC).approve(address(cdo), amt);
    uint256 minted = cdo.depositDuringEpoch(amt, AATranche); // minted includes trancheInterest
    vm.stopPrank();

    // Fair mint at realized-zero-extra-interest final price would be amt*supply/actualFinal;
    // assert minted > that value:
    uint256 fairMint = amt * AASupplyBefore / lastNAVAA_before;
    assertGt(minted, fairMint);

    // Epoch ends; borrower returns principal but manager stops with lower realized
    // interest (or a loss) — honest call.
    vm.warp(epochEndDate + 1);
    vm.prank(manager);
    cdo.stopEpoch(newApr, interestOverrideLessThanExpected); // or stopEpochWithDuration(..., loss)

    // Accounting crystallizes: attacker redeems inflated shares.
    vm.prank(attacker);
    cdo.requestWithdraw(0, AATranche);   // receipt priced at post-stop tranchePrice
    // ... next epoch stop, then:
    vm.prank(attacker);
    cdo.claimWithdrawRequest();

    uint256 attackerOut = IERC20(USDC).balanceOf(attacker);
    assertGt(attackerOut, amt);          // profited despite no corresponding yield
    // Honest holders' claimable NAV decreased by ~= trancheInterest (BB-first if loss).
}
```

The assertion `attackerOut > amt` while realized per-share yield was below the projection demonstrates the fair-mint invariant break; the magnitude equals the unearned `trancheInterest` (minus fees) extracted from the tranche NAV.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L330-351)
```text
  function _stopEpoch(uint256 _newApr, uint256 _interest, uint256 _lossAmount) private {
    _checkOnlyOwnerOrManager();
    bool _isRequestingAllFunds = _interest == 1;
    _checkProgrammableBorrowerMode();

    IdleCreditVault _strategy = IdleCreditVault(strategy);
    uint256 _pendingWithdrawFees = pendingWithdrawFees;

    _checkNotAllowed(
      // Check that epoch is running
      !isEpochRunning || 
      // Check that end date is passed
      block.timestamp < epochEndDate || 
      // Check that there are no pending instant withdraws, ie `getInstantWithdrawFunds` was called
      // before closing the epoch
      _pendingInstant() != 0 ||
      // Check that overridden interest, if passed (ie > 1), is greater than pending withdraw fees and the apr is 0 
      // otherwise withdrawal requests may not be fullfilled as they consider also the interest gained in the next epoch 
      (_interest > 1 && (_interest < _pendingWithdrawFees || _newApr != 0)) ||
      // Closing already recalls all principal, so applying a separate loss burn would strand returned cash.
      (_isRequestingAllFunds && _lossAmount != 0)
    );
```

**File:** contracts/IdleCDOEpochVariant.sol (L393-399)
```text
    (_pendingWithdraws, _lossAmount) = _strategy.previewLossAdjustedWithdrawFunds(_lossAmount);

    if (isProgrammableBorrower) {
      // Ask the programmable borrower to recall ERC4626 liquidity before IdleCDO pulls funds.
      // Hook reverts bubble so transient ERC4626 liquidity failures can be retried.
      if (!IProgrammableBorrower(_borrower()).onStopEpoch(_amountToPullFromBorrower + _pendingWithdraws, _isRequestingAllFunds)) {
        // Emit the exact cash liability requested from the borrower, including recalled principal
```

**File:** contracts/IdleCDOEpochVariant.sol (L656-677)
```text
  function depositDuringEpoch(uint256 _amount, address _tranche) external virtual returns (uint256 _minted) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == BBTranche && !isBBDepositEnabled) ||
      isDepositDuringEpochDisabled ||
      skipDefaultCheck ||
      // programmable borrowers use APR=0 so mid-epoch deposits would dilute existing depositors
      isProgrammableBorrower ||
      // check if AYS is active as we don't support deposits during epoch in that case
      isAYSActive ||
      // check if epoch is still running even if not manually stopped yet
      !isEpochRunning || block.timestamp >= epochEndDate ||
      !isWalletAllowed(msg.sender)
    );

    if (_amount == 0) {
      return _minted;
    }

    uint256 _trancheTotSupply = _trancheSupply(_tranche);
    // Avoid pricing discontinuities for the first mid-epoch deposit in a tranche
    _checkNotAllowed(_trancheTotSupply == 0);
```

**File:** contracts/IdleCDOEpochVariant.sol (L691-697)
```text
    uint256 buffer = bufferPeriod;
    uint256 remaining = epochEndDate - block.timestamp;
    uint256 interest = _calcInterest(_amount) *
      // the time the depositor actually participates (remaining epoch + full buffer)
      (remaining + buffer) /
      // the total time baked into the scaled APR (epoch + buffer).
      (epochDuration + buffer);
```

**File:** contracts/IdleCDOEpochVariant.sol (L711-725)
```text
    uint256 trancheInterest = _calcTrancheInterestShare(
      _netGainAfterFees(interest, _calculateManagementFee(_amount, remaining)),
      _tranche
    );
    // pre-deposit expected final NAV for existing holders.
    // This won't ever be zero as we checked _trancheTotSupply and we seed initial NAV at tranche creation
    uint256 expectedFinal = _lastSavedNAV(_tranche) + trancheExpected;

    // mint at a discounted price so depositor gets principal + its prorated interest at epoch end
    // A mid‑epoch depositor should get _amount + trancheInterest at epoch end.
    // So they need minted = (amount + trancheInterest) / priceEnd.
    // priceEnd = expectedFinal / _trancheTotSupply
    // so minted = (amount + trancheInterest) * _trancheTotSupply / expectedFinal
    _minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal;
    _mintShares(_tranche, msg.sender, _minted, _amount);
```

**File:** contracts/IdleCDOCreditVault.sol (L306-330)
```text
    int256 totalGain = int256(_nav) - int256(_lastNAV);
    // Ordinary zero-delta interactions keep their saved price for compatibility. Forced
    // accounting recomputes NAV per share, which is required after discounted mid-epoch deposits.
    if (totalGain == 0 && !skipDefaultCheck) return (_tranchePrice(_tranche), 0);

    // Remove performance fee for gains
    if (totalGain > 0) {
      totalGain -= totalGain * int256(fee) / int256(FULL_ALLOC);
    }

    bool _isAATranche = _tranche == AATranche;
    // A class with no saved NAV cannot be revived by later gains. If only this class has saved
    // NAV, it receives the full gain or loss; otherwise both classes participate.
    if (_lastTrancheNAV == 0) {
      _totalTrancheGain = 0;
    } else if (_lastNAV == _lastTrancheNAV) {
      _totalTrancheGain = totalGain;
    } else {
      if (totalGain > 0) {
        // Split the net gain, according to _trancheAPRSplitRatio, with precision loss favoring the AA tranche.
        int256 totalBBGain = totalGain * int256(FULL_ALLOC - _trancheAPRSplitRatio) / int256(FULL_ALLOC);
        // The new NAV for the tranche is old NAV + total gain for the tranche
        _totalTrancheGain = _isAATranche ? (totalGain - totalBBGain) : totalBBGain;
      } else {
        int256 maxBBLoss = -int256(lastNAVBB);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L674-705)
```text
    uint256 activeBalance = balanceOf(idleCDO);
    uint256 activeInterest = _defaultActiveInterestBasis(cdo);
    uint256 activeBasis = activeBalance + activeInterest;
    defaultBBNav = _defaultBBBasis(cdo, activeBalance, activeInterest);
    // Pending receipts have already left active CDO NAV, so they are added as a separate basis.
    uint256 pendingBasis = defaultPendingClaimBasis();
    uint256 totalBasis = activeBasis + pendingBasis;
    if (totalBasis == 0) revert NotAllowed();

    // Some recovery funds may already be in this strategy: partially prefunded instant requests
    // and borrower-send funds that failed at epoch start. Count both without pulling them again.
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
    // Bring active CDO NAV to the same recovery ratio. IdleCDOEpochVariant then calls
    // _forceUpdateAccounting so tranche prices/virtualPrice expose the crystallized loss.
    uint256 activeFinalNAV = (activeBasis * recoveryPrice) / RECOVERY_FULL;
    defaultBBNav = defaultBBNav * recoveryPrice / RECOVERY_FULL;
    if (activeBalance > activeFinalNAV) {
      _burn(idleCDO, activeBalance - activeFinalNAV);
    } else if (activeFinalNAV > activeBalance) {
      _mint(idleCDO, activeFinalNAV - activeBalance);
    }
```
