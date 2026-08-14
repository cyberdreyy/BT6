### Title
Interest rate/fee config changes are applied retroactively to unaccrued interest, letting an admin change the borrower/protocol/insurance fee split for a period that already elapsed under the old rates - (File: `programs/marginfi/src/instructions/marginfi_group/configure_bank.rs`, `programs/marginfi/src/instructions/marginfi_group/configure_bank_lite.rs`)

### Summary
`lending_pool_configure_bank` and `lending_pool_configure_bank_interest_only` mutate `bank.config.interest_rate_config` directly without first calling `accrue_interest` to settle the interest/fees that accumulated since `bank.last_update` under the *old* config.

### Finding Description
`lending_pool_configure_bank` calls `bank.configure(&bank_config)`, which applies `self.config.interest_rate_config.update(ir_config)` directly to the bank's interest-rate config with no preceding accrual step: [1](#0-0) [2](#0-1) 

Likewise, the delegate-scoped `lending_pool_configure_bank_interest_only` instruction updates `insurance_ir_fee`, `insurance_fee_fixed_apr`, `protocol_ir_fee`, `protocol_fixed_fee_apr`, `protocol_origination_fee`, `zero_util_rate`, `hundred_util_rate`, and `points` with no accrual call before applying `InterestRateConfigImpl::update`: [3](#0-2) 

Interest and fees are only realized lazily, the next time any instruction touching the bank calls `Bank::accrue_interest`, which computes the entire elapsed `time_delta` since `last_update` using whatever `interest_rate_config` is currently stored on the bank at that moment: [4](#0-3) 

Because `accrue_interest` has no memory of "old" vs "new" rate periods, if the config is changed mid-period, the *entire* elapsed period (including the portion that occurred while the old config was in effect) is charged/credited at the **new** rate. This is functionally identical to the Thruster `setYieldCut` finding: the fee/rate split (`insurance_ir_fee`/`insurance_fee_fixed_apr`, `protocol_ir_fee`/`protocol_fixed_fee_apr`, and the interest curve points themselves) is retroactively applied to a period during which depositors/borrowers/the protocol should have been charged under the old parameters.

### Impact Explanation
Any change to `interest_rate_config` (curve points, `zero_util_rate`/`hundred_util_rate`, or the insurance/protocol fee rates) causes the yield/fee split for the entire dormant period since the last interaction to be computed with the new parameters instead of a pro-rated old/new split. For banks with infrequent activity (e.g., illiquid or low-traffic banks), this window can be large. This can shift value between borrowers, lenders, the insurance fund, and the protocol/group fee recipients in a way inconsistent with the rates that were actually in force during most of that period. Because `accrue_interest` also determines `asset_share_value`/`liability_share_value` (the core accounting unit for all deposits and loans), the misapplied rate directly and durably affects protocol accounting values, not just a display figure.

### Likelihood Explanation
The trigger requires a privileged actor (group `admin` or `delegate_curve_admin`) to change the interest rate config, which mirrors the original Thruster report's precondition (owner changes yield cut). However, unlike a purely admin-side issue, the resulting misvaluation is realized and locked in by any subsequent **permissionless** interaction with the bank (deposit/withdraw/borrow/repay, or the dedicated permissionless `LendingPoolAccrueBankInterest` instruction), since accrual always uses the live config with no historical rate tracking. Given that rate/fee updates are a normal, expected operational action (adjusting curves/fees per market conditions) and bank inactivity between updates is common, the retroactive-rate window is realistic rather than purely theoretical.

### Recommendation
Before mutating `interest_rate_config` in `lending_pool_configure_bank` and `lending_pool_configure_bank_interest_only`, force a settlement of interest under the old config first — i.e., call `bank.accrue_interest(current_timestamp, &group, ...)` prior to `bank.configure(...)` / `interest_rate_config.update(...)`. This ensures `last_update` is advanced and all interest/fees up to the moment of the change are computed under the parameters that were actually in effect, and only interest accrued after the config change uses the new parameters. If forcing accrual first is undesired (e.g. no group account available in the interest-only instruction), require passing an up-to-date `MarginfiGroup` and timestamp to always accrue immediately prior to config application, as is already done elsewhere (e.g. `lending_pool_emissions_deposit`).

### Proof of Concept
1. Bank B has active borrows and is not touched for an extended time (`last_update` stays static).
2. Admin (or `delegate_curve_admin`) calls `lending_pool_configure_bank_interest_only` (or `lending_pool_configure_bank`) to raise `protocol_ir_fee`/`protocol_fixed_fee_apr` (or otherwise change the curve) — this mutates `bank.config.interest_rate_config` in place with no interest settlement: [5](#0-4) 
3. Some time later, any user deposits/withdraws/borrows/repays against bank B (or anyone calls the permissionless accrue-interest instruction). This triggers `Bank::accrue_interest`, which computes `time_delta = current_timestamp - last_update` spanning the *entire* dormant period (pre- and post-config-change) and applies the **new** `interest_rate_config` uniformly across that whole span: [6](#0-5) 
4. Result: interest/fees for the portion of the period that occurred under the old rates are computed and collected at the new rates, producing an incorrect (and non-reconstructible) split between insurance/protocol/group fees and borrower/lender interest for that period.

### Citations

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

**File:** programs/marginfi/src/state/bank.rs (L441-443)
```rust
        if let Some(ir_config) = &config.interest_rate_config {
            self.config.interest_rate_config.update(ir_config);
        }
```

**File:** programs/marginfi/src/state/bank.rs (L511-564)
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

        if (total_assets == I80F48::ZERO) || (total_liabilities == I80F48::ZERO) {
            #[cfg(not(feature = "client"))]
            emit!(LendingPoolBankAccrueInterestEvent {
                header: GroupEventHeader {
                    marginfi_group: self.group,
                    signer: None
                },
                bank,
                mint: self.mint,
                delta: time_delta,
                fees_collected: 0.,
                insurance_collected: 0.,
            });

            return Ok(());
        }
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
