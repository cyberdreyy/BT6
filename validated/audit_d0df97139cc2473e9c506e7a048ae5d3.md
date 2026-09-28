### Title
Donation-Inflated Tranche Price Causes Zero-Share Mints and Theft of Subsequent Deposits - ([File: contracts/IdleCDOCreditVault.sol](contracts/IdleCDOCreditVault.sol))

### Summary
The external bug class — an excessively large price/divisor driving a fixed-point division to zero and minting zero shares — has a direct analog in `IdleCDOCreditVault`. `_updateAccounting` values the vault with `getContractValue()`, which includes the **raw underlying (`token`) balance held by the contract** (line 127). An unprivileged attacker can therefore inflate `priceAA`/`priceBB` by directly transferring `token` to the vault, so that the share-mint formula `_amount * ONE_TRANCHE_TOKEN / _tranchePrice` (line 346) rounds down to `0` for the next depositor. The depositor's funds are absorbed into NAV while they receive no tranche tokens, and the attacker redeems their shares to capture the donated principal plus the victim's deposit.

### Finding Description
`_managedContractValue()` (lines 133-137) deliberately excludes raw `token` donations ("unsolicited transfers are skimmed"), but the accounting path that actually sets prices — `_updateAccounting` (line 227) — calls `getContractValue()` (lines 125-128), which returns `strategyToken balance + token balance - unclaimedFees`. So a direct `token` transfer to the CDO **does** enter `nav`, produces a positive `totalGain` in `_virtualPriceAux`, and (when the attacker holds the only tranche supply) is attributed entirely to that tranche, raising `priceAA`/`priceBB` at lines 336 / 247-248.

Attack sequence (buffer/running phase, KYC-passed attacker is just a normal lender):

1. Attacker is the first depositor: `depositAA(1)` → mints `1 * ONE_TRANCHE_TOKEN / oneToken` shares at the initial `oneToken` price; `lastNAVAA = 1`.
2. Attacker donates `D` underlying directly to the vault (plain `ERC20.transfer`), e.g. `D = 1000e6` USDC.
3. Victim calls `depositAA(V)`. Inside `_deposit`, `_updateAccounting` runs first: `nav = strategyTokens + tokenBal - fees` now includes the donation; `_virtualPriceAux` sets `priceAA ≈ (1 + D) * ONE_TRANCHE_TOKEN / supply` — arbitrarily large as `D` grows.
4. `_mintSharesAtCurrPrice(V, victim, AATranche)` computes `V * ONE_TRANCHE_TOKEN / priceAA`. For `priceAA > V * ONE_TRANCHE_TOKEN` this truncates to `0` — the victim's `V` tokens are pulled in (`_transferUnderlyingsFrom`), pushed to the strategy (line 211), `lastNAVAA += V`, but `IdleCDOTranche.mint(victim, 0)` gives them nothing.
5. Attacker calls `withdrawAA`/redeems their tranche tokens; `toRedeem` is computed against the full NAV including the victim's deposit, transferring the victim's principal to the attacker.

The minted amount is never checked for `> 0` in `_deposit`/`_mintSharesAtCurrPrice`, and `_tranchePrice` only falls back to `oneToken` when *supply* is zero (lines 422-427) — here supply is non-zero, so the inflated stored price is used. The Default/`_checkDefault` path does not apply since NAV increases.

### Impact Explanation
Direct theft of user funds: every subsequent depositor whose deposit amount satisfies `V < priceAA / ONE_TRANCHE_TOKEN` (attacker-tunable via donation size `D`) mints zero shares and permanently loses their full deposit to existing tranche holders — i.e., the attacker. Broken invariant: fair mint/burn and solvency of NAV-vs-supply (NAV grows without corresponding shares). With a 6-decimal underlying like USDC, donating ~`1e12` units makes any deposit below ~`1e6 * 1e18 / priceAA` mint zero shares; the attacker's cost is recoverable because the donation itself is redeemable through their tranche position, netting them the victim's principal minus fees.

### Likelihood Explanation
- Attacker needs only to be a normal depositor (KYC lender) plus an unprivileged direct token sender — both explicitly allowed attacker roles.
- First-depositor positioning is required (attacker holds the tranche supply), which is realistic for a newly deployed credit vault or after all other holders of a tranche class exit.
- Caveat: if a nonzero `limit` in `GuardedLaunchUpgradable._guarded` caps contract value, a very large donation may push `getContractValue()` over the cap and revert the victim's deposit (`AmountTooHigh`) — that yields DoS-of-deposits rather than theft, but attacker can tune `D` below the cap and still zero out small/medium deposits, or the vault may run with `limit = 0` (unlimited, per the initialize comment).
- The gap between `_managedContractValue` (donation-skimming, used by `virtualPrice`) and `getContractValue` (donation-including, used by `_updateAccounting`) is the root inconsistency.

### Recommendation
- Make `_updateAccounting` (and every NAV-consuming path) use `_managedContractValue()` so raw underlying donations cannot move tranche prices; sweep/skimming of unsolicited `token` should be handled explicitly.
- Revert in `_mintSharesAtCurrPrice` (or `_deposit`) when `_minted == 0` for a nonzero `_amount`, so a rounding-to-zero mint cannot silently absorb user funds.
- Optionally seed a minimum initial tranche supply / dead-share reserve at initialization to make price inflation attacks economically infeasible.

### Proof of Concept
Foundry fork PoC sketch (against `IdleCDOCreditVault`/`IdleCDOEpochVariant` deployment with USDC-like underlying, `limit = 0`):

```solidity
// test/foundry/TranchePriceDonation.t.sol
function test_DonationInflatesPrice_ZeroShareMint() public {
    // attacker = KYC'd lender; victim = normal user
    uint256 D = 1_000_000e6;   // donation, attacker-funded
    uint256 V = 100e6;         // victim deposit

    // 1. attacker first deposit -> 1 wei worth of AA shares
    deal(address(token), attacker, 1 + D);
    vm.startPrank(attacker);
    token.approve(address(cdo), 1);
    cdo.depositAA(1);                 // mints shares at oneToken
    token.transfer(address(cdo), D);  // raw underlying donation
    vm.stopPrank();

    // 2. victim deposits; _updateAccounting inflates priceAA via getContractValue()
    deal(address(token), victim, V);
    vm.startPrank(victim);
    token.approve(address(cdo), V);
    uint256 minted = cdo.depositAA(V);
    vm.stopPrank();

    // 3. victim received zero shares; NAV absorbed their deposit
    assertEq(minted, 0);
    assertEq(IdleCDOTranche(cdo.AATranche()).balanceOf(victim), 0);

    // 4. attacker redeems and profits (their claim covers victim's principal)
    vm.prank(attacker);
    cdo.withdrawAA(IdleCDOTranche(cdo.AATranche()).balanceOf(attacker));
    assertGt(token.balanceOf(attacker), 1 + D); // recaptured donation + victim's V
}
```

Notes on residual uncertainty: whether the concrete deployed variant (`IdleCDOEpochVariant`) routes deposits through `_deposit` such that `getContractValue()` is the NAV source on the interaction path, and whether a skim of unsolicited `token` exists there — the comments in `IdleCDOCreditVault` reference skimming but `_updateAccounting` in this file demonstrably uses the donation-inclusive `getContractValue()` at line 227, while only the `virtualPrice` view uses `_managedContractValue()`. If the epoch variant's request/claim flow computes NAV exclusively via `_managedContractValue`, the donation vector narrows to strategy-token donations (only viable if the strategy token is a transferable ERC4626 share the attacker can acquire), but the zero-share mint at line 346 remains unguarded either way.