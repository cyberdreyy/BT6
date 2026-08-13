Confirmed root cause: both `lending_pool_configure_bank` and `lending_pool_configure_bank_interest_only` mutate `bank.config.interest_rate_config` directly without first calling `bank.accrue_interest()`, and `accrue_interest` always computes `time_delta` from `self.last_update` using whatever interest-rate config is active *at the time it eventually runs* [1](#0-0) [2](#0-1) [3](#0-2) .

### Title
Admin-changed interest rate parameters apply retroactively to already-elapsed accrual periods, unfairly re-pricing dormant borrower/lender positions - (File: `programs/marginfi/src/state/bank.rs`)

### Summary
marginfi banks accrue interest lazily: `Bank::accrue_interest` only runs when a state-changing instruction touches the bank, computing `time_delta = current_timestamp - self.last_update` and applying the **current** `interest_rate_config` to that entire elapsed window [3](#0-2) . Because the group admin (or `delegate_curve_admin`) can update `interest_rate_config` at any time via `lending_pool_configure_bank` or `lending_pool_configure_bank_interest_only` without first forcing an accrual, any user who has not recently transacted has their entire dormant period re-priced under the new curve the next time accrual runs.

### Finding Description
- `Bank::configure` (invoked by `lending_pool_configure_bank`) directly overwrites `self.config.interest_rate_config` via `.update(ir_config)` with no preceding `accrue_interest` call [4](#0-3) .
- `lending_pool_configure_bank_interest_only` (callable by the `delegate_curve_admin`, a narrower/lower-trust role than the full group admin) does the same: it updates `bank.config.interest_rate_config` immediately, with no call to `accrue_interest` beforehand [5](#0-4) .
- `Bank::last_update` is untouched by either configure path — it is only advanced by `accrue_interest` or `update_bank_cache` [6](#0-5) [7](#0-6) .
- The next time any user (depositor, borrower, or a permissionless caller of `lending_pool_accrue_bank_interest`) triggers accrual, `create_interest_rate_calculator` builds the calculator from whatever `interest_rate_config` is active *at that moment*, and `calc_interest_rate_accrual_state_changes` applies that single rate uniformly across the entire `time_delta`, including the portion that elapsed before the config change [8](#0-7) [9](#0-8) .

This is the same bug class described in the Zaros report: fee/rate parameters accrue lazily and a privileged party's parameter change is applied retroactively to the entire un-settled window, unfairly impacting users based on parameters that didn't exist during most of the elapsed period.

### Impact Explanation
Any bank with borrowers/lenders who haven't transacted recently is exposed. If the group admin (or the lower-privileged `delegate_curve_admin`) raises `zero_util_rate`/`hundred_util_rate`/curve `points`, every dormant borrower is retroactively charged a higher rate for time that already elapsed under the old, lower rate — a direct, unbounded (bounded only by elapsed time and total liabilities) financial loss transferred from borrowers to lenders/protocol fees, or vice versa if rates are lowered (harming lenders). Because `delegate_curve_admin` is a distinct, more narrowly-scoped signer from the full group `admin`, this power can be exercised by an actor who is not the top-level trusted admin, widening the blast radius of the misconfigured/malicious-parameter scenario.

### Likelihood Explanation
High reachability: no special preconditions are needed beyond a bank having outstanding assets/liabilities and having gone some time without any interaction (common for less-active banks, as the project's own docs note — "Less popular Banks might compound just a few times per week" [10](#0-9) ). The admin/`delegate_curve_admin` role is expected to change interest curves periodically as normal operations, and the current code path makes retroactive mispricing the default behavior rather than an edge case requiring malicious intent.

### Recommendation
Force an `accrue_interest` (settling the bank at the *old* config) immediately before applying any interest-rate-config mutation in both `Bank::configure` (`programs/marginfi/src/state/bank.rs`) and `lending_pool_configure_bank_interest_only` (`programs/marginfi/src/instructions/marginfi_group/configure_bank_lite.rs`), so `last_update` is advanced and outstanding balances are settled at the pre-change rate before the new curve takes effect. This mirrors the existing pattern already used in `lending_pool_emissions_deposit`, which calls `bank.accrue_interest(...)` before mutating share values [11](#0-10) .

### Proof of Concept
1. Deploy a bank with a borrower holding an outstanding liability and a lender holding the corresponding asset; do not touch the bank for N days (no deposit/borrow/withdraw/repay/accrue-interest instruction is sent), so `last_update` stays stale.
2. As `admin` (or `delegate_curve_admin`), call `lending_pool_configure_bank_interest_only` (or `lending_pool_configure_bank`) to sharply raise `hundred_util_rate`/curve points.
3. Immediately after, trigger accrual (e.g., via the permissionless `lending_pool_accrue_bank_interest` instruction, `programs/marginfi/src/instructions/marginfi_group/accrue_bank_interest.rs`).
4. Observe that `calc_interest_rate_accrual_state_changes` charges the borrower the *new*, higher rate over the *entire* stale `time_delta` (including the days before the config change), rather than pro-rating the old rate for the pre-change period and the new rate only from the change point forward — reproducing the "retrospective impact" described in the Zaros report but for marginfi's interest-rate curve instead of a perp funding rate.

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

**File:** programs/marginfi/src/state/bank.rs (L440-443)
```rust

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

**File:** programs/marginfi/src/state/bank.rs (L656-658)
```rust
        // Update banks last update timestamp
        self.last_update = Clock::get()?.unix_timestamp;
        Ok(())
```

**File:** programs/marginfi/src/state/interest_rate.rs (L425-452)
```rust
pub fn calc_interest_rate_accrual_state_changes(
    time_delta: u64,
    total_assets_amount: I80F48,
    total_liabilities_amount: I80F48,
    interest_rate_calc: &InterestRateCalc,
    asset_share_value: I80F48,
    liability_share_value: I80F48,
) -> MarginfiResult<InterestRateStateChanges> {
    // If the cache is empty, we need to calculate the interest rates
    let utilization_rate: I80F48 = total_liabilities_amount
        .checked_div(total_assets_amount)
        .ok_or_else(math_error!())?;
    debug!(
        "Utilization rate: {}, time delta {}s",
        utilization_rate, time_delta
    );
    let interest_rates = interest_rate_calc.calc_interest_rate(utilization_rate)?;

    debug!("{:#?}", interest_rates);

    let ComputedInterestRates {
        lending_rate_apr,
        borrowing_rate_apr,
        group_fee_apr,
        insurance_fee_apr,
        protocol_fee_apr,
        ..
    } = interest_rates;
```

**File:** README.md (L176-179)
```markdown
like SOL, compound every few minutes, or even every few seconds on more active days. Less popular
Banks might compound just a few times per week, but these Banks typically have very few borrows (and
thus a low APR to compound). Since interest compounds based on usage, the more popular our platform,
the more often interest compounds. Remember that interest accrues for all of a Bank's users at the
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
