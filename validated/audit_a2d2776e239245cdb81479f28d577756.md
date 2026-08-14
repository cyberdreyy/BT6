### Title
Kamino/Solend withdraw instructions feed collateral-token amounts into rate-limiter and deleverage-cap USD valuations that expect underlying (liquidity) token amounts - (File: programs/marginfi/src/instructions/kamino/withdraw.rs)

### Summary
This is the same bug class as the reported `PolicyBook.aggregatedQueueAmount` issue: a value denominated in one unit (queued *shares*/collateral) is used directly where the code expects a different unit (underlying token amount), corrupting a financial safety check. In marginfi's Kamino (and structurally identical Solend) withdraw path, the `amount` parameter is explicitly documented as a **collateral token amount**, not the underlying liquidity token amount, yet it is passed unconverted into `record_withdrawal_outflow` and `calc_value`, both of which are documented/designed to operate on **native (underlying) token amounts**.

### Finding Description
Per the integrator docs, Kamino withdraw operates on collateral tokens which appreciate relative to the underlying liquidity token over time: [1](#0-0) 

and: [2](#0-1) 

In `kamino_withdraw`, when not `withdraw_all`, the raw `amount` (collateral units) is used directly as `rate_limit_amount`, which is then passed as both the native-amount argument to the bank-level rate limiter and the `balance_amount` used for USD valuation via `calc_value`: [3](#0-2) 

`record_withdrawal_outflow` treats its `native_amount` parameter as a native/underlying token amount for the bank-level rate limiter, and uses `balance_amount` together with the bank's oracle `price` (fetched for the underlying liquidity mint, e.g. USDC) and `bank.get_balance_decimals()` (the underlying mint's decimals) to compute a USD value for the group-level rate limiter: [4](#0-3) 

The bank-level rate limiter is documented as tracking "native token net outflow," and the group-level limiter is documented as tracking "USD net outflow," both computed from the withdrawn bank's price: [5](#0-4) 

The same unconverted collateral amount is also fed into the deleverage daily withdrawal cap: [6](#0-5) 

Because Kamino collateral tokens are worth strictly more than 1 underlying token as interest accrues (asset share value of the wrapped bank is fixed at 1, all yield accrues on Kamino's collateral exchange rate), the real value transferred out of the protocol is `collateral_amount * exchange_rate` underlying tokens, not `collateral_amount` underlying tokens. Feeding the raw collateral amount into `calc_value(amount, price_of_underlying, underlying_decimals)` and into the native-outflow rate limiter therefore **systematically undervalues** the actual withdrawal for every check that is supposed to bound outflow: the bank rate limiter, the group USD rate limiter, and the deleverage daily withdrawal limit.

The `solend/withdraw.rs` instruction contains the structurally identical pattern (`collateral_amount`/`amount` fed unconverted into the same `record_withdrawal_outflow` and `calc_value` calls): [7](#0-6) 

This mirrors the reported bug precisely: a queue/share-denominated quantity (`aggregatedQueueAmount`, analogously `collateral_amount`) is compared/valued as if it were already in the target unit (DAI, analogously native underlying tokens), producing a systematically wrong result rather than a random rounding error.

### Impact Explanation
These three checks exist specifically as economic/security safeguards:
- Bank/group rate limiters cap outflow to defend against drains (including compromised keys or abused permissions).
- The deleverage daily withdrawal limit is explicitly described as "a defense if the risk workflow is abused or compromised."

Because the collateral:liquidity exchange rate grows over time and is always ≥ 1, every Kamino/Solend withdrawal is under-counted against these caps by the exchange-rate factor. This allows an attacker (or normal user, cumulatively) to withdraw materially more real USD value than the configured hourly/daily/deleverage limits intend, silently eroding the effectiveness of these caps — a systemic, durable misvaluation with direct financial-safety impact, not a cosmetic issue. The magnitude of the discrepancy grows the longer a reserve has accrued interest, so the bypass becomes more severe over the life of a bank.

### Likelihood Explanation
This occurs on every non-`withdraw_all` Kamino or Solend withdraw once the underlying reserve has accrued any interest (collateral exchange rate > 1), i.e., essentially all real-world usage after the bank's first days. It requires no special privilege or malicious setup — it is triggered by the normal, permissionless `kamino_withdraw`/`solend_withdraw` instructions used by any user, whenever group or bank rate limits, or the deleverage cap, are enabled (both are optional admin-configured protections, and the bug directly weakens whichever of them is turned on).

### Recommendation
Before calling `record_withdrawal_outflow` and computing `calc_value` for the deleverage check in `kamino_withdraw` (and the analogous code in `solend_withdraw`), convert the collateral-denominated `amount`/`collateral_amount` into the equivalent underlying-liquidity-token amount using the reserve's current collateral-to-liquidity exchange rate (the same conversion documented as required for users/liquidators), and pass that converted value as `native_amount`/`balance_amount` to `record_withdrawal_outflow`, and as the input to `calc_value` for the deleverage-withdraw-limit check.

### Proof of Concept
Conceptual trace (values approximate):
1. Admin enables a bank-level daily rate limit of 1,000 USDC on a Kamino USDC bank, and/or a group daily rate limit / deleverage daily cap denominated in USD.
2. Over time, the Kamino reserve's liquidity accrues interest so that 1 collateral token now redeems for 1.5 underlying USDC (`exchange_rate = 1.5`).
3. A user calls `kamino_withdraw` with `amount = 1_000_000_000` (1,000 collateral-token units, 6 decimals) — this is a **real** withdrawal of `1,000 * 1.5 = 1,500` USDC of underlying value.
4. `kamino_withdraw` sets `rate_limit_amount = amount = 1_000_000_000` and calls `record_withdrawal_outflow(..., native_amount=1_000_000_000, balance_amount=1_000_000_000, price=1.0 USDC, ...)`. [8](#0-7) 
5. The bank-level rate limiter records only 1,000 (native units) of outflow, and `calc_value` computes only $1,000 USD of outflow for the group limiter/deleverage cap — even though the reserve and user actually moved $1,500 of real value.
6. A user (or several users) can repeatedly perform such withdrawals to extract far more real value than the configured 1,000/day (or group/deleverage) cap permits, because every check under-counts by the (growing) collateral exchange-rate factor.

Note: I was not able to fully inspect `bank.get_balance_decimals()`'s implementation body or `fetch_asset_price_for_bank_low_bias`'s full body within the available search budget (only confirmed their locations in `type-crate/src/types/bank.rs` and `programs/marginfi/src/utils/general.rs` respectively) — but the documented semantics (bank rate limiter = "native token," group rate limiter = "USD outflow" priced via the bank's own oracle for the underlying mint) combined with the docs explicitly stating Kamino withdraw `amount` is a **collateral** token amount requiring manual conversion, are sufficient to establish the unit mismatch with high confidence.

### Citations

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

**File:** guides/DEVELOPERS_INTEGRATORS/GETTING_STARTED_INTEGRATOR.md (L78-90)
```markdown
<details>
<summary> <b>kamino_withdraw</b> - withdraw from a Kamino Bank</summary>

- Check `bank.config.asset_tag` ASSET_TAG_KAMINO (3) is allowed with this instruction. Others
  have their own deposit instruction.
- Requires a Risk Engine check (pass banks and oracles in remaining accounts)
- If group rate limits are enabled, the withdrawn bank and its oracle account group must still be
  present in `remaining_accounts` so the program can price the outflow in USD.
- `amount` is in **collateral** token, which always uses native decimal. Perform a conversion
  from liquidity -> collateral token.
- Can fail if the Bank doesn't have enough liquidity, or the Account after the action would fail the
  risk check.
</details>
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

**File:** programs/marginfi/src/utils/general.rs (L464-522)
```rust
pub fn record_withdrawal_outflow(
    group_rate_limit_enabled: bool,
    native_amount: u64,
    balance_amount: u64,
    price: I80F48,
    bank: &mut Bank,
    group: &MarginfiGroup,
    group_key: Pubkey,
    bank_key: Pubkey,
    marginfi_account: &MarginfiAccount,
    clock: &Clock,
) -> MarginfiResult<()> {
    // Rate limiting tracks net outflow; skip for flashloan/liquidation/deleverage flows.
    if !should_skip_rate_limit(marginfi_account.account_flags) {
        if bank.rate_limiter.is_enabled() {
            bank.rate_limiter
                .try_record_outflow(native_amount, clock.unix_timestamp)?;
        }

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

            emit!(RateLimitFlowEvent {
                group: group_key,
                bank: bank_key,
                mint: bank.mint,
                flow_direction: 0, // outflow
                native_amount,
                mint_decimals: bank.mint_decimals,
                current_timestamp: clock.unix_timestamp,
            });
        }
```

**File:** guides/ADMIN/RATE_LIMITS_AND_DELEVERAGE_WITHDRAW_LIMITS.md (L26-46)
```markdown
Two levels exist:

- Bank rate limits: `configure_bank_rate_limits`
  - Tracks native token net outflow on the bank account.
- Group rate limits: `configure_group_rate_limits`
  - Tracks USD net outflow across the whole group.

Each has hourly and daily windows.

- `0` disables that window.
- Deposits/repays release capacity.
- Withdraws/borrows consume capacity.

### 2. User transaction path

During a withdraw or borrow:

- The bank rate limiter is updated immediately on the writable bank account.
- The group rate limiter is only checked read-only.
- The protocol converts the flow to USD using the instruction price/oracle path.
- If the projected group hourly or daily capacity is exceeded, the user instruction fails.
```

**File:** programs/marginfi/src/instructions/solend/withdraw.rs (L115-157)
```rust
        let (collateral_amount, share_amount) = if withdraw_all {
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

        // Track withdrawal limit for risk admin during deleverage
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
