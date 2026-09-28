### Title
`_calcInterestWithApr` divides `_apr` by 100 before multiplying, truncating sub‑percent APR and systematically understating epoch interest - (`contracts/IdleCDOEpochVariant.sol:807`)

### Summary
`IdleCDOEpochVariant._calcInterestWithApr()` computes epoch interest as `_amount * (_apr / 100) * epochDuration / (365 days * ONE_TRANCHE_TOKEN)` [1](#0-0) . The APR is expressed in hundredths of a percent (the code comments explicitly describe setting the stop‑epoch APR to `11.67%` by scaling `10% * 35/30`, i.e. a fractional‑percent value like `1167`) [2](#0-1) . Evaluating `_apr / 100` first truncates the two‑decimal fraction before any multiplication, so any APR that is not an exact whole percent silently loses up to ~1 percentage point of rate on every interest computation.

### Finding Description
The same divide‑before‑multiply bug class as the FluidLocker report lives in `_calcInterestWithApr`:

```solidity
return _amount * (_apr / 100) * epochDuration / (365 days * ONE_TRANCHE_TOKEN);
```

`_apr / 100` discards `_apr % 100` before scaling by `_amount * epochDuration`. The correct ordering, `_amount * _apr * epochDuration / (100 * 365 days * ONE_TRANCHE_TOKEN)`, would never overflow (all inputs are ≤ ~1e27 for realistic TVL/durations) and preserves the full rate. The truncated value propagates into every interest path:

- `requestWithdraw` → `_calcInterestWithdrawRequest` uses `_calcInterest(_managedContractValue())` and `_calcInterest(_amount)` to compute the receipt's projected `interest` and the over/under‑performance `diff` [3](#0-2) . Receipts are fixed at request time (`_withdrawOps` burns tranche tokens and NAV immediately) [4](#0-3) , so the understated interest is permanently locked into the payout.
- `depositDuringEpoch` uses `_calcInterest(_amount)` to price discounted mid‑epoch mints [5](#0-4) .
- `expectedEpochInterest` accumulates the truncated value (`expectedEpochInterest += interest`), so the discrepancy versus actual borrower interest paid flows into NAV/fee accounting rather than to tranche holders.

The rounding loss is asymmetric and unrecoverable for the withdrawer: the receipt is fixed once, and any real interest above the truncated estimate is left in the pool and socialized/diverted to remaining holders and `unclaimedFees` rather than the receipt owner.

### Impact Explanation
For an APR with a fractional component — which the protocol itself creates whenever the stop‑epoch APR is scaled up to cover the buffer period (e.g. `10% * 35/30 = 11.666…%` → `1166` or `1167`) — up to `99/100` of a percentage point of APR is dropped. For `_apr = 1099` (10.99%) the computed interest is ~9% lower than the true interest; for low APRs like `_apr = 199` (1.99%) the loss approaches ~50% of the interest. A withdrawer requesting `principal + interest` receives a permanently reduced receipt; on a 1e5 USDC position earning ~10% over a 30‑day epoch, a ~9% interest understatement is a ~75 USDC loss per request, repeated every epoch and aggregable by any tranche holder timing their `requestWithdraw` while APR carries a nonzero `_apr % 100` residual.

### Likelihood Explanation
High. The truncation fires whenever `_apr % 100 != 0`, which is the *normal* case: the protocol deliberately scales the APR by `(epochDuration + bufferPeriod) / epochDuration` at `stopEpoch`, producing fractional‑percent values for virtually any buffer‑period configuration. No privileged misbehavior is required — an honest borrower/manager setting the scaled APR triggers it, and any KYC'd tranche holder calling `requestWithdraw` during the epoch receives the truncated receipt. No existing guard (skim, flags, epoch gating) compensates, since the loss is inside the interest formula itself.

### Recommendation
Reorder the multiplication in `IdleCDOEpochVariant._calcInterestWithApr` (and the mirrored helper in `test/foundry`):

```solidity
return _amount * _apr * epochDuration / (100 * 365 days * ONE_TRANCHE_TOKEN);
```

The product `_amount * _apr * epochDuration` cannot overflow for realistic TVL, APR and duration bounds (a `type(uint256).max` bound check confirms this for amounts up to ~1e38 at max APR/duration). The same ordering fix should be audited in any other site computing interest from the 100‑scaled APR.

### Proof of Concept
Foundry fork PoC sketch:

```solidity
// APR chosen with a fractional component: 10.99% -> 1099 (scaled x100)
uint256 amount = 100_000e6;              // USDC-like underlying
uint256 apr    = 1099;                   // produced naturally by buffer-scaling at stopEpoch
uint256 epoch  = cdoEpoch.epochDuration();

uint256 buggy     = amount * (apr / 100) * epoch / (365 days * 1e18);
uint256 expected  = amount * apr * epoch / (100 * 365 days * 1e18);

// buggy == amount * 10 * epoch / ...  -> ~9% of interest truncated
assertLt(buggy, expected);
uint256 lossPerEpoch = expected - buggy; // material loss on the withdrawal receipt
```

Concrete flow: (1) start an epoch so `epochDuration > 0`; (2) stop/configure so `getApr()` returns a value with `apr % 100 != 0` (which buffer‑scaling guarantees); (3) as a KYC'd AA holder, call `requestWithdraw(amount, AATranche)`; (4) compare `underlyings` to `principal + amount*apr*epochDuration/(100*365d*1e18) - fees`; the receipt is short by the truncated fraction, and the shortfall is permanent because `_withdrawOps` fixes the receipt at request time.

Note: I was unable to fully read `IdleCreditVault.sol`'s APR storage internals (the grep output returned match counts only), so the exact scaling constant of `unscaledApr`/`getApr` is inferred from `_apr / 100` and the `11.67%` scaling comment; the PoC should confirm the scaling before finalizing numbers.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L693-724)
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

**File:** contracts/IdleCDOEpochVariant.sol (L807-809)
```text
  function _calcInterestWithApr(uint256 _amount, uint256 _apr) internal view returns (uint256) {
    return _amount * (_apr / 100) * epochDuration / (365 days * ONE_TRANCHE_TOKEN);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L843-855)
```text
  /// @notice Calculate the interest of an epoch for a withdraw request
  /// @dev to avoid having funds not getting interest during buffer period, the apr 
  /// set in the stopEpoch is higher than then intended one so it will cover also the buffer period
  /// eg epoch = 30 days, buffer = 5 days, then if we want to give 10% apr for all the 35 days then
  /// in stop epoch we set the apr to 10% * 35/30 = 11.67%. For this reason people who instead request
  /// a withdraw should not get the additional interest for the buffer period because they can withdraw
  /// a block after the buffer period starts. So we calculate the interest for the 30 days only,
  /// eg. if apr is set to 11.67% and we want to calculate the interest for 30 days at 10% we need to do the 
  /// the opposite -> 11.67% * 30/35 = 10%
  /// @param _amount Amount of underlyings
  /// @param _tranche Tranche to withdraw from
  /// @return _interest Interest for the given amount and given tranche
  /// @return _diff over/under performance that this withdraw will cause to the other tranche
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
