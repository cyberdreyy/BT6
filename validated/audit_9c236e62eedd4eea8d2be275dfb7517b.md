## Analog Found: Receivership liquidation health check uses stale interest-accrued share values

### Title
Receivership liquidation (`start_liquidation`/`start_deleverage`) computes account health from stale bank share values, permitting delayed liquidations - (File: `programs/marginfi/src/instructions/marginfi_account/liquidate_start.rs`)

### Summary
marginfi values every position by multiplying a user's shares by the bank's `asset_share_value` / `liability_share_value`. These share values only advance when `Bank::accrue_interest` runs, which happens inside instructions like `borrow`, `withdraw`, `deposit`, `repay`, and classic `lending_account_liquidate` — each of which explicitly calls `accrue_interest` on the relevant bank(s) before computing health. However, `start_receivership` (the shared logic behind the permissionless `start_liquidation` and admin `start_deleverage` instructions) computes account health directly via `check_pre_liquidation_condition_and_get_account_health` / `get_health_components` without ever calling `accrue_interest` on the account's banks first.

### Finding Description
`Bank::accrue_interest` is the only function that advances `asset_share_value`/`liability_share_value` to reflect elapsed time and interest owed [1](#0-0) . Health/value calculations read these cached share values directly via `get_asset_amount`/`get_liability_amount` [2](#0-1) , and `get_health_components` uses exactly this path per balance [3](#0-2) .

Classic liquidation (`lending_account_liquidate`) is careful to accrue interest on both the asset and liability banks immediately before computing pre-liquidation health: [4](#0-3) . Likewise `borrow` and `withdraw` call `bank.accrue_interest(...)` before their health checks [5](#0-4) [6](#0-5) .

By contrast, `start_receivership` — invoked by both permissionless `start_liquidation` and risk-admin `start_deleverage` — calls `check_pre_liquidation_condition_and_get_account_health` and `get_health_components` directly on the account's banks with no preceding `accrue_interest` call anywhere in the function: [7](#0-6) . This means the liability-side `liability_share_value` used to compute `liabs`/`liabs_equity` (and the maintenance/equity snapshots persisted into `liq_record.cache`) reflects only interest accrued as of whichever bank instruction last touched that specific bank — not the current timestamp.

The allowed pre-instructions for a `start_liquidation`/`start_deleverage` transaction are limited to Kamino refresh, Drift/Juplend rate-update CPIs, and `INIT_LIQUIDATION_RECORD` [8](#0-7)  — marginfi's own permissionless `lending_pool_accrue_bank_interest` instruction is not in that allow-list, so a liquidator cannot force-refresh the bank's interest within the same atomic liquidation transaction.

### Impact Explanation
If a bank with meaningful borrows has gone some time without any deposit/withdraw/borrow/repay/liquidate transaction touching it, `liability_share_value` is stale and understates the true debt owed by borrowers on that bank. `start_receivership`'s health computation will then overstate account health (assets − liabilities), potentially reporting a genuinely underwater account as healthy and reverting with `HealthyAccount`, exactly the "late liquidation" impact class described in the source report. This delays liquidation of insolvent positions, increasing bad-debt risk and, in aggregate, protocol insolvency risk — matching the original report's High severity rationale for using a maintenance-checked net-value/health figure computed from stale fee/interest state.

### Likelihood Explanation
`start_liquidation` is a fully permissionless instruction usable by any liquidator, and staleness naturally occurs on any bank with low transaction frequency (the same condition the project's own documentation acknowledges: "banks without much activity may have stale numbers until someone interacts with them" [9](#0-8) ). No privileged access or unusual conditions are required — only that the liability bank hasn't been touched recently, which is a normal, easily arranged state for a liquidator/attacker who wants to delay their own liquidation or influence receivership economics.

### Recommendation
Call `bank.accrue_interest(...)` (and `update_bank_cache`) for every distinct bank referenced by the account's active balances inside `start_receivership`, before calling `check_pre_liquidation_condition_and_get_account_health`/`get_health_components`, mirroring the pattern already used in `lending_account_liquidate`, `borrow`, and `withdraw`.

### Proof of Concept
1. Deposit collateral and borrow from `Bank B` in account `A`.
2. Let sufficient time pass so `A`'s true (interest-inclusive) liability exceeds its maintenance-weighted collateral, but do not send any deposit/withdraw/borrow/repay/liquidate/accrue instruction touching `Bank B` in the interim (so `B.last_update`/`liability_share_value` remain stale).
3. Call `start_liquidation` against account `A`: `start_receivership` computes health using `B`'s stale `liability_share_value`, which is lower than the true, time-accrued value.
4. If the discrepancy is large enough, `check_pre_liquidation_condition_and_get_account_health` reports `healthy == true` and reverts with `MarginfiError::HealthyAccount` [10](#0-9) , blocking liquidation of an account that is actually insolvent under current interest.

### Citations

**File:** programs/marginfi/src/state/bank.rs (L231-241)
```rust
    fn get_liability_amount(&self, shares: I80F48) -> MarginfiResult<I80F48> {
        Ok(shares
            .checked_mul(self.liability_share_value.into())
            .ok_or_else(math_error!())?)
    }

    fn get_asset_amount(&self, shares: I80F48) -> MarginfiResult<I80F48> {
        Ok(shares
            .checked_mul(self.asset_share_value.into())
            .ok_or_else(math_error!())?)
    }
```

**File:** programs/marginfi/src/state/bank.rs (L511-528)
```rust
    fn accrue_interest(
        &mut self,
        current_timestamp: i64,
        group: &MarginfiGroup,
        #[cfg(not(feature = "client"))] bank: Pubkey,
    ) -> MarginfiResult<()> {
        #[cfg(all(not(feature = "client"), feature = "debug"))]
        sol_log_compute_units();

        let time_delta: u64 = (current_timestamp - self.last_update).try_into().unwrap();
        if time_delta == 0 {
            return Ok(());
        }

        let total_assets = self.get_asset_amount(self.total_asset_shares.into())?;
        let total_liabilities = self.get_liability_amount(self.total_liability_shares.into())?;

        self.last_update = current_timestamp;
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L944-959)
```rust
    let account_health = assets.checked_sub(liabs).ok_or_else(math_error!())?;
    let healthy = account_health > I80F48::ZERO;

    if let Some(cache) = health_cache.as_mut() {
        cache.set_healthy(healthy);
    }

    if healthy && !ignore_healthy {
        msg!(
            "pre_liquidation_health: {} ({} - {})",
            account_health,
            assets,
            liabs
        );
        return err!(MarginfiError::HealthyAccount);
    }
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L1322-1328)
```rust
            let value = calc_value(
                bank.get_asset_amount(balance.asset_shares.into())?,
                lower_price,
                bank.get_balance_decimals(),
                Some(asset_weight),
            )?;

```

**File:** programs/marginfi/src/instructions/marginfi_account/liquidate.rs (L153-167)
```rust
    let group = &*marginfi_group_loader.load()?;
    {
        ctx.accounts.asset_bank.load_mut()?.accrue_interest(
            current_timestamp,
            group,
            #[cfg(not(feature = "client"))]
            ctx.accounts.asset_bank.key(),
        )?;
        ctx.accounts.liab_bank.load_mut()?.accrue_interest(
            current_timestamp,
            group,
            #[cfg(not(feature = "client"))]
            ctx.accounts.liab_bank.key(),
        )?;
    }
```

**File:** programs/marginfi/src/instructions/marginfi_account/borrow.rs (L76-81)
```rust
    bank_loader.load_mut()?.accrue_interest(
        clock.unix_timestamp,
        &group,
        #[cfg(not(feature = "client"))]
        bank_loader.key(),
    )?;
```

**File:** programs/marginfi/src/instructions/marginfi_account/withdraw.rs (L98-103)
```rust
        bank.accrue_interest(
            clock.unix_timestamp,
            &group,
            #[cfg(not(feature = "client"))]
            bank_loader.key(),
        )?;
```

**File:** programs/marginfi/src/instructions/marginfi_account/liquidate_start.rs (L85-129)
```rust
pub fn start_receivership<'info>(
    marginfi_account: &mut MarginfiAccount,
    liq_record: &mut LiquidationRecord,
    remaining_ais: &'info [AccountInfo<'info>],
    ignore_healthy: bool,
) -> MarginfiResult {
    // Note: the receiver can use the health cache state after this ix concludes to plan their
    // liquidation/deleverage strategy.
    let mut health_cache = HealthCache::zeroed();
    let mut liq_price_cache = LiquidationPriceCache::default();
    let (_pre_health, assets, liabs) = check_pre_liquidation_condition_and_get_account_health(
        marginfi_account,
        remaining_ais,
        None,
        &mut Some(&mut health_cache),
        HealthPriceMode::Live {
            liq_cache: Some(&mut liq_price_cache),
        },
        ignore_healthy,
    )?;

    // Use heap-efficient equity calculation
    let (assets_equity, liabs_equity) = get_health_components(
        marginfi_account,
        remaining_ais,
        RequirementType::Equity,
        &mut Some(&mut health_cache),
        HealthPriceMode::Live {
            liq_cache: Some(&mut liq_price_cache),
        },
    )?;

    write_liquidation_price_cache_from(marginfi_account, remaining_ais, &liq_price_cache)?;
    marginfi_account.health_cache = health_cache;
    marginfi_account.set_flag(ACCOUNT_IN_RECEIVERSHIP, false);
    marginfi_account.indexer_flags.has_ever_been_liquidated = 1;

    // Snapshot values to use in later checks
    liq_record.cache.asset_value_maint = assets.into();
    liq_record.cache.liability_value_maint = liabs.into();
    liq_record.cache.asset_value_equity = assets_equity.into();
    liq_record.cache.liability_value_equity = liabs_equity.into();

    Ok(())
}
```

**File:** programs/marginfi/src/instructions/marginfi_account/liquidate_start.rs (L149-176)
```rust
    validate_ix_first(
        &ixes,
        program_id,
        start_ix,
        &[
            (
                kamino_mocks::kamino_lending::ID,
                kamino::RefreshReserve::DISCRIMINATOR,
            ),
            (
                kamino_mocks::kamino_lending::ID,
                kamino::RefreshReservesBatch::DISCRIMINATOR,
            ),
            (
                kamino_mocks::kamino_lending::ID,
                kamino::RefreshObligation::DISCRIMINATOR,
            ),
            (id_crate::ID, &ix_discriminators::INIT_LIQUIDATION_RECORD),
            (
                drift_mocks::drift::ID,
                drift::UpdateSpotMarketCumulativeInterest::DISCRIMINATOR,
            ),
            (
                juplend_mocks::juplend_earn::ID,
                juplend::UpdateRate::DISCRIMINATOR,
            ),
        ],
    )?;
```

**File:** guides/ADMIN/COLLECTING_FEES.md (L31-34)
```markdown
then the group gets 10% * 10% + 1% = 2% APR. Interest is only paid by borrowers, so banks without
borrowers (like Staked Collateral) never earn fees. Interest accumulates when any transaction that
changes funds occurs, so banks without much activity may have stale numbers until someone interacts
with them.
```
