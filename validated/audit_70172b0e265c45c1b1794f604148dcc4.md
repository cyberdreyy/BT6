### Title
Admin bank config updates mutate `interest_rate_config` without first accruing pending interest, causing the new curve to be retroactively applied across time that elapsed under the old curve - ([File: programs/marginfi/src/instructions/marginfi_group/configure_bank.rs])

### Summary
`lending_pool_configure_bank` and `lending_pool_configure_bank_interest_only` allow the group admin / `delegate_curve_admin` to change a bank's `interest_rate_config` (and weights/limits) directly in storage without first calling `accrue_interest` to settle the interest owed for the time elapsed since `last_update`. This is the same bug class as the external report: a privileged config update is applied to storage while a separate, un-triggered "settlement" step (`execute_epoch_operations` in the report, `accrue_interest`/`lending_pool_accrue_bank_interest` here) is responsible for pricing state changes over elapsed time, leaving stale/mixed-rate periods to be priced incorrectly.

### Finding Description
`lending_pool_configure_bank` loads the bank and calls `bank.configure(&bank_config)`, which directly overwrites `self.config.interest_rate_config` (via `update`) as well as asset/liability weights, without touching `last_update` or calling `accrue_interest`: [1](#0-0) 

The bank-level `configure` function applies the new `ir_config` straight into `self.config.interest_rate_config`: [2](#0-1) 

Similarly, `lending_pool_configure_bank_interest_only` updates `bank.config.interest_rate_config` in place with no accrual step: [3](#0-2) 

Interest is only actually settled by `accrue_interest`, which computes accrued asset/liability share value changes over the *entire* `time_delta = current_timestamp - self.last_update` using whatever `interest_rate_config` is present *at the time accrue_interest runs* - not the config that was in effect during each sub-interval of that delta: [4](#0-3) 

`accrue_interest` is only invoked from user-driven or permissionless instructions (`deposit`, `withdraw`, `borrow`, `repay`, `close_balance`, `liquidate`, `handle_bankruptcy`, `super_admin_deposit/withdraw`, and the standalone permissionless `lending_pool_accrue_bank_interest`) — none of which are called automatically as part of `lending_pool_configure_bank` or `lending_pool_configure_bank_interest_only`: [5](#0-4) 

Because `last_update` is not advanced when the config changes, if a bank sits unaccrued for some time under interest-rate curve A, and the admin then changes the curve to B, the next time any user action triggers `accrue_interest`, the *whole* elapsed `time_delta` (including the portion that occurred under curve A) is priced entirely using curve B. This is directly analogous to the report's root cause: a config value (`anc_purchase_factor` there, `interest_rate_config`/weights here) is updated in storage but the epoch/accrual routine that consumes that config over an elapsed time window is not invoked at the moment of the config change, leaving out-of-date/mixed-period data to be priced incorrectly on the next settlement.

### Impact Explanation
This causes an exploitable misvaluation of accrued interest between the bank's depositors, borrowers, and the protocol/insurance fee pools:
- If the admin raises rates, borrowers can be charged interest at the new higher rate for time that already elapsed at the old, lower rate — effectively backdating a rate hike.
- If the admin lowers rates (or a malicious/compromised `delegate_curve_admin` lowers them), depositors lose interest they should have earned during the pre-change period, since the entire un-accrued window is retroactively priced at the lower rate.
- An admin (or a party who can influence when `lending_pool_accrue_bank_interest`/user activity occurs) can manipulate the interest actually realized by timing config changes relative to periods of low accrual activity, shifting value between counterparties (lenders vs borrowers vs fee recipients) in a way unrelated to the actual time each rate was in effect.

This is a financial-effect misvaluation issue, though it requires either admin/delegate action or the coincidence of a stale bank plus a config change, so severity is moderate rather than critical.

### Likelihood Explanation
Likelihood is moderate: it requires a privileged party (`admin` or `delegate_curve_admin`) to change `interest_rate_config`/weights while the bank has an un-accrued time gap (i.e., no deposit/withdraw/borrow/repay/liquidation/accrue call happened recently). This is a plausible operational scenario for low-traffic banks or right after admin curve/weight changes are batched, and does not require any exploit beyond normal admin operation timing.

### Recommendation
Call `bank.accrue_interest(...)` (and `update_bank_cache`) inside `lending_pool_configure_bank` and `lending_pool_configure_bank_interest_only` before applying the new `BankConfigOpt`/`InterestRateConfigOpt`, so that any pending interest is settled under the *old* curve prior to switching to the new one. This mirrors the report's recommendation of calling the epoch/settlement routine before/after config updates.

### Proof of Concept
1. Admin creates a bank with interest-rate curve A; users deposit/borrow, bank starts idle (`last_update = T0`).
2. Time passes to `T1` with no deposit/withdraw/borrow/repay/liquidate/`lending_pool_accrue_bank_interest` calls (so `last_update` is still `T0`).
3. Admin calls `lending_pool_configure_bank_interest_only` (or `lending_pool_configure_bank`) to change `interest_rate_config` to curve B. No accrual happens; `bank.config.interest_rate_config` is now B and `last_update` is still `T0`.
4. At `T2`, a user deposits, triggering `accrue_interest`, which computes `time_delta = T2 - T0` and prices the *entire* interval `[T0, T2]` — including `[T0, T1]`, which should have used curve A — using curve B instead, per `accrue_interest`'s use of `self.config.interest_rate_config.create_interest_rate_calculator(group)`. [6](#0-5)

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
