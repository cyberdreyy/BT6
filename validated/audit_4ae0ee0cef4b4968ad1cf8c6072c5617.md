### Title
Permissionless `accrue_interest` retroactively charges an entire elapsed period at an attacker-manipulated spot utilization rate - ([File: programs/marginfi/src/state/bank.rs])

### Summary
Like the Morpho `MarketsManagerForAave._updateSPYs` bug, marginfi computes its interest rate from a **lazily-updated, instantaneous snapshot** of utilization and then applies that single rate to the **entire elapsed `time_delta`** since the bank's `last_update`. Because utilization is recomputed live from `total_asset_shares`/`total_liability_shares` at call time (not time-weighted), and because the accrual can be triggered permissionlessly, an attacker can spike utilization immediately before triggering accrual, lock in a high rate applied to the whole stale period, and then reverse the position — profiting at the expense of the bank's other depositors/borrowers.

### Finding Description
`Bank::accrue_interest` computes `time_delta = current_timestamp - self.last_update`, then reads live `total_assets`/`total_liabilities`, builds an `InterestRateCalc`, and calls `calc_interest_rate_accrual_state_changes`, which derives `utilization_rate = total_liabilities / total_assets` and calculates `lending_rate_apr`/`borrowing_rate_apr` from that single instantaneous value: [1](#0-0) 

That single rate is then applied via `calc_accrued_interest_payment_per_period`, compounding it over the *entire* `time_delta`: [2](#0-1) [3](#0-2) 

This accrual is triggered by every user-facing instruction (`lending_account_deposit`, `lending_account_borrow`, `lending_account_repay`, `lending_account_withdraw`) as a first step, but there is also a **fully permissionless** instruction, `lending_pool_accrue_bank_interest`, that anyone can call for any bank with no signer/authority requirement: [4](#0-3) 

Because `utilization_rate` is read fresh from the bank's current shares/amounts at the moment of the call — exactly analogous to Morpho reading Aave's `currentLiquidityRate`/`currentVariableBorrowRate` at call time — an attacker can borrow a large amount from the target bank (spiking `total_liabilities`, and thus utilization, near 100%), then immediately call `lending_pool_accrue_bank_interest` (or any other action ix, which accrues first) while the bank is in this manipulated state. The entire time since the bank's last accrual (`time_delta`, which can be long for low-traffic banks) is then compounded at this artificially spiked rate, permanently baking the manipulated interest into `asset_share_value`/`liability_share_value` for **all** existing depositors and borrowers. The attacker can then immediately repay the borrow (e.g., using `lending_account_start_flashloan`/`lending_account_end_flashloan`, which allow borrowing without a risk check mid-transaction) in the same transaction, paying no real interest on the manipulated borrow itself while capturing the inflated `asset_share_value` growth on a pre-existing (or freshly-opened) supply position in that bank.

This is the same bug class as the report: a rate model is a lazy-updated snapshot of an instantaneous, attacker-influenceable quantity (utilization/pool rate), and there is no time-weighting or manipulation-resistance, and the update can be triggered by an unprivileged, permissionless caller.

### Impact Explanation
A successful attack retroactively inflates `liability_share_value` (increasing debt owed by every existing borrower of that bank) and `asset_share_value` (increasing the value credited to every lender, including the attacker), for the full stale `time_delta` — not just the brief moment of manipulation. This causes:
- Unjust enrichment of the attacker's deposit position at the direct expense of honest borrowers in the same bank (increased debt with no corresponding real economic activity).
- Distorted `collected_insurance_fees_outstanding`/`collected_group_fees_outstanding`/`collected_program_fees_outstanding`, since fees are calculated off the same spiked rate over the same `time_delta`.
- Durable state corruption: unlike Morpho where the rate only persists until the next trigger, here the compounding is baked directly and irreversibly into share values once `accrue_interest` executes — there is no way to "undo" it once written to `asset_share_value`/`liability_share_value`.

The magnitude of the exploit scales with how long the bank has been inactive (larger `time_delta`) and how steep the interest curve is at high utilization (banks can be configured with `hundred_util_rate` far above 100% APR per the seven-point curve), making it more attractive against illiquid/low-activity banks — precisely the same caveat Spearbit raised for Morpho ("This can be detrimental in low-activity markets where a high APY is locked in").

### Likelihood Explanation
The attack requires only: (1) sufficient available liquidity in the target bank to spike utilization (borrowable via a marginfi flashloan or attacker's own capital/collateral), (2) that `lending_pool_accrue_bank_interest` (or any deposit/borrow/withdraw/repay call) be invoked while utilization is manipulated, and (3) reversing the borrow before end of the same transaction. All of these are unprivileged, permissionless operations already exposed by the protocol (`lending_pool_accrue_bank_interest` has no signer constraint, and `lending_account_start_flashloan`/`end_flashloan` explicitly permit risk-check-free intra-transaction borrowing). The only friction is `time_delta == 0` returning early, meaning the attacker needs at least one prior slot elapsed since the bank's `last_update` — trivial to satisfy for any bank that hasn't been touched in the current slot.

### Recommendation
- Avoid applying a single spot-utilization rate over the entire elapsed `time_delta`; instead consider a time-weighted average utilization/rate (TWAP-style), or checkpoint/accrue more granularly (e.g., limit how large a `time_delta` can be compounded in one shot at a manipulable state), similar to Morpho's mitigation of using an oracle/administrator-triggered TWAR instead of trusting the live snapshot.
- Alternatively, make `total_assets`/`total_liabilities` used for the rate calculation resistant to same-transaction manipulation, e.g., by snapshotting the utilization at the start of `last_update` rather than recomputing it fresh with the attacker's flash-borrow included, or by requiring flashloaned liabilities to be excluded from the accrual-triggering utilization computation.
- Consider disallowing/deprioritizing borrow amounts that are repaid within the same transaction (flashloan-style round-trips) from influencing `accrue_interest`'s utilization snapshot.

### Proof of Concept
1. Identify a marginfi Bank `B` with `last_update` at least one slot in the past and enough available liquidity to move utilization from a low value toward ~100% (e.g., `total_liabilities / total_assets`).
2. In a single transaction:
   a. Call `lending_account_start_flashloan`.
   b. Call `lending_account_borrow` against Bank `B` for the maximum available liquidity (no risk check required inside a flashloan), spiking `total_liability_shares` and thus utilization toward 100%.
   c. Call `lending_pool_accrue_bank_interest` for Bank `B` (permissionless, no signer required) — this reads the now-spiked utilization and compounds the resulting high `borrowing_rate_apr`/`lending_rate_apr` over the entire `time_delta` since `last_update`, permanently updating `asset_share_value`/`liability_share_value` for the whole bank.
   d. Call `lending_account_repay` to fully repay the flashloaned amount.
   e. Call `lending_account_end_flashloan` (final risk check passes since the debt is repaid).
3. The attacker's own pre-existing (or freshly opened prior to the tx) deposit position in Bank `B` now reflects the inflated `asset_share_value`, while every other borrower in Bank `B` now owes more via the inflated `liability_share_value` for that stale period, all for the cost of gas. [5](#0-4) [6](#0-5)

### Citations

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

**File:** programs/marginfi/src/state/interest_rate.rs (L367-381)
```rust
/// Calculates the accrued interest payment per period `time_delta` in a principal value `value` for interest rate (in APR) `arp`.
/// Result is the new principal value.
fn calc_accrued_interest_payment_per_period(
    apr: I80F48,
    time_delta: u64,
    value: I80F48,
) -> Option<I80F48> {
    let ir_per_period: I80F48 = apr
        .checked_mul(time_delta.into())?
        .checked_div(SECONDS_PER_YEAR)?;

    let new_value: I80F48 = value.checked_mul(I80F48::ONE.checked_add(ir_per_period)?)?;

    Some(new_value)
}
```

**File:** programs/marginfi/src/state/interest_rate.rs (L425-486)
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

    Ok(InterestRateStateChanges {
        new_asset_share_value: calc_accrued_interest_payment_per_period(
            lending_rate_apr,
            time_delta,
            asset_share_value,
        )
        .ok_or_else(math_error!())?,
        new_liability_share_value: calc_accrued_interest_payment_per_period(
            borrowing_rate_apr,
            time_delta,
            liability_share_value,
        )
        .ok_or_else(math_error!())?,
        insurance_fees_collected: calc_interest_payment_for_period(
            insurance_fee_apr,
            time_delta,
            total_liabilities_amount,
        )
        .ok_or_else(math_error!())?,
        group_fees_collected: calc_interest_payment_for_period(
            group_fee_apr,
            time_delta,
            total_liabilities_amount,
        )
        .ok_or_else(math_error!())?,
        protocol_fees_collected: calc_interest_payment_for_period(
            protocol_fee_apr,
            time_delta,
            total_liabilities_amount,
        )
        .ok_or_else(math_error!())?,
    })
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/accrue_bank_interest.rs (L8-42)
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

#[derive(Accounts)]
pub struct LendingPoolAccrueBankInterest<'info> {
    #[account(
        constraint = (
            !group.load()?.is_protocol_paused()
        ) @ MarginfiError::ProtocolPaused
    )]
    pub group: AccountLoader<'info, MarginfiGroup>,

    #[account(
        mut,
        has_one = group @ MarginfiError::InvalidGroup
    )]
    pub bank: AccountLoader<'info, Bank>,
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
