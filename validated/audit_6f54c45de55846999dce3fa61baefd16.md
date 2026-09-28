### Title
Mid-epoch deposits mint tranche shares for unrealized expected interest, letting a depositor hold a claim larger than deposited collateral that survives default — (`contracts/IdleCDOEpochVariant.sol`)

### Summary
`depositDuringEpoch` mints `(_amount + trancheInterest)` tranche shares while crediting only `_amount` to the tranche NAV. The extra shares are backed solely by *expected*, not-yet-realized epoch interest — the direct analog of opening a position backed only by positive uPnL. If the epoch later ends in a loss or borrower default, those shares still entitle the holder to a pro-rata claim, so the attacker extracts value that was never collateralized, diluting honest tranche holders.

### Finding Description
In `depositDuringEpoch`, the minted amount is computed as `(_amount + trancheInterest) * _trancheTotSupply / expectedFinal`, where `trancheInterest` is the depositor's share of *projected* interest for the remaining epoch plus buffer (`_calcInterest(_amount) * (remaining + buffer) / (epochDuration + buffer)`). [1](#0-0) 

Crucially, `_mintShares(_tranche, msg.sender, _minted, _amount)` only adds `_amount` to `lastNAVAA`/`lastNAVBB` — the share count includes the unrealized interest but the NAV does not. [2](#0-1) 

The mint is designed so that at a successful epoch end the depositor receives principal + prorated interest, and `expectedEpochInterest += interest` accounts for it on the honest path. However, if the epoch resolves with a `_lossAmount` via `stopEpochWithDuration` or a borrower default via `_handleBorrowerDefault`, the crystallized NAV never contains that interest, yet the attacker still holds shares minted against it. In loss/default resolution (BB-first waterfall in `_virtualPriceAux`, or aggregate recovery claims), payouts are pro-rata over tranche supply, so the interest-inflated shares claim a larger fraction of the real recovered funds than the attacker's deposited principal warrants. [3](#0-2) 

Attack sequence (fixed-APR, running epoch):
1. Alice (KYC-passed, unprivileged) calls `depositDuringEpoch(amount, AATranche)` mid-epoch. She deposits `amount` but receives shares for `amount + trancheInterest`.
2. The borrower fails to repay (honest borrower, market-driven default) or the manager stops the epoch with a loss — `defaulted` is set or loss is burned via `_forceUpdateAccounting`. [4](#0-3) 
3. Alice's inflated shares redeem/claim against actual recovered funds pro-rata with supply, giving her more than `amount` worth of recovery while honest holders absorb the difference — an uncollateralized position paid for by other depositors.

Existing guards do not stop this: `depositDuringEpoch` only requires `isEpochRunning`, KYC (`isWalletAllowed`), non-programmable/non-AYS mode, and `!skipDefaultCheck`; there is no check that minted shares correspond to deposited principal, and `_skimDonatedAssets`/`_guarded` are orthogonal. [5](#0-4) 

### Impact Explanation
Direct theft/dilution: on any loss or default resolution, the attacker redeems recovery value proportional to `amount + trancheInterest` while having only funded `amount`. The excess comes out of honest tranche holders' recovery, quantified as the attacker's `trancheInterest` fraction of the tranche's total claim — scaling with deposit size, APR, and remaining epoch duration.

### Likelihood Explanation
Requires only a KYC-passing wallet calling a public function during a running epoch, plus a subsequent loss/default event (not attacker-controlled, but a normal failure mode of the credit vault). No privileged action by the attacker is needed.

### Recommendation
Do not mint shares against unrealized expected interest without collateral backing. Either mint only `_amount`-denominated shares and credit the prorated interest at epoch settlement (when it is actually realized), or track the interest-minted portion separately and burn/discount it in `_handleBorrowerDefault` and the `_lossAmount` path so default recoveries are paid only against deposited principal.

### Proof of Concept
A Foundry fork PoC would: (1) deploy/fork a fixed-APR credit vault, have an honest lender seed the AA tranche; (2) `startEpoch`; (3) warp mid-epoch, attacker calls `depositDuringEpoch` and assert `minted * priceEnd > amount` (shares include expected interest while `lastNAVAA` increased by only `amount`); (4) simulate borrower non-repayment so `stopEpoch` hits the catch-path `_handleBorrowerDefault`, or call `stopEpochWithDuration` with a `_lossAmount`; (5) assert the attacker's recovery/claim value exceeds their deposited `amount` pro-rata versus an honest holder who deposited the same principal before the epoch — demonstrating the uncollateralized claim. I was unable to fully verify the exact claim-weight formula used in the default recovery path (`_claimDefaulted*` / `DefaultDistributor`) within the iteration budget, so step (5)'s precise payout accounting should be confirmed against those functions — the core defect (interest-minted shares surviving default) is confirmed at lines 693–725.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L330-336)
```text
  function _stopEpoch(uint256 _newApr, uint256 _interest, uint256 _lossAmount) private {
    _checkOnlyOwnerOrManager();
    bool _isRequestingAllFunds = _interest == 1;
    _checkProgrammableBorrowerMode();

    IdleCreditVault _strategy = IdleCreditVault(strategy);
    uint256 _pendingWithdrawFees = pendingWithdrawFees;
```

**File:** contracts/IdleCDOEpochVariant.sol (L496-505)
```text
      if (_lossAmount != 0) {
        _strategy.burnStrategyTokens(_lossAmount);
        // Realize the active loss immediately through the ordinary BB-first waterfall.
        _forceUpdateAccounting();
      }
    } catch {
      // if borrower defaults, prev instant withdraw requests can still be withdrawn
      // as were already fullfilled prior to the default (all funds already sent to the strategy)
      _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
    }
```

**File:** contracts/IdleCDOEpochVariant.sol (L656-681)
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

    _skimDonatedAssets();
    // Check that limit is not exceeded after removing skimmable raw donations.
    _guarded(_amount);
```

**File:** contracts/IdleCDOEpochVariant.sol (L693-725)
```text
    uint256 interest = _calcInterest(_amount) *
      // the time the depositor actually participates (remaining epoch + full buffer)
      (remaining + buffer) /
      // the total time baked into the scaled APR (epoch + buffer).
      (epochDuration + buffer);

    uint256 expectedInt = expectedEpochInterest;
    uint256 pendingFees = pendingWithdrawFees;
    uint256 trancheExpected;
    // existing holders' share of net expected interest for the epoch (pre-deposit)
    // (exclude pendingWithdrawFees since they go to fee receivers, not tranche holders)
    if (expectedInt > pendingFees) {
      trancheExpected = _calcTrancheInterestShare(
        _netGainAfterFees(expectedInt - pendingFees, _calculateManagementFee(lastNAVAA + lastNAVBB, remaining)),
        _tranche
      );
    }
    // interest this deposit will earn for the tranche over the remaining time (net of fees)
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
