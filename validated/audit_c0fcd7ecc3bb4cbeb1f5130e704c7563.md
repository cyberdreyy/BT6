### Title
`kamino_withdraw` undervalues withdrawals in group rate-limit and deleverage-cap USD accounting by treating collateral-token amount as liquidity-token amount - (File: programs/marginfi/src/instructions/kamino/withdraw.rs)

### Summary
`kamino_withdraw` computes the USD value used for the group-level outflow rate limiter and the deleverage withdrawal cap directly from the raw Kamino **collateral-token** amount, instead of converting it to the equivalent **liquidity-token** amount first. Because Kamino collateral tokens appreciate relative to the underlying liquidity token as interest accrues, this systematically understates the true USD value withdrawn, exactly mirroring the referenced Allora bug where an amount denominated in one unit (stake amount) was fed into a valuation function that expected a different unit (fee revenue), producing an incorrect protocol-level decision (topic activation there; rate-limit/deleverage-cap enforcement here).

### Finding Description
`kamino_withdraw` explicitly documents that its `amount` parameter is denominated in **collateral tokens**, not liquidity tokens, and that "collateral tokens appreciate in value relative to the liquidity token" [1](#0-0) . This is also stated in the integration guide: withdraw/liquidate use collateral-token amounts and "withdrawers must manually convert from collateral token amounts to liquidity token amounts using the current exchange rate" [2](#0-1) .

Inside `kamino_withdraw`, `rate_limit_amount` is set to the raw collateral amount (`amount`, or `collateral_amount` for `withdraw_all`) and passed straight into `record_withdrawal_outflow` as both `native_amount` and `balance_amount`: [3](#0-2) 

The same unconverted collateral amount is also used to compute `withdrawn_equity` for the deleverage withdraw limit check: [4](#0-3) 

`record_withdrawal_outflow` feeds this collateral-token amount directly into `calc_value(balance_amount, price, ...)` to derive the USD value checked against the group's hourly/daily rate limits: [5](#0-4) 

The `price` used here comes from `fetch_asset_price_for_bank_low_bias`, which reflects the underlying liquidity token's oracle price (e.g. USDC/SOL), not a collateral-token price. The correct conversion function, `collateral_to_liquidity`, exists and is used later in the same instruction only for the actual token transfer sanity check, not for the rate-limit/deleverage valuation: [6](#0-5) 

The docs confirm collateral tokens are worth *more* liquidity tokens than a 1:1 mapping once interest has accrued (round-trip test shows `collateral_to_liquidity` output is generally ≥ input in liquidity terms, growing with the reserve's exchange rate) [7](#0-6) , and integration tests explicitly compute `expected_liquidity_delta` via `collateral_to_liquidity(withdraw_amount)` to know the true withdrawn value [8](#0-7) .

This is the same bug class as the Allora report: an input meant for one accounting basis (collateral shares) is substituted for a different basis (liquidity-token/USD value) inside a function whose output gates a protocol-level guardrail (there: topic activation weight threshold; here: group USD rate limiter and deleverage withdrawal cap).

### Impact Explanation
Both the group-level rate limiter (`hourly`/`daily` outflow USD caps, `configure_group_rate_limits`) and the deleverage withdrawal daily USD cap (`configure_deleverage_withdrawal_limit`) are risk-mitigation guardrails intended to bound net USD outflow from a group. By valuing a Kamino withdrawal using the raw collateral-token count instead of the true liquidity-token equivalent, the accounted USD value is understated by the accrued interest factor (which grows over the reserve's lifetime and can be significant for long-lived, high-utilization reserves). This lets:
- Users/attackers extract more actual USD value per hour/day from a rate-limited group than the configured cap permits, undermining the anti-drain defense the rate limiter exists to provide.
- Forced deleverage withdrawals (used as "a defense if the risk workflow is abused or compromised") to exceed the configured daily USD cap without triggering `check_deleverage_withdraw_limit`'s failure, defeating its stated purpose.

This is a financially meaningful bypass of a security control on unprivileged, permissionless user-facing paths (`kamino_withdraw`), not merely cosmetic.

### Likelihood Explanation
The mis-valuation triggers on every `kamino_withdraw` call once the underlying Kamino reserve has accrued any interest (the normal, expected state of an active reserve), and does not require any privileged role — any ordinary marginfi user with a Kamino-wrapped position can trigger it. The magnitude of the discrepancy grows with reserve age/utilization, so it becomes increasingly impactful the longer a bank operates, making exploitation realistic without special preconditions beyond group/deleverage rate limiting being enabled (a supported, documented configuration).

### Recommendation
Before computing `rate_limit_amount` / `withdrawn_equity` in `kamino_withdraw`, convert the collateral-token amount to its liquidity-token equivalent using the same `collateral_to_liquidity` conversion already used for the transfer sanity check, and pass that liquidity-equivalent amount (not the raw collateral amount) into `record_withdrawal_outflow` and into the `calc_value` call feeding `check_deleverage_withdraw_limit`.

### Proof of Concept
1. Set up a group with `configure_group_rate_limits` (e.g. daily USD cap = $1,000) and/or `configure_deleverage_withdrawal_limit`, and a Kamino-wrapped bank whose reserve has accrued meaningful interest so that `collateral_to_liquidity(x) > x` by a non-trivial margin (e.g. 20%).
2. As a normal user with a Kamino position, call `kamino_withdraw` repeatedly with collateral amounts sized so that the *raw collateral amount* stays under the configured USD cap when priced directly (as the current code computes), while the *true liquidity-equivalent value* (`collateral_to_liquidity(amount) * price`) exceeds it.
3. Observe that the rate limiter / deleverage cap does not reject these withdrawals even though the actual USD value extracted from the group exceeds the configured daily limit, confirming the guardrail is bypassed via the collateral/liquidity unit mismatch identified in `record_withdrawal_outflow` at [5](#0-4)  and the deleverage check at [4](#0-3) .

### Citations

**File:** programs/marginfi/src/instructions/kamino/withdraw.rs (L52-70)
```rust
///
/// # Important Note on Token Amounts:
/// The `amount` parameter is specified in terms of COLLATERAL tokens, not the underlying
/// liquidity tokens (e.g., USDC). This is important for users to understand.
///
/// Collateral tokens represent shares in the Kamino reserve. When withdrawing:
///
/// 1. The user specifies how many collateral tokens they want to withdraw.
///
/// 2. Kamino calculates the corresponding amount of liquidity tokens (e.g., USDC)
///    to return based on the current exchange rate in the Kamino reserve.
///
/// 3. If a user wants to withdraw a specific amount of liquidity tokens, they need
///    to calculate the required collateral tokens themselves using the reserve's current
///    exchange rate before making the withdrawal request.
///
/// 4. For withdrawing an entire position, use the `withdraw_all` option instead of
///    trying to calculate the exact amount.
///
```

**File:** programs/marginfi/src/instructions/kamino/withdraw.rs (L138-163)
```rust
        (collateral_amount, share_amount) = if withdraw_all {
            bank_account.withdraw_all(in_receivership)?
        } else {
            let share_amount = bank_account.withdraw(I80F48::from_num(amount))?;
            (amount, share_amount)
        };

        // Rate limiting tracks net outflow; skip for flashloan/liquidation/deleverage flows.
        let rate_limit_amount = if withdraw_all {
            collateral_amount
        } else {
            amount
        };

        record_withdrawal_outflow(
            group_rate_limit_enabled,
            rate_limit_amount,
            rate_limit_amount,
            price,
            &mut bank,
            &group,
            ctx.accounts.group.key(),
            ctx.accounts.bank.key(),
            &marginfi_account,
            &clock,
        )?;
```

**File:** programs/marginfi/src/instructions/kamino/withdraw.rs (L164-180)
```rust
        // Note: we only care about the withdraw limit in case of deleverage
        if marginfi_account.get_flag(ACCOUNT_IN_DELEVERAGE) {
            let withdrawn_equity = calc_value(
                I80F48::from_num(collateral_amount),
                price,
                bank.get_balance_decimals(),
                None,
            )?;
            group.check_deleverage_withdraw_limit(withdrawn_equity, clock.unix_timestamp)?;
            emit!(DeleverageWithdrawFlowEvent {
                group: ctx.accounts.group.key(),
                bank: ctx.accounts.bank.key(),
                mint: bank.mint,
                outflow_usd: withdrawn_equity.to_num(),
                current_timestamp: clock.unix_timestamp,
            });
        }
```

**File:** programs/marginfi/src/instructions/kamino/withdraw.rs (L192-207)
```rust
    let expected_liquidity_amount = ctx
        .accounts
        .integration_acc_1
        .load()?
        .collateral_to_liquidity(collateral_amount)?;

    ctx.accounts.cpi_kamino_withdraw(collateral_amount)?;

    // Really just a sanity check, vault balance change is more important
    let final_deposit_amount = ctx.accounts.integration_acc_2.load()?.deposits[0].deposited_amount;
    let actual_deposit_decrease = initial_deposit_amount - final_deposit_amount;
    require_eq!(
        actual_deposit_decrease,
        collateral_amount,
        MarginfiError::KaminoWithdrawFailed
    );
```

**File:** guides/DEVELOPERS_INTEGRATORS/KAMINO_INTEGRATION.md (L112-124)
```markdown
## Token Amount Types by Instruction

| Instruction | Token Amount Type | Notes |
|-------------|------------------|-------|
| Deposit | Liquidity token amount | Raw underlying token (e.g., USDC, SOL) |
| Withdraw | Collateral token amount | Must convert from collateral to liquidity token amount |
| Liquidate | Collateral token amount | Must convert from collateral to liquidity token amount |

**Important:** Deposit operations accept liquidity token amounts (the underlying asset), while
withdraw and liquidate operations work with collateral token amounts. Since collateral tokens
appreciate in value relative to the liquidity token as interest accumulates, liquidators and
withdrawers must manually convert from collateral token amounts to liquidity token amounts using the
current exchange rate.
```

**File:** programs/marginfi/src/utils/general.rs (L483-512)
```rust
        // Group-level rate limiting: read-only validation + event emission.
        // The admin aggregates events off-chain and calls update_group_rate_limiter.
        if group_rate_limit_enabled {
            check!(price > I80F48::ZERO, MarginfiError::InvalidRateLimitPrice);

            let value = calc_value(
                I80F48::from_num(balance_amount),
                price,
                bank.get_balance_decimals(),
                None,
            )?;
            if group.rate_limiter.hourly.is_enabled() {
                let remaining = group
                    .rate_limiter
                    .hourly
                    .effective_remaining_capacity(clock.unix_timestamp);
                if value.to_num::<i64>() > remaining {
                    return Err(MarginfiError::GroupHourlyRateLimitExceeded.into());
                }
            }
            if group.rate_limiter.daily.is_enabled() {
                let remaining = group
                    .rate_limiter
                    .daily
                    .effective_remaining_capacity(clock.unix_timestamp);
                if value.to_num::<i64>() > remaining {
                    return Err(MarginfiError::GroupDailyRateLimitExceeded.into());
                }
            }

```

**File:** programs/kamino-mocks/src/state.rs (L123-159)
```rust
// Notable Kamino naming conventions:
// * `mint_total_supply` aka `total_col` - total amount of collateral tokens that exist
// * `total_supply` aka `total_liq` - total amount of liquidity tokens under the reserve's control
impl MinimalReserve {
    /// Returns `(total_liquidity_tokens, total_collateral_tokens)` both in “no-decimals” I80F48
    /// form (i.e. scaled down by 10^mint_decimals).
    pub fn scaled_supplies(&self) -> Result<(I80F48, I80F48)> {
        let total_liq_raw = self.calculate_total_supply_i80f48();
        let (total_liq, total_col) = scale_supplies(
            total_liq_raw,
            self.mint_total_supply,
            self.mint_decimals as u8,
        )
        .ok_or_else(math_error!())?;
        Ok((total_liq, total_col))
    }

    // Note: our conversion has less precision than Kamino's internal representation (which uses
    //  U256 to avoid any precision loss), but sufficient for our purposes because we only use these
    //  to sanity check that the user got the expected amount of tokens +/- 1 when
    //  depositing/withdrawing

    /// Convert collateral tokens to equivalent liquidity tokens
    /// * Returns liquidity tokens (uses `mint_decimals`)
    pub fn collateral_to_liquidity(&self, collateral: u64) -> Result<u64> {
        let (total_liq, total_col) = self.scaled_supplies()?;
        collateral_to_liquidity_from_scaled(collateral, total_liq, total_col)
            .ok_or(KaminoMocksError::MathError.into())
    }

    /// Convert liquidity tokens to equivalent value in collateral token.
    /// * Returns collateral equivalent (in `mint_decimals`)
    pub fn liquidity_to_collateral(&self, liquidity: u64) -> Result<u64> {
        let (total_liq, total_col) = self.scaled_supplies()?;
        liquidity_to_collateral_from_scaled(liquidity, total_liq, total_col)
            .ok_or(KaminoMocksError::MathError.into())
    }
```

**File:** programs/marginfi/tests/user_actions/kamino.rs (L99-126)
```rust
    let post = setup.load_state(&user_token).await;
    let post_accounted = setup
        .load_user_accounted_collateral(&user)
        .await
        .expect("kamino bank balance should remain active after partial withdraw");

    let expected_liquidity_delta = pre_reserve.collateral_to_liquidity(withdraw_amount)? as i128;
    let actual_user_liquidity_delta = post.user_balance as i128 - pre.user_balance as i128;
    let actual_reserve_liquidity_delta =
        pre.reserve_supply_balance as i128 - post.reserve_supply_balance as i128;
    let actual_obligation_delta =
        pre.obligation_collateral as i128 - post.obligation_collateral as i128;
    let actual_accounted_delta = pre_accounted as i128 - post_accounted as i128;

    assert_eq_with_tolerance!(
        actual_user_liquidity_delta,
        expected_liquidity_delta,
        KAMINO_ROUNDING_TOLERANCE_NATIVE as i128
    );
    assert_eq_with_tolerance!(
        actual_reserve_liquidity_delta,
        expected_liquidity_delta,
        KAMINO_ROUNDING_TOLERANCE_NATIVE as i128
    );
    assert_eq!(
        actual_obligation_delta, withdraw_amount as i128,
        "obligation collateral burn should match requested collateral amount"
    );
```
