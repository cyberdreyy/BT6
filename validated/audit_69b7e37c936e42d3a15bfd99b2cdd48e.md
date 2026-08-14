### Title
Bank interest-rate cache not refreshed when admin updates `interest_rate_config` - ([File: programs/marginfi/src/instructions/marginfi_group/configure_bank_lite.rs])

### Summary
This is the direct analog of the reported bug class: a setter mutates a config value (`royaltyBps` in the report; `interest_rate_config` here) that determines a separately-stored derived/cached value (`ERC2981` internal royalty state vs. `Bank.cache.base_rate/lending_rate/borrowing_rate`), but the setter never refreshes that cache, so the two representations of the same logical value become inconsistent.

### Finding Description
`lending_pool_configure_bank_interest_only` (and, likewise, `lending_pool_configure_bank`'s normal path via `Bank::configure`) mutates `bank.config.interest_rate_config` directly and returns without calling `Bank::update_bank_cache`: [1](#0-0) 

Compare this with `Bank::update_bank_cache`, which is the function responsible for recomputing `cache.base_rate`, `cache.lending_rate`, and `cache.borrowing_rate` from the current `interest_rate_config` and utilization: [2](#0-1) [3](#0-2) 

Other instructions that change bank/share state (deposits, withdrawals, borrows, repays, `lending_pool_emissions_deposit`) explicitly call `bank.update_bank_cache(&group)` after touching balances, e.g.: [4](#0-3) 

But neither `lending_pool_configure_bank_interest_only` nor `lending_pool_configure_bank` (`configure_bank.rs`, calling `bank.configure(&bank_config)`) invoke this refresh after changing the curve/fee parameters that feed the cached rates. The documentation itself confirms `bank.cache` is meant to reflect the bank's "last spot interest rate," updated "any time a Bank has a balance change, or [when someone sends] a (permissionless) accrue interest instruction": [5](#0-4) 

### Impact Explanation
After an admin changes `interest_rate_config` (curve points, fixed/variable fee components, `zero_util_rate`/`hundred_util_rate`), `bank.cache.base_rate/lending_rate/borrowing_rate` continue to report the pre-update rates until some other state-changing instruction on that bank (deposit/withdraw/borrow/repay/`accrue_bank_interest`) incidentally calls `update_bank_cache`. Any consumer that reads `bank.cache` directly to display or act on the bank's current rate (front-ends, integrators, off-chain risk/rate systems) — exactly the class of "external reader relies on a cached/derived value" impact described in the report — will observe values inconsistent with the just-updated `interest_rate_config` until the next incidental refresh. Unlike core accrual math (which recomputes from `interest_rate_config` directly via `create_interest_rate_calculator`, not from the cache), this does not directly cause a fund-safety issue in the on-chain accounting path, which limits severity relative to the original ERC2981 report (where `royaltyInfo` is the sole source of truth used to route real payments).

### Likelihood Explanation
Likely to occur any time a curve/fee admin updates `interest_rate_config` on a bank that has been quiet for a while, since the stale window persists only until the next balance-changing or `accrue_bank_interest` transaction touches that specific bank — for illiquid or low-activity banks this window can be non-trivial.

### Recommendation
Call `bank.update_bank_cache(&group)` (loading the relevant `MarginfiGroup`) at the end of `lending_pool_configure_bank_interest_only` and at the end of the non-frozen branch of `lending_pool_configure_bank`, mirroring the pattern already used in `lending_pool_emissions_deposit`, so the cache is never left stale relative to the just-applied config.

### Proof of Concept
1. Bank B has been idle (no deposits/withdrawals/borrows/repays) for some time, so `B.cache.{base_rate,lending_rate,borrowing_rate}` reflect the old `interest_rate_config`.
2. `delegate_curve_admin` calls `lending_pool_configure_bank_interest_only` to change `zero_util_rate`, `hundred_util_rate`, and curve `points` (e.g., significantly raising rates) — see [6](#0-5) .
3. `bank.config.interest_rate_config` is updated on-chain, but `bank.cache.base_rate/lending_rate/borrowing_rate` are untouched.
4. Any reader/integration that queries `B.cache` for the "current" rate (per the documented contract in `README.md`) sees the stale, incorrect rate until the next incidental instruction on `B` invokes `update_bank_cache`.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/configure_bank_lite.rs (L12-35)
```rust
pub fn lending_pool_configure_bank_interest_only(
    ctx: Context<LendingPoolConfigureBankInterestOnly>,
    interest_rate_config: InterestRateConfigOpt,
) -> MarginfiResult {
    let mut bank = ctx.accounts.bank.load_mut()?;
    msg!(
        "Configuring bank: {:?} mint: {:?}",
        ctx.accounts.bank.key(),
        bank.mint
    );

    // If settings are frozen, interest rates can't update.
    if bank.get_flag(FREEZE_SETTINGS) {
        msg!("WARN: Bank settings frozen, did nothing.");
    } else {
        bank.config
            .interest_rate_config
            .update(&interest_rate_config);
        bank.config.interest_rate_config.validate()?;
        msg!("Bank configured!");
    }

    Ok(())
}
```

**File:** programs/marginfi/src/state/bank.rs (L625-659)
```rust
    /// Updates bank cache with the actual values for interest/fee rates.
    ///
    /// Should be called in the end of each instruction calling `accrue_interest` to ensure the cache is up to date.
    ///
    /// # Arguments
    /// * `group` - The marginfi group
    fn update_bank_cache(&mut self, group: &MarginfiGroup) -> MarginfiResult<()> {
        if self.cache.is_liquidation_price_cache_locked() {
            return Ok(());
        }
        let total_assets_amount: I80F48 = self.get_asset_amount(self.total_asset_shares.into())?;
        let total_liabilities_amount: I80F48 =
            self.get_liability_amount(self.total_liability_shares.into())?;

        if (total_assets_amount == I80F48::ZERO) || (total_liabilities_amount == I80F48::ZERO) {
            self.cache.reset_preserving_oracle_price();
            return Ok(());
        }

        let ir_calc = self
            .config
            .interest_rate_config
            .create_interest_rate_calculator(group);

        let utilization_rate: I80F48 = total_liabilities_amount
            .checked_div(total_assets_amount)
            .ok_or_else(math_error!())?;
        let interest_rates = ir_calc.calc_interest_rate(utilization_rate)?;

        update_interest_rates(&mut self.cache, &interest_rates);

        // Update banks last update timestamp
        self.last_update = Clock::get()?.unix_timestamp;
        Ok(())
    }
```

**File:** programs/marginfi/src/state/bank_cache.rs (L1-8)
```rust
use fixed::types::I80F48;
use marginfi_type_crate::types::{milli_to_u32, BankCache};

pub fn update_interest_rates(bank_cache: &mut BankCache, interest_rates: &ComputedInterestRates) {
    bank_cache.base_rate = milli_to_u32(interest_rates.base_rate_apr);
    bank_cache.lending_rate = milli_to_u32(interest_rates.lending_rate_apr);
    bank_cache.borrowing_rate = milli_to_u32(interest_rates.borrowing_rate_apr);
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/configure_bank.rs (L138-150)
```rust
    let total_assets = bank.get_asset_amount(total_asset_shares)?;
    let updated_total_assets = total_assets
        .checked_add(I80F48::from_num(amount))
        .ok_or_else(math_error!())?;

    bank.asset_share_value = updated_total_assets
        .checked_div(total_asset_shares)
        .ok_or_else(math_error!())?
        .into();

    bank.update_bank_cache(&group)?;

    msg!(
```

**File:** README.md (L119-121)
```markdown
You can read a Bank's last spot interest rate from `bank.cache`. This updates any time a Bank has a
balance change, or send a (permissionless) accrue interest instruction to force it to update.

```
