### Title
ERC4626 vault donation inflates `totalInterestDueNow` and mints unbacked epoch interest to tranche holders — (`contracts/strategies/idle/ProgrammableBorrower.sol`)

### Summary
The HotCRP bug is unsanitized user input flowing into a code-generation "formula" that produces attacker-chosen behavior. The analog here is attacker-controlled input flowing into the interest "formula" `totalInterestDueNow()`: `_vaultNetInterest()` derives epoch profit from the external ERC4626 vault's `convertToAssets()` share price, and any user of that vault can inflate the share price by donating underlying directly to the vault. In minted-interest mode, `IdleCDOEpochVariant._stopEpoch` then calls `mintStrategyTokens(_grossInterest)` for phantom interest and raises AA/BB tranche prices, letting an attacker LP redeem at an inflated price and steal real pool/borrower funds.

### Finding Description
`ProgrammableBorrower._vaultNetInterest` computes epoch interest as:

```
earnedAssets = _currentVaultAssets() + epochWithdrawnFromVault
netDelta     = bufferedVaultDelta + earnedAssets - (epochStartVaultAssets + epochDepositedToVault)
```

where `_currentVaultAssets() = vault.convertToAssets(vault.balanceOf(this))` [1](#0-0) [2](#0-1) .

`convertToAssets` is a vault-global share price. An attacker who is a user of the same ERC4626 vault (an explicitly allowed unprivileged role) donates `D` underlying directly to the vault. That raises `convertToAssets` for the ProgrammableBorrower's shares without touching `epochDepositedToVault`, so the entire `D`-proportional increase is read as epoch profit by `totalInterestDueNow` [3](#0-2) .

`IdleCDOEpochVariant._stopEpoch` resolves this value into `_expectedInterest` and, in minted-interest mode (`isInterestMinted && !_isRequestingAllFunds`), mints `_grossInterest` strategy tokens and distributes fees/tranche value at the inflated NAV via `_updateAccounting()` and `_mintSharesAtCurrPrice` [4](#0-3) [5](#0-4) . The donation guard `_skimDonatedAssets()` only skims tokens sitting on the CDO contract itself; it does nothing about a donation inside the external vault [6](#0-5) . There is no deposit isolation: `onStartEpoch` snapshots the baseline before re-depositing, but a mid-epoch vault donation is indistinguishable from yield [7](#0-6) .

### Impact Explanation
Attacker sequence (running epoch, minted-interest mode):

1. During buffer, deposit into the credit vault as a KYC'd LP (attacker also holds shares of the external ERC4626 vault).
2. Mid-epoch, transfer `D` underlying directly into the ERC4626 vault contract, inflating its share price.
3. After `epochEndDate`, `stopEpoch(0,0)` runs (honest manager call): `totalInterestDueNow` reports phantom gain ≈ `D * borrowerShares / totalVaultShares`; `mintStrategyTokens` mints unbacked strategy tokens; AA/BB prices rise.
4. Attacker redeems tranche tokens at the inflated price or claims against the minted NAV, extracting real underlying funded by pending withdraws, other LPs, or the CDO's fronted interest.

Broken invariant: fair mint/burn / donation isolation — epoch "interest" is created with no underlying backing. In cash mode the same phantom `_expectedInterest` is pulled from the borrower via `getFundsFromBorrower`, directly overcharging the facility. Quantified loss ≈ the donated amount's pro-rata effect on the borrower's share position (attacker recovers most of the donation as a vault shareholder, so net cost is small relative to extracted LP value).

### Likelihood Explanation
Requires a programmable-borrower deployment with `isInterestMinted` or cash-settled epochs and an ERC4626 vault whose `convertToAssets` is donation-sensitive (typical totalAssets-based vaults). The attacker needs only to be an unprivileged vault user plus a tranche holder — both allowed roles. No privileged collusion needed; only that manager calls `stopEpoch` on schedule.

### Recommendation
Track vault profit in shares/assets actually moved rather than global `convertToAssets`: e.g., record `epochStartVaultShares` and compute net interest from share-balance changes at a fixed entry price, or credit interest only from realized `withdraw`/`redeem` amounts (extend `epochWithdrawnFromVault` accounting and drop the mark-to-market delta). Alternatively, bound recognized interest by `maxApr` unconditionally, or sanity-cap `_grossInterest` against time-based expected interest in `_resolveStopEpochInterest`.

### Proof of Concept
Foundry fork PoC sketch (mainnet fork, e.g. a Morpho/Gauntlet USDC vault as in `test/foundry/ProgrammableBorrowerCreditVault.t.sol`):

```solidity
// Setup: programmable-borrower CDO, isInterestMinted = true, attacker deposits AA.
cdoEpoch.setIsInterestMinted(true);
idleCDO.depositAA(10_000e6);            // attacker LP
_startEpoch(0);                          // onStartEpoch deposits into ERC4626 vault

// Mid-epoch: attacker donates D underlying directly to the external vault
uint256 sharesHeld = vault.balanceOf(address(programmableBorrower));
uint256 priceBefore = vault.convertToAssets(sharesHeld);
deal(USDC, attacker, 1_000e6);
USDC.transfer(address(vault), 1_000e6);  // donation, no shares minted
uint256 phantom = vault.convertToAssets(sharesHeld) - priceBefore;
assertGt(phantom, 0);

// priceAA/priceBB already elevated via totalInterestDueNow -> expectedEpochInterest
vm.warp(cdoEpoch.epochEndDate() + 1);
cdoEpoch.stopEpoch(0, 0);                // mints phantom interest

// attacker redeems AA at inflated NAV, extracting > deposited+real yield
uint256 p = cdoEpoch.virtualPrice(address(aaTranche));
idleCDO.withdrawAA(0);                   // proceeds > share of real assets
```

Expected: `lastEpochInterest` includes `phantom` with no cash backing; `virtualPrice` inflated; attacker's AA redemption exceeds proportional share of `(contractValue - phantom)`, leaving remaining LPs and pending withdraw requests undercollateralized.

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L213-221)
```text
    // exact pre-deposit amount instead of a post-deposit share-conversion round-down.
    uint256 startAssets = underlyingToken.balanceOf(address(this)) + currentVaultAssets;
    // Any idle balance left on the contract between epochs is parked immediately into the vault.
    _depositToVault(underlyingToken.balanceOf(address(this)), 0);
    // Reset the epoch accounting baseline from the exact pre-start total assets so the new epoch
    // does not treat the just-deposited idle balance as fresh vault profit or loss.
    epochStartVaultAssets = startAssets;
    epochDepositedToVault = 0;
    epochWithdrawnFromVault = 0;
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L330-334)
```text
  function totalInterestDueNow() external view returns (uint256) {
    (uint256 vaultInterest, uint256 loss) = _vaultNetInterest();
    uint256 totalGain = vaultInterest + borrowerInterestAccruedNow() + bufferInterest;
    return totalGain > loss ? totalGain - loss : 0;
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L336-347)
```text
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

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L545-549)
```text
  /// @notice Convert the current vault share position into underlying terms.
  function _currentVaultAssets() internal view returns (uint256) {
    uint256 shares = vault.balanceOf(address(this));
    return shares == 0 ? 0 : vault.convertToAssets(shares);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L354-355)
```text
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();
```

**File:** contracts/IdleCDOEpochVariant.sol (L373-379)
```text
    uint256 _grossInterest = _isRequestingAllFunds ? _expectedInterest - _totBorrowed : _expectedInterest;
    bool _mintInterest = isInterestMinted && !_isRequestingAllFunds;
    // In minted mode IdleCDO only needs cash for withdraw requests, not for epoch interest itself.
    uint256 _amountToPullFromBorrower = _mintInterest ? 0 : _expectedInterest;
    if (_mintInterest && _interest > 1) {
      uint256 _maxApr = _strategy.maxApr();
      _checkNotAllowed(_maxApr != 0 && _grossInterest > _calcInterestWithApr(getContractValue(), _maxApr) + _pendingWithdrawFees);
```

**File:** contracts/IdleCDOEpochVariant.sol (L428-436)
```text
      if (_mintInterest) {
        // if interest is not transferred we mint strategy tokens equal to the full epoch interest
        if (_grossInterest != 0) _strategy.mintStrategyTokens(_grossInterest);
        // and increase unclaimedFees by pending withdraw fees before _updateAccounting
        unclaimedFees += _pendingWithdrawFees;
      }

      // update tranche prices and unclaimed fees
      _updateAccounting();
```
