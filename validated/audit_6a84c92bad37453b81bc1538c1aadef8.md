### Title
Group admin (or delegated Curve Admin) can set `protocol_origination_fee` (and other interest-rate fee fields) to an unbounded value, allowing forced over-collection from borrowers on every borrow - (File: `programs/marginfi/src/state/interest_rate.rs`, `programs/marginfi/src/instructions/marginfi_account/borrow.rs`)

### Summary
`InterestRateConfigImpl::validate_seven_point()` only checks the ordering/consistency of the utilization curve points; it never bounds the fee-related fields (`insurance_fee_fixed_apr`, `insurance_ir_fee`, `protocol_fixed_fee_apr`, `protocol_ir_fee`, `protocol_origination_fee`). These fields are set via `InterestRateConfigImpl::update()`, which is reachable by the group admin or a scoped `Delegate Curve Admin` through `ConfigureBankLiteCurve`. Because `protocol_origination_fee` has no maximum cap enforced anywhere in the codebase, it can be set to 100% or more, causing every subsequent borrower to incur an origination fee equal to (or exceeding) their entire borrowed principal, which is added straight to their liability and diverted to group/program fees. This mirrors the reported analog: a percentage parameter fully controlled by a privileged party with no upper bound, and no protection for the unprivileged counterparty (node runner → here, the borrower).

### Finding Description
`InterestRateConfig::validate()` dispatches to `validate_seven_point()`: [1](#0-0) 

`validate_seven_point()` only enforces ordering of curve points (util ascending, rates between zero/hundred util rates) — it performs **no bound check at all** on `insurance_fee_fixed_apr`, `insurance_ir_fee`, `protocol_fixed_fee_apr`, `protocol_ir_fee`, or `protocol_origination_fee`: [2](#0-1) 

The `update()` function copies whatever value is supplied in `InterestRateConfigOpt` directly into the live `InterestRateConfig`, with no clamping: [3](#0-2) 

Per the permissions guide, this update path (`ConfigureBankLiteCurve`, taking `InterestRateConfigOpt`) is reachable not only by the group admin but also by a scoped `Delegate Curve Admin`, specifically to modify `protocol_origination_fee` among other fee parameters: [4](#0-3) 

The consequence is realized in `lending_account_borrow`, where the unprivileged borrower's liability is directly inflated by `origination_fee_rate` with no cap check on the rate itself: [5](#0-4) 

If `protocol_origination_fee` is set to `1.0` (100%) or higher, a borrower requesting `amount` receives only `amount_pre_fee` in tokens but is charged a liability of `amount_pre_fee + origination_fee` where `origination_fee = amount_pre_fee * origination_fee_rate`. At 100% this doubles the borrower's debt instantly for no additional received funds; at higher settings it can be made arbitrarily punitive. The excess fee is credited to `collected_group_fees_outstanding`/`collected_program_fees_outstanding`, i.e., value is redirected from the borrower to the group/program fee wallets: [6](#0-5) 

This is functionally identical to the report's root cause: a percentage-type parameter fully controlled by a privileged actor (DAO/admin) with no upper limit, applied against an unprivileged party's economic outcome (node runner reward share → here, borrower's borrowed principal/liability).

### Impact Explanation
A malicious or compromised group admin/Curve Admin can, immediately before or during normal operation, set `protocol_origination_fee` (or the other uncapped fee fields, which also affect ongoing interest accrual for depositors/borrowers) to values far exceeding sane bounds (e.g., 100%+). Any borrower transaction submitted afterward pays that fee with no on-chain protection, resulting in an immediate, unrecoverable loss of value redirected to group/program fee accounts. Because there's no admin-facing "sanity ceiling" enforced by the protocol itself, users relying on the last-known fee rate (as displayed by a front-end) have no on-chain guarantee that the rate they see is the rate applied to their next transaction — the same "front-running the fee update" concern raised by the judge in the original finding (fees change instantly and apply to any subsequent/unclaimed activity).

### Likelihood Explanation
Likelihood is moderate: it requires the group admin (or a delegated Curve Admin) to act maliciously or be compromised, which is the exact scenario explicitly called out as in-scope for this class of bug in the original report (the judge kept the finding valid specifically because DAO/admin compromise is in scope and its impact is immediate and applies broadly). No additional preconditions (e.g., oracle manipulation, race conditions) are needed beyond a single admin transaction followed by a normal user borrow.

### Recommendation
Enforce an explicit maximum bound on `protocol_origination_fee` (and ideally on the sum of all interest/fee components: `insurance_fee_fixed_apr + insurance_ir_fee + protocol_fixed_fee_apr + protocol_ir_fee`) inside `InterestRateConfigImpl::validate_seven_point()` (and `validate()`), rejecting configurations where these fees exceed a sane ceiling (e.g., 100% for origination fee, and a bounded total for ongoing interest fee components). This validation should run any time `update()` is invoked via `ConfigureBankLiteCurve`, so it can't be bypassed by direct field manipulation.

### Proof of Concept
1. Group admin (or delegated Curve Admin) calls `ConfigureBankLiteCurve` with `InterestRateConfigOpt { protocol_origination_fee: Some(1.0.into()), .. }` on a target bank.
2. `InterestRateConfigImpl::update()` sets `protocol_origination_fee = 1.0` with no validation rejecting the value (`validate_seven_point()` never inspects this field).
3. A borrower calls `lending_account_borrow` for `amount`. `origination_fee_rate = 1.0`, so `origination_fee = amount_pre_fee * 1.0 = amount_pre_fee`.
4. Borrower's liability share is created for `amount_pre_fee + origination_fee = 2 * amount_pre_fee`, while only `amount_pre_fee` tokens are transferred to them (`bank.withdraw_spl_transfer(amount_pre_fee, ...)`), per [7](#0-6) .
5. The extra `origination_fee` (equal to the entire borrowed amount) is credited to `collected_group_fees_outstanding`/`collected_program_fees_outstanding`, redirecting full value away from the borrower with no cap preventing this outcome.

### Citations

**File:** programs/marginfi/src/state/interest_rate.rs (L51-59)
```rust
    fn validate(&self) -> MarginfiResult {
        match self.curve_type {
            INTEREST_CURVE_LEGACY => self.validate_legacy()?,
            INTEREST_CURVE_SEVEN_POINT => self.validate_seven_point()?,
            _ => panic!("unsupported curve type"),
        }

        Ok(())
    }
```

**File:** programs/marginfi/src/state/interest_rate.rs (L66-126)
```rust
    /// * the rate at zero is the lowest
    /// * utils in points are in ascending order, and non-decreasing rates
    /// * no "holes" in points, all padding is at the end of the slice
    /// * the rate at 100% util is the highest
    fn validate_seven_point(&self) -> MarginfiResult {
        let zero = self.zero_util_rate;
        let hundred = self.hundred_util_rate;

        // Collect used points (util > 0), enforce trailing padding
        let mut used: Vec<RatePoint> = Vec::with_capacity(self.points.len());
        let mut seen_padding = false;
        for (i, p) in self.points.iter().enumerate() {
            if p.util == 0 {
                // Padding: must be (0,0); once seen, all following must be padding as well.
                if p.rate != 0 {
                    msg!("Expected padding (zero rate) at {:?}", i);
                    return err!(MarginfiError::InvalidConfig);
                }
                seen_padding = true;
            } else {
                // No "holes": non-zero util after padding is not allowed.
                if seen_padding {
                    msg!("Expected padding at {:?} (no holes permitted)", i);
                    return err!(MarginfiError::InvalidConfig);
                }

                used.push(*p);
            }
        }

        // Points must be strictly increasing in util, and non-decreasing in rate
        for i in 1..used.len() {
            let prev = &used[i - 1];
            let curr = &used[i];

            if prev.util >= curr.util {
                msg!("util not ascending between {:?} {:?}", i - 1, i);
                msg!("utils: {:?} {:?}", prev.util, curr.util);
                return err!(MarginfiError::InvalidConfig);
            }
            if prev.rate > curr.rate {
                msg!("rate is decreasing between {:?} {:?}", i - 1, i);
                msg!("rates: {:?} {:?}", prev.rate, curr.rate);
                return err!(MarginfiError::InvalidConfig);
            }
        }

        // rate at zero < rate at 100%, and for each point p, 0_rate <= p <= 100%_rate
        let zero_lte_hundred = zero <= hundred;
        if !zero_lte_hundred {
            msg!("The zero rate is higher than the hundred rate");
            return err!(MarginfiError::InvalidConfig);
        }
        let p_between_zero_hundred = used.iter().all(|p| zero <= p.rate && p.rate <= hundred);
        if !p_between_zero_hundred {
            msg!("A point is not between 0 and 100 rates");
            return err!(MarginfiError::InvalidConfig);
        }

        Ok(())
    }
```

**File:** programs/marginfi/src/state/interest_rate.rs (L128-149)
```rust
    fn update(&mut self, ir_config: &InterestRateConfigOpt) {
        set_if_some!(
            self.insurance_fee_fixed_apr,
            ir_config.insurance_fee_fixed_apr
        );
        set_if_some!(self.insurance_ir_fee, ir_config.insurance_ir_fee);
        set_if_some!(
            self.protocol_fixed_fee_apr,
            ir_config.protocol_fixed_fee_apr
        );
        set_if_some!(self.protocol_ir_fee, ir_config.protocol_ir_fee);
        set_if_some!(
            self.protocol_origination_fee,
            ir_config.protocol_origination_fee
        );
        set_if_some!(self.zero_util_rate, ir_config.zero_util_rate);
        set_if_some!(self.hundred_util_rate, ir_config.hundred_util_rate);
        set_if_some!(self.points, ir_config.points);

        // Note: If we ever support another curve type, this will become configurable.
        self.curve_type = INTEREST_CURVE_SEVEN_POINT;
    }
```

**File:** guides/ADMIN/PERMISSIONS_AND_ROLES.md (L62-77)
```markdown
### Delegate Curve Admin

A scoped admin that can modify interest rate configuration, including both curve parameters and
fee parameters within the interest rate config.

**Can do:**
- Modify curve parameters (`zero_util_rate`, `hundred_util_rate`, `points`) on any bank
- Modify interest rate fee parameters (`insurance_ir_fee`, `insurance_fee_fixed_apr`,
  `protocol_ir_fee`, `protocol_fixed_fee_apr`, `protocol_origination_fee`)
- All via `ConfigureBankLiteCurve` (which takes `InterestRateConfigOpt`)

Note: any update through this path forces the bank to the seven-point curve type. Changes are
blocked if the bank has `FREEZE_SETTINGS` enabled.

This role allows interest rate management to be delegated to a separate party (e.g. a rate
committee) without giving them access to weights, oracle config, or other bank settings.
```

**File:** programs/marginfi/src/instructions/marginfi_account/borrow.rs (L94-156)
```rust
        let origination_fee_rate: I80F48 = bank
            .config
            .interest_rate_config
            .protocol_origination_fee
            .into();

        let lending_account = &mut marginfi_account.lending_account;
        let mut bank_account =
            BankAccountWrapper::find_or_create(&bank_loader.key(), &mut bank, lending_account)?;

        // User needs to borrow amount + fee to receive amount
        amount_pre_fee = maybe_bank_mint
            .as_ref()
            .map(|mint| {
                utils::calculate_pre_fee_spl_deposit_amount(
                    mint.to_account_info(),
                    amount,
                    clock.epoch,
                )
            })
            .transpose()?
            .unwrap_or(amount);

        let (origination_fee_u64, share_amount): (u64, I80F48);
        if !origination_fee_rate.is_zero() {
            origination_fee = I80F48::from_num(amount_pre_fee)
                .checked_mul(origination_fee_rate)
                .ok_or_else(math_error!())?;
            origination_fee_u64 = origination_fee.checked_to_num().ok_or_else(math_error!())?;

            // Incurs a borrow that includes the origination fee (but withdraws just the amt)
            share_amount =
                bank_account.borrow(I80F48::from_num(amount_pre_fee) + origination_fee)?;
        } else {
            // Incurs a borrow for the amount without any fee
            origination_fee_u64 = 0;
            share_amount = bank_account.borrow(I80F48::from_num(amount_pre_fee))?;
        }

        let resulting_liability_shares: I80F48 = bank_account.balance.liability_shares.into();
        check!(
            resulting_liability_shares <= I80F48::ZERO
                || resulting_liability_shares >= EMPTY_BALANCE_THRESHOLD,
            MarginfiError::IllegalBalanceState,
            "Borrow would leave positive liability shares below the empty balance threshold"
        );

        marginfi_account.last_update = clock.unix_timestamp as u64;

        bank.withdraw_spl_transfer(
            amount_pre_fee,
            bank_liquidity_vault.to_account_info(),
            destination_token_account.to_account_info(),
            bank_liquidity_vault_authority.to_account_info(),
            maybe_bank_mint.as_ref(),
            token_program.to_account_info(),
            bank_signer!(
                BankVaultType::Liquidity,
                bank_loader.key(),
                liquidity_vault_authority_bump
            ),
            ctx.remaining_accounts,
        )?;
```

**File:** programs/marginfi/src/instructions/marginfi_account/borrow.rs (L173-201)
```rust
    // The program and/or group fee account gains the origination fee
    {
        let mut bank = bank_loader.load_mut()?;

        if !origination_fee.is_zero() {
            let mut bank_fees_after: I80F48 = bank.collected_group_fees_outstanding.into();

            if !program_fee_rate.is_zero() {
                // Some portion of the origination fee to goes to program fees
                let program_fee_amount: I80F48 = origination_fee
                    .checked_mul(program_fee_rate)
                    .ok_or_else(math_error!())?;
                // The remainder of the origination fee goes to group fees
                bank_fees_after = bank_fees_after
                    .saturating_add(origination_fee.saturating_sub(program_fee_amount));

                // Update the bank's program fees
                let program_fees_before: I80F48 = bank.collected_program_fees_outstanding.into();
                bank.collected_program_fees_outstanding = program_fees_before
                    .saturating_add(program_fee_amount)
                    .into();
            } else {
                // If program fee rate is zero, add the full origination fee to group fees
                bank_fees_after = bank_fees_after.saturating_add(origination_fee);
            }

            // Update the bank's group fees
            bank.collected_group_fees_outstanding = bank_fees_after.into();
        }
```
