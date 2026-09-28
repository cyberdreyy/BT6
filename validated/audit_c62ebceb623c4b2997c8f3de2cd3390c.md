### Title
Mid-epoch `setIsInterestMinted` toggle mints unbacked strategy tokens, crediting phantom epoch interest - (File: `contracts/IdleCDOEpochVariant.sol`)

### Summary
`setIsInterestMinted` can be flipped by the owner while an epoch is running because, unlike `setIsProgrammableBorrower` (which reverts when `isEpochRunning`), it performs no epoch-phase check. `_stopEpoch` reads `isInterestMinted` at stop time: `_mintInterest = isInterestMinted && !_isRequestingAllFunds` then sets `_amountToPullFromBorrower = 0` and mints `_grossInterest` strategy tokens instead of collecting real underlyings from the borrower [1](#0-0) . In an epoch that started in cash mode, the borrower was never charged interest and `sendInterestAndDeposits` moved only principal [2](#0-1) , so the minted strategy tokens are backed by nothing. `_updateAccounting` then distributes this phantom gain into `lastNAVAA`/`lastNAVBB` and tranche prices.

### Finding Description
- `setIsInterestMinted` only blocks turning the flag *off* for programmable borrowers; there is no `isEpochRunning` guard [3](#0-2) .
- When the flag is enabled mid-epoch, `stopEpoch` takes the minted path: no funds are pulled from the borrower, `_strategy.mintStrategyTokens(_grossInterest)` creates unbacked strategy tokens, and `unclaimedFees` plus tranche NAVs are credited as if the interest had been paid [4](#0-3) .
- `lastEpochInterest = netInterest` and `_setScaledApr` then carry the inflated basis forward, so subsequent epochs and `requestWithdraw`/`claimWithdrawRequest` payouts reference yield that has no underlying backing.

### Impact Explanation
The vault records `expectedEpochInterest` worth of gains that were never received. Tranche prices are inflated by exactly the epoch interest; an unprivileged KYC-passed lender who requests and claims withdrawal early redeems real underlyings, while the last claimers face insolvency of up to the full epoch interest (e.g., 10% APR on the pool NAV). The broken invariant is fair mint/burn and solvency: one receipt, one payout fails because aggregate claims exceed real assets by `_grossInterest`.

### Likelihood Explanation
Requires an honest owner/manager sequence: start a normal epoch, then call `setIsInterestMinted(true)` before `stopEpoch` — a plausible operational action when migrating the pool to minted-interest mode, since nothing signals it is unsafe mid-epoch. Low likelihood, high impact, matching the external report's profile.

### Recommendation
Revert in `setIsInterestMinted` when `isEpochRunning` (mirroring `setIsProgrammableBorrower`), or checkpoint/settle the epoch's accrued interest under the old mode before switching so `stopEpoch` pulls the real cash interest for the elapsed period.

### Proof of Concept
Fork-based Foundry PoC (against the existing `IdleCreditVault.t.sol` harness): deposit into AA/BB, `startEpoch()`, `vm.prank(owner) cdoEpoch.setIsInterestMinted(true)`, warp past `epochEndDate`, `stopEpoch(newApr, 0)` — assert borrower balance unchanged, strategy tokens minted equal `expectedEpochInterest`, and that the sum of `virtualPrice`-denominated claims exceeds vault real assets; a first user `requestWithdraw`+claim drains cash leaving later claimers unpayable.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L166-170)
```text
  function setIsInterestMinted(bool _isMinted) external {
    _checkOnlyOwner();
    _checkNotAllowed(!_isMinted && isProgrammableBorrower);
    isInterestMinted = _isMinted;
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L271-274)
```text
    uint256 _totEpochDeposits = _strategy.totEpochDeposits();
    // If interest is minted we do not transfer interest to the strategy
    uint256 _toSend = isInterestMinted ? _totEpochDeposits : lastEpochInterest + _totEpochDeposits;
    _strategy.sendInterestAndDeposits(_toSend);
```

**File:** contracts/IdleCDOEpochVariant.sol (L373-376)
```text
    uint256 _grossInterest = _isRequestingAllFunds ? _expectedInterest - _totBorrowed : _expectedInterest;
    bool _mintInterest = isInterestMinted && !_isRequestingAllFunds;
    // In minted mode IdleCDO only needs cash for withdraw requests, not for epoch interest itself.
    uint256 _amountToPullFromBorrower = _mintInterest ? 0 : _expectedInterest;
```

**File:** contracts/IdleCDOEpochVariant.sol (L428-447)
```text
      if (_mintInterest) {
        // if interest is not transferred we mint strategy tokens equal to the full epoch interest
        if (_grossInterest != 0) _strategy.mintStrategyTokens(_grossInterest);
        // and increase unclaimedFees by pending withdraw fees before _updateAccounting
        unclaimedFees += _pendingWithdrawFees;
      }

      // update tranche prices and unclaimed fees
      _updateAccounting();

      // transfer fees
      uint256 _fees = unclaimedFees;
      if (_mintInterest) {
        // If interest is minted then we mint new shares for fee receivers instead of transferring underlyings
        if (_fees != 0) {
          uint256 feeReceiverAmount = _feeReceiverAmount(_fees);
          if (feeReceiverAmount != 0) {
            _mintSharesAtCurrPrice(feeReceiverAmount, feeReceiver, AATranche);
          }
          _mintSharesAtCurrPrice(_fees - feeReceiverAmount, owner(), AATranche);
```
