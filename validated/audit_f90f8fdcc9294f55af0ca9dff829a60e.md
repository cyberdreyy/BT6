### Title
Donated `strategyToken` counted at par in NAV with no skim, enabling tranche-price inflation and withdrawal against other lenders' liquidity - (File: contracts/IdleCDOCreditVault.sol)

### Summary
The Summer.fi exploit was a NAV-donation attack: a stale, effectively illiquid position remained priced into the vault's `totalAssets()`, the attacker donated the asset to push reported NAV up, then redeemed shares against other depositors' real liquidity.

The analog in this repo is `IdleCDOCreditVault.getContractValue()` / `_managedContractValue()` and `IdleCDOEpochVariant._skimDonatedAssets()`. The contract deliberately protects itself against raw-`token` donations by skimming the underlying balance to `feeReceiver` and by using `_managedContractValue()` (strategy-token-only) for `virtualPrice`. But donated `strategyToken` (the IdleCreditVault ERC4626 share token, which an unprivileged user of the programmable borrower's vault can legitimately hold and transfer) is counted directly at a hardcoded 1:1 par in both NAV paths and is never skimmed. A direct `strategyToken.transfer(cdo, amount)` permanently inflates NAV, `virtualPrice`, `lastNAVAA/BB`, and therefore the tranche price at which `requestWithdraw`/withdrawal claims are paid.

### Finding Description
- `getContractValue()` returns `_contractTokenBalance(strategyToken) + _contractTokenBalance(token) - unclaimedFees` — strategy-token balance is summed at face value with no price or recoverability check (`IdleCDOCreditVault.sol:125-128`).
- `_managedContractValue()` — the NAV used by `virtualPrice()` — is `_contractTokenBalance(strategyToken) - unclaimedFees` (`IdleCDOCreditVault.sol:133-137`). Any unsolicited `strategyToken` transfer raises it.
- `_skimDonatedAssets()` only sweeps `token` (`IdleCDOEpochVariant.sol:794-796`). There is no equivalent skim for `strategyToken`, so a donated share balance persists across every subsequent `_updateAccounting()`.
- `_updateAccounting()` computes `nav = getContractValue()` and splits `nav - lastNAV` as gain between AA and BB via `_virtualPriceAux`, raising `priceAA`/`priceBB` and `lastNAVAA`/`lastNAVBB` (`IdleCDOCreditVault.sol:222-248`). Because `_deposit` calls `_updateAccounting()` *before* pulling the user's funds, the donation is baked into the price the attacker then redeems at.
- The credit-vault strategy token is `IIdleCDOStrategy(strategy).strategyToken()`, an ERC4626 share of `IdleCreditVault`. The threat model explicitly includes "a user of the programmable borrower's ERC4626 vault" — such a user can hold and freely transfer these shares.

Attack sequence (epoch vault, buffer phase or running epoch where requests are enabled):
1. Attacker deposits into the CDO (`depositAA`) acquiring a large fraction of tranche supply, or flash-funds a deposit.
2. Attacker acquires `strategyToken` directly on the `IdleCreditVault` ERC4626 vault (a permitted unprivileged user).
3. Attacker calls `strategyToken.transfer(address(cdo), Y)` — no minted tranche shares, pure NAV push (mirrors the Silo donation in the Summer.fi exploit).
4. Next interaction (`_updateAccounting`) counts +Y as gain; `priceAA`/`priceBB` rise.
5. Attacker calls `requestWithdraw` and later claims at the inflated `tranchePrice`, paid from the epoch's real liquidity (borrower repayment / buffer funds) — other lenders' capital.

The economics are profitable when the strategy-token shares can be obtained below the par at which the CDO values them — e.g., a mid-epoch discounted `depositDuringEpoch`, residual shortfall, or any discount on the ERC4626 secondary value — because the CDO marks them 1:1 with `token` unconditionally (comment at `IdleCDOCreditVault.sol:126`: "strategy tokens are minted 1:1 with underlyings"), with no `vaultLoss`-style markdown applied to balances it did not mint itself.

### Impact Explanation
Broken invariant: donation isolation / fair mint-burn. Donated `strategyToken` raises `virtualPrice` and withdrawal payouts without adding claimable liquidity — the donated shares are stuck in the CDO with no removal path (analogous to the Ark that could never be emptied). Withdrawal requests are honored at the inflated `tranchePrice` out of real borrower liquidity, directly transferring value from honest lenders to the attacker; residual holders are left with shares backed by unredeemable donated strategy tokens. Loss scales with donation size × attacker share of supply; a ≥50%-supply attacker recovers nearly the full donated par value plus the real-discount spread.

### Likelihood Explanation
Requires only unprivileged actions: holding tranche tokens, holding/transferring ERC4626 strategy shares, and calling `requestWithdraw`. No privileged role, oracle, or reentrancy needed. The guard that exists (`_skimDonatedAssets`) provably covers only `token`, and the dedicated tests (`testDonatedUnderlyingDoesNotChangeVirtualPrice`) only exercise underlying donations — strategy-token donation is unprotected. Caveat I could not fully verify within the search budget: exact `strategyToken` transfer restrictions in `IdleCreditVault` and whether any entry point sweeps stray strategy-token balances — if `strategyToken` is non-transferable or skimmed elsewhere, severity drops.

### Recommendation
Treat `strategyToken` like `token`: track the internally-expected strategy-token balance (minted minus redeemed) rather than reading `balanceOf`, or extend `_skimDonatedAssets` to forward `strategyTokenBalance - expectedStrategyTokenBalance` to `feeReceiver`. Alternatively apply a `vaultLoss`/price-based valuation to the strategy-token balance instead of assuming par.

### Proof of Concept
```solidity
// Foundry fork test sketch (test/foundry/IdleCreditVault.t.sol harness)
function testStrategyTokenDonationInflatesTranchePrice() external {
    uint256 amount = 10_000 * ONE_SCALE;
    idleCDO.depositAA(amount);                       // attacker+others hold AA at priceAA = 1e18

    uint256 pricePre = cdoEpoch.virtualPrice(address(AAtranche));

    // acquire strategyToken as an unprivileged ERC4626 user of IdleCreditVault
    uint256 donation = 1_000 * ONE_SCALE;
    deal(defaultUnderlying, attacker, donation);
    vm.startPrank(attacker);
    underlying.approve(address(creditVault), donation);
    uint256 shares = creditVault.deposit(donation, attacker); // ERC4626 shares
    // donate shares directly to the CDO -- no skim exists for strategyToken
    IERC20(address(cdoEpoch.strategyToken())).transfer(address(cdoEpoch), shares);
    vm.stopPrank();

    // NAV and price inflated by the par-valued donation
    assertGt(cdoEpoch._managedContractValue(), amount - feesBefore);
    vm.roll(block.number + 1);
    idleCDO.depositAA(ONE_SCALE);                    // any interaction bakes the gain
    assertGt(cdoEpoch.tranchePrice(address(AAtranche)), pricePre);

    // attacker redeems pre-donation shares at the inflated price,
    // paid from borrower/buffer liquidity, leaving donated illiquid shares behind
    uint256 requested = cdoEpoch.requestWithdraw(attackerShares, address(AAtranche));
    assertGt(requested, attackerPrincipal);
}
```