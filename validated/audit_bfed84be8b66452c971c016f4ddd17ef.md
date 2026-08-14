### Title
Interest rate config updates apply retroactively to the full unaccrued period instead of only from the update time - ([File: programs/marginfi/src/instructions/marginfi_group/configure_bank_lite.rs])

### Summary
`lending_pool_configure_bank_interest_only` and `lending_pool_configure_bank` allow an admin/delegate to update a bank's `interest_rate_config` (or full `BankConfig`) without first calling `accrue_interest` to settle interest owed under the *old* curve for the time elapsed since `last_update`. This mirrors the reported `update_reward_config` bug class: a config value that drives a time-weighted accrual is swapped in-place while `last_update`/accrued-so-far state is left stale, so the next accrual event applies the *new* rate to the *entire* elapsed interval, including time that occurred under the old rate.

### Finding Description
`Bank::accrue_interest` computes interest for the whole `time_delta = current_timestamp - self.last_update` using whatever `interest_rate_config` is currently stored on the bank at the time it is invoked: [1](#0-0) [2](#0-1) 

`interest_rate_config` (and the rest of `BankConfig`) can be updated directly via `lending_pool_configure_bank_interest_only`, which mutates `bank.config.interest_rate_config` in place and never calls `accrue_interest`/`update_bank_cache` first, and never advances `last_update`: [3](#0-2) 

The same pattern exists in the general `lending_pool_configure_bank` path, which calls `bank.configure()`; that function also swaps `interest_rate_config` in place with no preceding accrual: [4](#0-3) [5](#0-4) 

Because `last_update` is only advanced inside `accrue_interest` (or `update_bank_cache`), and neither of the configure instructions accrue interest before rewriting the curve, any accrual triggered later (via `lending_pool_accrue_bank_interest`, a deposit, borrow, withdraw, or repay) will compute interest for the *entire* elapsed period — spanning both before and after the config change — using only the **new** curve parameters. This is structurally identical to the reported bug: `update_reward_config` overwrote `reward_config` without first invoking `self_update` to flush unclaimed rewards computed under the old `reward_per_day`, causing the new rate to be applied retroactively to time that should have accrued under the old rate.

### Impact Explanation
Depending on the direction of the interest-rate curve change, this misattributes interest across all depositors/borrowers in the bank for the entire unaccrued interval:
- If the curve is lowered, borrowers who accrued a large debt at a high rate for most of the interval instead get charged the new lower rate retroactively (protocol/lenders/insurance under-collect fees and interest).
- If the curve is raised, depositors who should have earned at a lower rate for most of the interval are retroactively credited (or, for borrowers, penalized) at the new higher rate, i.e. borrowers pay more than economically owed while lenders/protocol over-collect for time that occurred under the old, lower curve.

This has a direct financial effect on protocol accounting (`asset_share_value`/`liability_share_value`, insurance/protocol fee collection) and on user balances, and can be triggered any time a `delegate_curve_admin` or group `admin` updates rates while the bank has gone any nonzero time since its last accrual (which is normal/likely in production, since accrual is lazy and permissionless but not guaranteed to run every instant).

### Likelihood Explanation
Interest rate changes are a routine, expected admin/delegate operation, and accrual on any given bank is lazy — there is no guarantee accrual has run immediately prior to a config update. The stale window can be arbitrarily long (limited only by how recently `lending_pool_accrue_bank_interest` or another accrual-triggering instruction last ran), so this is easily triggered under normal operational conditions without needing a malicious actor — it requires only a routine, privileged interest-rate-config update while the bank is not "freshly accrued" (i.e. it needs `delegate_curve_admin` or `admin` authority to execute, which is the same standing authority as the analogous Otter Audits patched contract's admin).

### Recommendation
Call `bank.accrue_interest(current_timestamp, group, ...)` (and `update_bank_cache`) at the start of `lending_pool_configure_bank_interest_only` and `lending_pool_configure_bank`/`Bank::configure`, before applying the new `interest_rate_config`, so that `last_update` and the share values reflect interest owed under the old curve up to the moment of the change, and only time after the update accrues under the new curve — mirroring the remediation already applied upstream in the referenced patch (`9d25e65`).

### Proof of Concept
1. Create a bank with an initial `interest_rate_config` curve C1; have a lender deposit and a borrower borrow so `total_asset_shares`/`total_liability_shares` are nonzero.
2. Advance the clock by `T` seconds without calling `lending_pool_accrue_bank_interest` (this is easy since accrual is permissionless/lazy, not automatic).
3. As `delegate_curve_admin`, call `lending_pool_configure_bank_interest_only` with a new curve C2 (see `configure_bank_lite.rs:12-35`) — note `bank.last_update` is unchanged and no accrual occurs.
4. Trigger accrual (e.g., call `lending_pool_accrue_bank_interest`, or have a user deposit/withdraw). Observe in `Bank::accrue_interest` (`bank.rs:511-575`) that `time_delta` (still the full `T` seconds) is priced entirely with curve C2, even though C1 was in effect for most of that interval — demonstrate the resulting `asset_share_value`/`liability_share_value` differ from the value that would result from splitting the interval at the config-change timestamp and pricing each sub-interval with its respective curve.

### Citations

**File:** programs/marginfi/src/state/bank.rs (L441-443)
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
