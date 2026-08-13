### Title
Bank interest-rate/weight config updates skip `accrue_interest`, retroactively misapplying new terms over the stale accrual period - ([File: programs/marginfi/src/instructions/marginfi_group/configure_bank.rs])

### Summary
`lending_pool_configure_bank` and `lending_pool_configure_bank_interest_only` mutate `bank.config` (including `interest_rate_config`, asset/liability weights, etc.) directly on the `Bank` account without first calling `bank.accrue_interest()`. Because `Bank.last_update` is not advanced by these instructions, the next time interest is accrued (on any deposit/withdraw/borrow/repay/liquidate or the permissionless `lending_pool_accrue_bank_interest`), the *entire* elapsed `time_delta` since the last accrual — which spans time both before and after the admin's config change — is charged using the brand-new interest-rate curve/fees instead of the rates that were actually in effect during that period. This is the same root cause as the reported `setTreasuryWeights` issue: a weight/rate-affecting admin setter that lacks the "process pending interest first" step.

### Finding Description
`Bank::accrue_interest` computes interest strictly as a function of `time_delta = current_timestamp - self.last_update` and the *current* `interest_rate_config` fetched from `self.config.interest_rate_config.create_interest_rate_calculator(group)`: [1](#0-0) [2](#0-1) 

`lending_pool_configure_bank` (the full config setter, gated by `admin`) calls `bank.configure(&bank_config)` which updates `interest_rate_config`, `asset_weight_init/maint`, `liability_weight_init/maint`, `deposit_limit`, `borrow_limit`, etc., but never touches `last_update` or invokes `accrue_interest`: [3](#0-2) [4](#0-3) 

Likewise, `lending_pool_configure_bank_interest_only` (gated by `delegate_curve_admin`) directly updates `interest_rate_config` with no prior accrual: [5](#0-4) 

Compare this with the pattern the protocol *does* use elsewhere to avoid exactly this bug — e.g. `lending_pool_emissions_deposit` explicitly calls `bank.accrue_interest(...)` before mutating share values, and the dedicated `LendingPoolAccrueBankInterest` instruction runs `accrue_interest` + `update_bank_cache` as an atomic unit: [6](#0-5) [7](#0-6) 

Because the config setters skip this step, the next transaction that triggers `accrue_interest` (any balance-changing user action, or the permissionless accrue instruction) will apply the *new* `zero_util_rate`/`hundred_util_rate`/`points`/fee parameters (or the old ones, if lowered) over the full stale `time_delta`, not just the portion of time after the config change. This misattributes interest between lenders/borrowers and misallocates `insurance_fees_outstanding` / `group_fees_outstanding` / `protocol_fees_outstanding`, matching the reported bug class ("weights/rates applied from the last interest calculation point, instead of from the point of the update").

### Impact Explanation
Financial impact falls on unprivileged pool participants (lenders/borrowers) and fee recipients, not on the admin who triggers the change: depending on whether rates are raised or lowered, borrowers/lenders either overpay or underpay interest for the pre-change portion of the stale period, and insurance/group/protocol fee splits are computed with the wrong curve. This is the same "loss of yield for the treasury or pool participants" impact described in the source report, medium severity, since it only affects the delta between the last accrual and the next accrual after a config change.

### Likelihood Explanation
Medium: it requires (a) a bank going a nontrivial amount of time without any balance-changing transaction, and (b) an admin/delegate curve admin issuing `LendingPoolConfigureBank` or `LendingPoolConfigureBankInterestOnly` during that gap. This is a normal, expected admin operation (updating rate curves per market conditions), so the window is realistically hit whenever config changes coincide with periods of low activity — the same likelihood profile as the original report.

### Recommendation
Call `bank.accrue_interest(Clock::get()?.unix_timestamp, &group, bank_key)` (and `bank.update_bank_cache(&group)`) at the start of `lending_pool_configure_bank` and `lending_pool_configure_bank_interest_only`, before applying any changes to `interest_rate_config` or the asset/liability weights, so that all pending interest is settled under the old configuration prior to the update taking effect (mirroring the pattern already used in `lending_pool_emissions_deposit`).

### Proof of Concept
1. Bank B has active deposits and borrows; `last_update = T0`.
2. No further deposit/withdraw/borrow/repay occurs for a long interval (e.g. 90 days).
3. At `T0 + 60 days`, group admin calls `lending_pool_configure_bank` (or `lending_pool_configure_bank_interest_only`) to raise `hundred_util_rate`/`points` (e.g., due to market conditions) — this only mutates `bank.config.interest_rate_config`; `bank.last_update` remains `T0`. [8](#0-7) 
4. At `T0 + 90 days`, any user deposits/withdraws or someone calls the permissionless `LendingPoolAccrueBankInterest`; `accrue_interest` computes `time_delta = 90 days` and applies the *new* (higher) rate curve for the *entire* 90-day window, even though the old, lower rate was in effect for the first 60 days: [9](#0-8) 
5. Borrowers are retroactively charged 90 days at the new elevated rate instead of 60 days at the old rate + 30 days at the new rate, and fee splits (`insurance_fees_collected`, `group_fees_collected`, `protocol_fees_collected`) are computed using the wrong `InterestRateCalc`, producing a durable, unrecoverable misallocation of yield/fees for that period.

### Citations

**File:** programs/marginfi/src/state/bank.rs (L403-443)
```rust
    fn configure(&mut self, config: &BankConfigOpt) -> MarginfiResult {
        set_if_some!(self.config.asset_weight_init, config.asset_weight_init);
        set_if_some!(self.config.asset_weight_maint, config.asset_weight_maint);
        set_if_some!(
            self.config.liability_weight_init,
            config.liability_weight_init
        );
        set_if_some!(
            self.config.liability_weight_maint,
            config.liability_weight_maint
        );
        set_if_some!(self.config.deposit_limit, config.deposit_limit);

        set_if_some!(self.config.borrow_limit, config.borrow_limit);

        if let Some(new_state) = config.operational_state {
            // JupLend banks must be activated exactly once through `juplend_init_position`.
            check!(
                !(self.config.asset_tag == ASSET_TAG_JUPLEND
                    && self.config.operational_state == BankOperationalState::Uninitialized),
                MarginfiError::Unauthorized
            );
            // These states are unreachable by configuration
            check!(
                new_state != BankOperationalState::KilledByBankruptcy
                    && new_state != BankOperationalState::Uninitialized,
                MarginfiError::Unauthorized
            );
            // Log operational state change
            let old_state = self.config.operational_state;
            self.config.operational_state = new_state;
            msg!(
                "Operational state changed from {:?} to {:?}",
                old_state,
                new_state
            );
        }

        if let Some(ir_config) = &config.interest_rate_config {
            self.config.interest_rate_config.update(ir_config);
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

**File:** programs/marginfi/src/state/bank.rs (L546-564)
```rust
        let ir_calc = self
            .config
            .interest_rate_config
            .create_interest_rate_calculator(group);

        let InterestRateStateChanges {
            new_asset_share_value: asset_share_value,
            new_liability_share_value: liability_share_value,
            insurance_fees_collected,
            group_fees_collected,
            protocol_fees_collected,
        } = calc_interest_rate_accrual_state_changes(
            time_delta,
            total_assets,
            total_liabilities,
            &ir_calc,
            self.asset_share_value.into(),
            self.liability_share_value.into(),
        )?;
```

**File:** programs/marginfi/src/instructions/marginfi_group/configure_bank.rs (L20-45)
```rust
pub fn lending_pool_configure_bank(
    ctx: Context<LendingPoolConfigureBank>,
    bank_config: BankConfigOpt,
) -> MarginfiResult {
    let mut bank = ctx.accounts.bank.load_mut()?;

    // If settings are frozen, you can only update the deposit and borrow limits, everything else is ignored.
    if bank.get_flag(FREEZE_SETTINGS) {
        bank.configure_unfrozen_fields_only(&bank_config)?;

        msg!("WARN: Only deposit+borrow limits updated. Other settings IGNORED for frozen banks!");

        emit!(LendingPoolBankConfigureFrozenEvent {
            header: GroupEventHeader {
                marginfi_group: ctx.accounts.group.key(),
                signer: Some(*ctx.accounts.admin.key)
            },
            bank: ctx.accounts.bank.key(),
            mint: bank.mint,
            deposit_limit: bank.config.deposit_limit,
            borrow_limit: bank.config.borrow_limit,
        });
    } else {
        // Settings are not frozen, everything updates
        bank.configure(&bank_config)?;
        msg!("Bank configured!");
```

**File:** programs/marginfi/src/instructions/marginfi_group/configure_bank.rs (L117-122)
```rust
    bank.accrue_interest(
        clock.unix_timestamp,
        &group,
        #[cfg(not(feature = "client"))]
        ctx.accounts.bank.key(),
    )?;
```

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

**File:** programs/marginfi/src/instructions/marginfi_group/accrue_bank_interest.rs (L8-26)
```rust
pub fn lending_pool_accrue_bank_interest(
    ctx: Context<LendingPoolAccrueBankInterest>,
) -> MarginfiResult {
    let clock = Clock::get()?;
    let mut bank = ctx.accounts.bank.load_mut()?;
    let group = &ctx.accounts.group.load()?;

    bank.accrue_interest(
        clock.unix_timestamp,
        group,
        #[cfg(not(feature = "client"))]
        ctx.accounts.bank.key(),
    )?;

    // TODO see if we can recycle some things like the InterestRateCalc from accrue to save some CU
    bank.update_bank_cache(group)?;

    Ok(())
}
```
