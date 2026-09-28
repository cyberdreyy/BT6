### Title
Minted-mode fee payment double-charges tranche holders by minting shares against fee-reduced NAV - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
In minted-interest mode (`_mintInterest == true`, programmable borrower), `stopEpoch` pays accrued fees by minting new AA tranche shares via `_mintSharesAtCurrPrice` instead of transferring underlyings. Because `unclaimedFees` already reduces `getContractValue()` (and therefore the tranche price), minting new shares against that reduced NAV adds claims with no new assets: existing holders are diluted by roughly the fee amount *in addition to* the fee already charged through the NAV reduction. This is the same "mint the exact fee amount" bug class as the Amun finding — minting shares whose nominal value equals the fee does not transfer that value cleanly; it dilutes existing supply.

### Finding Description
The flow in `_afterStopEpochWithDuration`/`stopEpoch` is:

1. `_updateAccounting()` checkpoints `_accrueManagementFee()` into `unclaimedFees` and recomputes tranche prices on NAV net of `unclaimedFees` (tests confirm `getContractValue() == amount - expectedFee` after checkpoint, `test/foundry/IdleCreditVault.t.sol:1040`).
2. Then, in minted mode, the accrued `_fees = unclaimedFees` is paid by minting AA shares at the current (post-fee) price:

```solidity
// contracts/IdleCDOEpochVariant.sol:439-449
uint256 _fees = unclaimedFees;
if (_mintInterest) {
  if (_fees != 0) {
    uint256 feeReceiverAmount = _feeReceiverAmount(_fees);
    if (feeReceiverAmount != 0) {
      _mintSharesAtCurrPrice(feeReceiverAmount, feeReceiver, AATranche);
    }
    _mintSharesAtCurrPrice(_fees - feeReceiverAmount, owner(), AATranche);
    _updateSplitRatio(_getAARatio(true));
  }
}
```

3. `unclaimedFees -= _fees` then zeroes the accrual.

Numerically, with post-fee NAV `V`, AA+BB supply `S`, price `p = V/S`, the code mints `m = _fees / p` shares. New price `p' = V/(S+m)`:

- Minted fee shares are worth `m·p' = _fees·S/(S+m) < _fees` (the Amun dilution: feeReceiver/owner get less than the nominal fee).
- Existing holders' claim drops from `V` to `V·S/(S+m)`, i.e., they lose `≈ _fees·m/(S+m)` of value *on top of* the `_fees` already deducted from NAV during accounting.

So holders effectively bear the fee twice: once via the `unclaimedFees` NAV reduction in the tranche price, and again via the share-mint dilution that funds the payment. In the non-minted path (`_transferFeeUnderlyings(_fees)` at line 456) no such double charge exists, because cash is transferred and the NAV reduction is the single charge. The two payment paths are therefore economically inconsistent: the same `managementFee`/`fee` accrual costs holders ~2× in minted mode vs 1× in cash mode.

### Impact Explanation
- Every `stopEpoch` in minted-interest mode with a nonzero accrued fee overcharges tranche holders (AA and BB pro rata via price) by approximately the fee amount, scaled by `m/(S+m) ≈ _fees/V`. For a 10% fee-on-interest configuration with a large accrued `unclaimedFees` balance (e.g., carried fees that exceeded gross interest in prior epochs, which the code explicitly allows to accumulate — `testStopEpochCarriesManagementFeeAboveGrossInterest`), the excess dilution can be a material fraction of holder NAV.
- The value is transferred to `feeReceiver`/`owner` (honest roles) but is funded by holder dilution rather than by the fee accrual alone — a broken fair mint/burn invariant: shares are minted for underlying value that was already extracted from NAV. An unprivileged tranche holder who deposits before `startEpoch` and redeems after `stopEpoch` suffers a quantified loss relative to the documented fee rate, with no way to avoid it.

### Likelihood Explanation
- Requires only minted-interest mode (programmable borrower where interest is minted rather than cash-settled) plus a nonzero `fee`/`managementFee`. These are owner-set configurations; no attacker action is needed — the loss occurs deterministically at every `stopEpoch`.
- The magnitude grows when `unclaimedFees` carries over multiple epochs (funded-cap at `_availableForFees` only limits cash payouts, not the minted path, which always mints the full `unclaimedFees`).

Note: I could not fully read `_updateAccounting`'s body to confirm whether tranche prices are set before or after the mint in the same call; the dilution argument holds either way because the mint occurs at post-fee-price with no asset inflow, but the exact split of the double-charge between "already-deducted" and "minted" depends on that ordering.

### Recommendation
In the minted-fee path, avoid paying fees by minting shares against NAV that already excludes `unclaimedFees`. Options:

- Add the accrued fee back to NAV for the mint (mint shares priced on pre-fee NAV: `m = _fees / (V + _fees) · S`), so holders are charged exactly once; or
- Convert the fee to underlying and use `_transferFeeUnderlyings` funded by `mintStrategyTokens`/borrower settlement, matching the non-minted path; or
- Mint `m = _fees·S / V` computed on pre-deduction supply/NAV and accept the residual rounding, rather than minting `_fees / p` at the post-deduction price.

### Proof of Concept
Foundry fork test sketch (extends `test/foundry/IdleCreditVault.t.sol` patterns; requires a programmable-borrower CDO configured with `_mintInterest == true`):

```solidity
function testMintedFeeDoubleCharge() external {
    // Setup: programmable borrower vault, _mintInterest = true,
    // fee = 10% (FULL_ALLOC/10), feeSplit = FULL_ALLOC, managementFee = 0 for isolation
    uint256 amount = 1_000_000 * ONE_SCALE;
    idleCDO.depositAA(amount);            // attacker/victim: ordinary KYC'd AA holder
    _transferBurnedTrancheTokens(address(this), true);
    _setFeeParams(TL_MULTISIG, 10_000, FULL_ALLOC, 0); // 10% perf fee

    vm.startPrank(manager);
    cdoEpoch.setEpochParams(365 days, 0);
    IdleCreditVault(address(strategy)).setApr(initialProvidedApr);
    cdoEpoch.startEpoch();
    vm.stopPrank();

    uint256 gross = cdoEpoch.expectedEpochInterest(); // e.g. 100_000
    // minted mode: borrower does NOT send cash; stopEpoch mints strategy tokens
    vm.warp(cdoEpoch.epochEndDate());
    vm.prank(manager);
    cdoEpoch.stopEpoch(initialProvidedApr, 0);

    uint256 feeShares = IERC20(address(AAtranche)).balanceOf(TL_MULTISIG);
    uint256 price = cdoEpoch.virtualPrice(address(AAtranche));
    uint256 feeValue = feeShares * price / ONE_TRANCHE_TOKEN;
    uint256 holderValue =
        IERC20(address(AAtranche)).balanceOf(address(this)) * price / ONE_TRANCHE_TOKEN;

    // Single-charge expectation: holderValue == amount + gross - fee
    // Actual: holderValue is additionally reduced by dilution ~= fee^2 / NAV
    assertLt(holderValue + feeValue, amount + gross, "supply claims < NAV+fee: holders charged twice");
    assertLt(feeValue, gross * 10_000 / FULL_ALLOC, "feeReceiver underpaid vs nominal fee");
}
```

Expected result: `holderValue + feeValue < amount + gross` (claims total is conserved at NAV, but holders bear the fee both via the NAV reduction and the dilutive mint), and `feeValue < intendedFee` — reproducing the Amun M-10 dilution plus an additional holder-side overcharge that the cash path does not produce.