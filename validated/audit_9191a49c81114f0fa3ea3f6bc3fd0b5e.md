### Title
Interest accrued but not yet accounted is captured at full weight by depositors who enter just before accounting — no time-weighting of tranche deposits - ([File: contracts/IdleCDOCreditVault.sol](contracts/IdleCDOCreditVault.sol))

### Summary
`IdleCDOCreditVault` values tranches solely from the strategy-token and underlying balances it holds (`getContractValue`), and only materializes interest when the strategy mints new strategy tokens or accounting is updated. Because `virtualPrice`/`_virtualPriceAux` split all accrued gain at the moment of accounting without regard to when each tranche token was minted, an unprivileged user can deposit at the stale price immediately before yield is recognized and withdraw right after, capturing yield that accrued before they entered — the direct analog of a stale, non-decaying vote weight receiving full rewards.

### Finding Description
In `contracts/IdleCDOCreditVault.sol`:

- `getContractValue()` returns `_contractTokenBalance(strategyToken) + _contractTokenBalance(token) - unclaimedFees` (lines 125-128). Yield accruing inside the downstream `IdleCreditVault` is invisible to the CDO until the strategy mints additional `strategyToken`s (interest accrual is realized via `mintStrategyTokens`/`vaultInterestAccrued` at epoch stop/harvest in `contracts/strategies/idle/IdleCreditVault.sol`).
- `_deposit` calls `_updateAccounting()` and then mints shares at `_tranchePrice(_tranche)` via `_mintSharesAtCurrPrice` (lines 191-212, 344-348). Between accounting events, `priceAA`/`priceBB` are stale.
- `_virtualPriceAux` (lines ~280-336) splits `totalGain` between AA and BB purely by `trancheAPRSplitRatio` — there is no checkpoint, timestamp, or time-weighting of when shares were minted. A token minted one second before accounting receives the same claim on previously-accrued yield as a token held for months.

Exploit sequence:
1. Attacker (any KYC-passing lender) monitors accrued-but-unminted interest in the credit vault (e.g., `vaultInterestAccrued`/`totalInterestDueNow` growing during a running epoch).
2. Just before the borrower/manager's interest-minting or epoch-stop transaction (or before a harvest that will call `_updateAccounting` with a large positive `totalGain`), the attacker calls `depositAA`/`depositBB`, minting tranche tokens at the stale price.
3. The accounting transaction runs; `getContractValue` jumps by the accrued interest and `priceAA`/`priceBB` increase.
4. Attacker calls `withdrawAA`/`withdrawBB`, redeeming at the new price. `_checkSameBlock` only prevents deposit and withdrawal within the same block — it does not prevent deposit in block N and withdrawal in block N+1 after the accounting transaction.

### Impact Explanation
The attacker steals a pro-rata share of yield that accrued over the entire epoch before they deposited. With a sufficiently large deposit relative to existing TVL, the fraction of accrued interest captured approaches `attackerShares / totalShares`. The loss is borne by long-term AA/BB holders whose per-share yield is diluted. This breaks the fair mint/burn invariant: shares minted at a price that excludes already-earned yield are immediately redeemable at a price that includes it.

### Likelihood Explanation
Feasible wherever interest is accounted discretely (epoch stops via `stopEpoch`/interest minting, or periodic harvest-style calls) rather than streamed continuously into `strategyToken` value. The attacker needs only to sandwich the accounting transaction, which is permissionless to predict since interest accrual is observable on-chain. Constraints: the attacker must pass `isWalletAllowed`/KYC gating, must have capital for the deposit, and cannot act if deposits are paused or the epoch is in a phase where deposits are closed. I was unable to fully trace `IdleCreditVault`'s interest-minting entry points within this review, so the exact callable surface (which external function triggers the jump in `getContractValue`) should be confirmed, but the stale-pricing window in `_deposit`/`_mintSharesAtCurrPrice`/`getContractValue` is clearly present in the cited code.

### Recommendation
Time-weight the yield accrual rather than the deposit:
- Accrue interest continuously (e.g., update `priceAA`/`priceBB` or the implied strategy-token price on every interaction, or track `block.timestamp`-based interest since `latestHarvestBlock`, which is already repurposed as a fee checkpoint timestamp).
- Alternatively, mint `strategyToken`s for accrued interest before accepting deposits (i.e., refresh `getContractValue` inputs at the top of `_deposit`), so new shares are priced against realized NAV.
- Keep `_updateAccounting` before minting, but ensure the NAV it reads reflects interest up to the current block, eliminating the stale-price window.

### Proof of Concept
Foundry-style sketch against `IdleCDOCreditVault` + `IdleCreditVault` (mainnet/local fork with the strategy deployed):

```solidity
function test_StalePriceYieldCapture() public {
    // 1. Honest LP deposits 100k into AA during the running epoch
    cdo.depositAA(100_000e18);

    // 2. Epoch runs; credit vault accrues interest (e.g. 10k)
    //    interest is visible via vault vaultInterestAccrued() but
    //    NOT yet in cdo.getContractValue() -> priceAA still stale
    vm.warp(epochEnd - 1);

    uint256 priceBefore = cdo.tranchePrice(AA);

    // 3. Attacker deposits right before interest minting / stopEpoch
    uint256 shares = cdo.depositAA(1_000_000e18); // minted at stale price

    // 4. Manager/borrower tx materializes interest -> getContractValue jumps
    //    (strategy.mintStrategyTokens / stopEpoch path)
    vm.prank(manager);
    creditVault.stopEpochWithDuration(0, duration); // or equivalent interest mint

    uint256 priceAfter = cdo.tranchePrice(AA);
    assertGt(priceAfter, priceBefore);

    // 5. Attacker withdraws at new price (different block -> passes _checkSameBlock)
    vm.roll(block.number + 1);
    cdo.withdrawAA(shares);

    // Attacker profit = shares * (priceAfter - priceBefore) / 1e18
    // = attackerShareFraction * totalAccruedInterest
}
```