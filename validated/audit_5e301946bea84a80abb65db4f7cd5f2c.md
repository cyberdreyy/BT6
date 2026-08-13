### Title
Bank interest rate config changes are not accrued before taking effect, causing new rates to be retroactively applied to stale periods - ([File: programs/marginfi/src/state/bank.rs])

### Summary
### Finding Description
Bank interest accrual in marginfi is lazy: `accrue_interest()` is only invoked when a user performs an action (deposit/borrow/repay/withdraw) or when `lending_pool_accrue_bank_interest` is called permissionlessly [1](#0-0) . When it finally runs, it computes a single `time_delta = current_timestamp - self.last_update` and applies the bank's *current* `interest_rate_config` uniformly across that entire elapsed window [2](#0-1) .

The admin-facing configuration paths `lending_pool_configure_bank` (`bank.configure()`) and the scoped `lending_pool_configure_bank_interest_only` both mutate `self.config.interest_rate_config` in place with no call to `accrue_interest()` beforehand, so any interest that should have accrued under the *old* curve/fee parameters up to the moment of the change is never snapshotted [3](#0-2) [4](#0-3) . `last_update` is likewise left untouched by these configure instructions. This is the same root cause as the referenced ReaperVaultV2 finding: a rate-governing parameter (`lockedProfitDegradation` there, `interest_rate_config`/curve points here) is swapped without first settling accrued state (`lockedProfit`/`lastReport` there, `asset_share_value`/`liability_share_value`/`last_update` here) against the *previous* parameter.

As a result, the next unprivileged user action that triggers `accrue_interest()` after a rate change applies the brand-new rate retroactively to the entire stale window (which may span a long period before the admin's change), instead of splitting the interval at the moment the config changed.

### Impact Explanation
Because `accrue_interest()` distributes the computed change in `asset_share_value`/`liability_share_value` pro-rata over `total_asset_shares`/`total_liability_shares` *at the time the accrual finally executes* [5](#0-4) , whoever holds shares at that moment captures (or is charged) interest for a stale period computed at the wrong (new, not blended) rate. An unprivileged depositor/borrower can time an ordinary deposit/withdraw/borrow to land immediately after an admin rate change (and before anyone else triggers accrual for that bank), causing the backlog of un-accrued interest to be computed entirely at the new rate and settled against the current share pool — misvaluing the bank's owed/earned interest and redistributing value between existing lenders and borrowers in a way that does not reflect the actual historical rate schedule. This is a financial-effect exploitable misvaluation, not merely a display/logging issue, since `asset_share_value`/`liability_share_value` directly determine withdrawal and repayment amounts.

### Likelihood Explanation
Likelihood is comparable to the original finding: it requires (a) an admin/delegate-curve-admin rate change that leaves a bank's un-accrued backlog of interest non-trivial (plausible on low-traffic banks, since interest "accumulates when any transaction ... occurs, so banks without much activity may have stale numbers until someone interacts with them" per the project's own fee guide), and (b) an unprivileged user to trigger the next accrual (deposit/withdraw/borrow, or the fully permissionless `lending_pool_accrue_bank_interest` instruction) right after the change. Unlike the ReaperVault case, no whitelisting of depositors gates who can trigger this in marginfi — any user, or a bot watching for `LendingPoolBankConfigureEvent`/`LendingPoolBankConfigureFrozenEvent`, can race to be the first to call an accrual-triggering instruction after a rate update, making the "front-run the parameter change" step easier here than in the original report's whitelisted-depositor vault.

### Recommendation
Call `bank.accrue_interest(clock.unix_timestamp, group, ...)` (and, ideally, `bank.update_bank_cache(group)`) at the start of `lending_pool_configure_bank`, `configure_unfrozen_fields_only`, and `lending_pool_configure_bank_interest_only`, before mutating `interest_rate_config`, so `last_update` and the share values are settled under the old curve/fees before the new configuration takes effect. This mirrors the recommended fix of updating `lockedProfit`/`lastReport` before changing `lockedProfitDegradation`.

### Proof of Concept
1. Bank has depositors/borrowers with `last_update = T0` and old `interest_rate_config = R_old`; no bank action occurs for a long time (interest quietly accrues but isn't materialized).
2. At `T1 >> T0`, the group admin (or `delegate_curve_admin`) calls `lending_pool_configure_bank_interest_only` to update the curve/fee parameters to `R_new`, per `configure_bank_lite.rs` — this only mutates `bank.config.interest_rate_config`; `last_update` stays at `T0` [6](#0-5) .
3. Immediately after, an unprivileged user calls any bank-touching instruction (or the permissionless `LendingPoolAccrueBankInterest`) before anyone else does. `accrue_interest` runs with `time_delta = T1' - T0` (T1' ≈ T1) and computes the entire interest for that whole stale window using `R_new`, not a blend of `R_old` (T0→T1) and `R_new` (T1→T1') [7](#0-6) .
4. The resulting jump in `asset_share_value`/`liability_share_value` is applied to whatever `total_asset_shares`/`total_liability_shares` exist at that instant, misallocating interest that should have been split between the two rate regimes.

Note: I could not find any explicit test asserting that `interest_rate_config` changes call `accrue_interest` first (the existing `configure_bank_interest_only_success` test only asserts config-field equality, not accrual behavior) [8](#0-7) , which is consistent with this gap being unaddressed in the current codebase as indexed.

### Citations

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

**File:** programs/marginfi/src/state/bank.rs (L441-443)
```rust
        if let Some(ir_config) = &config.interest_rate_config {
            self.config.interest_rate_config.update(ir_config);
        }
```

**File:** programs/marginfi/src/state/bank.rs (L511-565)
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

**File:** programs/marginfi/src/state/bank.rs (L569-575)
```rust
        self.cache.accumulated_since_last_update = asset_share_value
            .checked_sub(I80F48::from(self.asset_share_value))
            .and_then(|v| v.checked_mul(I80F48::from(self.total_asset_shares)))
            .ok_or_else(math_error!())?
            .into();
        self.cache.interest_accumulated_for = time_delta.min(u32::MAX as u64) as u32;
        self.asset_share_value = asset_share_value.into();
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

**File:** programs/marginfi/tests/admin_actions/setup_bank.rs (L1570-1600)
```rust
#[tokio::test]
async fn configure_bank_interest_only_success() -> anyhow::Result<()> {
    let test_f = TestFixture::new(Some(TestSettings::all_banks_payer_not_admin())).await;
    let bank = test_f.get_bank(&BankMint::Usdc);
    let old_bank = bank.load().await;

    let exp_points = make_points(&vec![
        RatePoint::new(1234, 56789),
        RatePoint::new(2345, 67890),
    ]);

    let ir_config = InterestRateConfigOpt {
        // TODO deprecate in 1.7
        // placeholder0: Some(I80F48::from_num(0.9).into()),
        // placeholder1: Some(I80F48::from_num(0.5).into()),
        // placeholder2: Some(I80F48::from_num(1.5).into()),
        insurance_fee_fixed_apr: Some(I80F48::from_num(0.01).into()),
        insurance_ir_fee: Some(I80F48::from_num(0.02).into()),
        protocol_fixed_fee_apr: Some(I80F48::from_num(0.03).into()),
        protocol_ir_fee: Some(I80F48::from_num(0.04).into()),
        protocol_origination_fee: Some(I80F48::from_num(0.05).into()),
        zero_util_rate: Some(123),
        hundred_util_rate: Some(1234567),
        points: Some(exp_points),
    };

    test_f
        .marginfi_group
        .try_lending_pool_configure_bank_interest_only(&bank, ir_config.clone())
        .await?;

```
