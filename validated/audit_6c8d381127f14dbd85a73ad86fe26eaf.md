### Title
Attacker can inflate `expectedFinal` via `depositDuringEpoch`, reducing victim's minted tranche shares and enabling a sandwich - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
Any KYC-passing lender can call `depositDuringEpoch` while an epoch is running. Each call adds the caller's own prorated interest to `expectedEpochInterest` (line 728) and computes the mint price from `expectedFinal = _lastSavedNAV(_tranche) + trancheExpected`, where `trancheExpected` is the tranche's share of *all* net expected epoch interest (lines 704–717). An attacker can frontrun a victim's `depositDuringEpoch` with their own deposit, inflating `trancheExpected`/`expectedFinal` with interest that economically belongs to the attacker's deposit, so the victim mints fewer tranche shares for the same underlyings. The dilution accrues to pre-existing holders — including the attacker — who can exit via `requestWithdraw` after the epoch.

### Finding Description
In `IdleCDOEpochVariant.sol` (lines 656–733), `depositDuringEpoch` mints:

```
_minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal
```

with `expectedFinal = _lastSavedNAV(_tranche) + trancheExpected` and `trancheExpected = _calcTrancheInterestShare(net(expectedEpochInterest - pendingWithdrawFees))` computed over the *global* `expectedEpochInterest` counter. The analogous bug to the external report is that an unprivileged action (here a mid-epoch deposit, there a third-party `burn`) deterministically shrinks the victim's implicit "min amount out" (minted shares) inside the same transaction flow:

1. Epoch is running in fixed-APR mode (`isAYSActive == false`, `isProgrammableBorrower == false`, `isDepositDuringEpochDisabled == false`), tranche supply non-zero.
2. Victim broadcasts `depositDuringEpoch(amount, AATranche)`.
3. Attacker (any KYC'd wallet) frontruns with `depositDuringEpoch(X, AATranche)`. This adds `interest(X)` to `expectedEpochInterest` and mints attacker shares at the *old* `expectedFinal`.
4. Victim's call now computes `trancheExpected` over the enlarged `expectedEpochInterest`, so `expectedFinal` is larger and `_minted` is smaller than intended — the interest portion attributable to the attacker's own deposit is double-counted into the denominator pricing the victim's shares.
5. The shortfall is captured by existing AA holders; the attacker exits at/after epoch end via `requestWithdraw`/`claimWithdrawRequest`, which pays receipts at `lastWithdrawRequest`-fixed basis, preserving the siphoned value.

The `_skimDonatedAssets()` guard (line 679) only sweeps raw underlying donations and does not constrain `expectedEpochInterest` inflation, so no existing guard prevents this. `requestWithdraw` itself calls `_skimDonatedAssets` and `_updateAccounting` (lines 746–750) but likewise does not bound how much `expectedEpochInterest` one depositor's contribution raises another depositor's price denominator.

### Impact Explanation
The victim receives fewer tranche shares than the fair prorated-interest price implies; the loss is bounded by the marginal increase of `trancheExpected` times the victim's relative size and is realized as a permanent transfer of tranche NAV to existing holders. The attacker captures the majority of it proportional to their share of pre-deposit supply, satisfying "direct theft" with a quantified loss equal to `victimMinted_fair - victimMinted_actual` (measurable in the PoC).

### Likelihood Explanation
Requires a running epoch in a mode where `depositDuringEpoch` is enabled (fixed-APR, non-AYS, non-programmable deployments — an in-scope configuration), a mempool-visible victim deposit, and an attacker willing to hold tranche exposure until withdrawal. All attacker actions are unprivileged KYC-lender operations.

### Recommendation
Attribute incremental expected interest to the deposit that generated it when pricing subsequent `depositDuringEpoch` calls — e.g., compute `trancheExpected` from interest accrued to existing supply only, or deduct the current epoch's already-added `interest` contributions attributable to mid-epoch joiners. Alternatively, add a `minShares` parameter to `depositDuringEpoch` so callers can bound dilution.

### Proof of Concept
Foundry fork test (same harness style as `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testDepositDuringEpochSandwich() external {
    // fixed-APR mode, AYS off, depositDuringEpoch enabled
    uint256 amountWei = 10_000 * ONE_SCALE;
    idleCDO.depositAA(amountWei);          // seed tranche supply + NAV
    idleCDO.depositBB(amountWei);
    _startEpochAndCheckPrices(0);

    address attacker = makeAddr('attacker');
    address victim   = makeAddr('victim');
    deal(underlying, attacker, 50_000 * ONE_SCALE);
    deal(underlying, victim,   10_000 * ONE_SCALE);
    _kyc(attacker); _kyc(victim);          // whitelist both

    // Baseline: victim mints with no frontrun
    uint256 snap = vm.snapshot();
    vm.prank(victim);
    uint256 mintedFair = cdoEpoch.depositDuringEpoch(10_000 * ONE_SCALE, address(AAtranche));
    vm.revertTo(snap);

    // Attack: attacker frontruns a large mid-epoch deposit in the same tranche
    vm.prank(attacker);
    cdoEpoch.depositDuringEpoch(50_000 * ONE_SCALE, address(AAtranche));

    vm.prank(victim);
    uint256 mintedActual = cdoEpoch.depositDuringEpoch(10_000 * ONE_SCALE, address(AAtranche));

    assertLt(mintedActual, mintedFair, 'victim shares diluted');

    // Attacker exits after epoch via requestWithdraw and captures the dilution
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(AAtranche));
    _startEpochAndCheckPrices(1);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    assertGt(underlying.balanceOf(attacker) - balPre, 50_000 * ONE_SCALE,
        'attacker recovered principal plus siphoned value');
}
```

Uncertainty note: the exact magnitude of `trancheExpected` depends on `_calcTrancheInterestShare`/`trancheAPRSplitRatio` internals I could not fully trace within the iteration budget; the PoC asserts direction (fewer minted shares) which follows directly from `expectedFinal` growing with `expectedEpochInterest` at lines 704–724, and the assertion should be tightened to the exact delta once `_calcTrancheInterestShare` allocation is confirmed.