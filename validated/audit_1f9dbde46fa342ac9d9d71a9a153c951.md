### Title
Group rate-limiter USD accounting uses low-biased oracle price, systematically undercounting real withdrawal value and letting real token outflow exceed the intended hourly/daily USD cap - (File: `programs/marginfi/src/utils/general.rs`)

### Summary
`record_withdrawal_outflow` computes the USD `value` used to check the group-level hourly/daily rate limiter via `calc_value(balance_amount, price, ...)`, where `price` is supplied by callers using `fetch_asset_price_for_bank_low_bias`, which applies `PriceBias::Low` (subtracting the confidence interval). Because the group limiter compares this deflated `value` against `effective_remaining_capacity` (a USD-denominated cap), a withdrawer can repeatedly extract real native tokens whose true/mid market value exceeds the cap while the accounted `value` stays under it, each time the oracle confidence interval is non-trivial and near its bound.

### Finding Description
In `lending_account_withdraw` (`programs/marginfi/src/instructions/marginfi_account/withdraw.rs:80-96`), the price used for rate-limiter accounting is fetched with `fetch_asset_price_for_bank_low_bias`, which calls `pf.get_price_of_type(OraclePriceType::RealTime, Some(PriceBias::Low), bank.config.oracle_max_confidence)` (`programs/marginfi/src/utils/general.rs:321-336`). `PriceBias::Low` subtracts the (capped) confidence interval from the mid-price, producing a conservative *lower* price — a bias designed for solvency/health checks where undervaluing collateral is the safe direction.

This same low-biased price is passed straight into `record_withdrawal_outflow` (`programs/marginfi/src/utils/general.rs:464-525`), which uses it in `calc_value(I80F48::from_num(balance_amount), price, bank.get_balance_decimals(), None)` to derive `value`, then checks `value.to_num::<i64>() > remaining` against `group.rate_limiter.hourly/daily.effective_remaining_capacity(...)` (`programs/marginfi/src/state/rate_limiter.rs:44,110-126`). Since `value` is deliberately deflated relative to the true/mid oracle price, the accounted USD outflow is always less than or equal to the real USD value of the tokens actually transferred out via `bank.withdraw_spl_transfer` later in the same instruction. An attacker does not need to manipulate anything beyond normal oracle noise: whenever the live confidence interval is non-zero (which is the normal case for any real price feed, capped by `bank.config.oracle_max_confidence`), every withdrawal underreports its true value to the group limiter by up to the confidence-interval discount. Repeating withdrawals within the same hourly/daily window compounds this gap, letting the attacker drain a real dollar amount larger than `max_outflow` while `effective_remaining_capacity` never goes negative from the limiter's own bookkeeping.

This is a systematic accounting/design flaw in the rate limiter's price-selection logic, not third-party bad oracle data — the oracle itself may be perfectly within bounds; the issue is that the same conservative bias built for health/solvency checks is reused for a control (the outflow-value cap) that is supposed to reflect real economic value moved.

### Impact Explanation
The group hourly/daily rate limiter is a fund-drain circuit breaker; its purpose is to bound the real USD value that can leave the protocol in a given window. Systematically undercounting outflow value defeats that purpose: an attacker (or any ordinary user, since no privileged action is required) can extract more real value than the configured cap permits, and can do so repeatedly across the fleet of pooled banks in the group. This is a rate-limiter circumvention causing outsized fund drain, matching the "Critical" scope described in the prompt (PRICE_CONSERVATISM tied to rate-limiter accounting).

### Likelihood Explanation
This does not require any oracle manipulation or admin/governance action — only that the oracle's normal confidence interval be non-zero, which is true for virtually all live Pyth/Switchboard feeds. Any unprivileged user with a funded position can trigger `lending_account_withdraw` repeatedly within an hourly/daily window with `group.rate_limiter` enabled, so the precondition is trivially met in production whenever the group rate limiter feature is turned on. The magnitude of the gap is bounded by `bank.config.oracle_max_confidence`, so its exploitability scales with how wide that admin-configured bound is, but even modest confidence bounds (a few basis points to a percent) compound over many transactions in the same window.

### Recommendation
Use an unbiased (mid/real) price — e.g. `fetch_unbiased_price_for_bank` — for the `calc_value` computation feeding the group/bank rate-limiter checks in `record_withdrawal_outflow`, reserving `PriceBias::Low`/`PriceBias::High` exclusively for solvency/health-check paths. If a bias must be retained for conservatism in the limiter direction, use `PriceBias::High` for outflow accounting (so the limiter is conservative against underreporting the value leaving the protocol) instead of `PriceBias::Low`.

### Proof of Concept
Rust unit/integration test plan:
1. Construct a `Bank` with a `PythPushOracle` feed configured with a known mid price `P` and non-zero confidence `C` within `bank.config.oracle_max_confidence`.
2. Construct a `MarginfiGroup` with `rate_limiter.hourly` enabled with `max_outflow = M` (USD, scaled).
3. Call `fetch_asset_price_for_bank_low_bias` to obtain `price_low = P - bias(C)` and separately compute the true/mid price `P`.
4. Repeatedly call `record_withdrawal_outflow` with `balance_amount` chosen such that `calc_value(balance_amount, price_low, decimals, None) <= remaining` at each step (so the limiter never rejects), summing native token amounts withdrawn across the window.
5. Assert: `sum(native_amount * P) / 10^decimals > M` — i.e., the true USD value extracted over the hourly window exceeds the configured cap `M`, even though every individual call passed the `effective_remaining_capacity` check using `price_low`.
6. Fuzz `C` within `[0, bank.config.oracle_max_confidence]` and `balance_amount` per call, asserting the invariant "cumulative real USD outflow (computed at true price) is bounded by `M`" fails while the limiter's own `value`-based accounting reports staying within `M`.

<cite repo="EzraCole/marginfi-v2--017" path="programs/marginfi/src/utils/general.rs" start="317="336" end="336" /> [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** programs/marginfi/src/utils/general.rs (L464-525)
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
    }
    Ok(())
}
```

**File:** programs/marginfi/src/instructions/marginfi_account/withdraw.rs (L77-96)
```rust
        // Fetch oracle price for rate limiting and deleverage tracking
        // When group rate limiter is enabled, oracle is required
        let group_rate_limit_enabled = group.rate_limiter.is_enabled();
        let price = if in_receivership_or_order_execution || group_rate_limit_enabled {
            let price = fetch_asset_price_for_bank_low_bias(
                &bank_loader.key(),
                &bank,
                &clock,
                ctx.remaining_accounts,
            )?;

            // Validate price is non-zero during liquidation/deleverage to prevent exploits
            if in_receivership_or_order_execution {
                check!(price > I80F48::ZERO, MarginfiError::ZeroAssetPrice);
            }

            price
        } else {
            I80F48::ZERO
        };
```

**File:** programs/marginfi/src/state/rate_limiter.rs (L96-126)
```rust
    fn remaining_capacity(&self, current_timestamp: i64) -> i64 {
        if !self.is_enabled() {
            return i64::MAX;
        }
        remaining_capacity_from_state(
            self.max_outflow,
            self.window_duration,
            self.window_start,
            self.prev_window_outflow,
            self.cur_window_outflow,
            current_timestamp,
        )
    }

    fn effective_remaining_capacity(&self, current_timestamp: i64) -> i64 {
        if !self.is_enabled() {
            return i64::MAX;
        }

        let (window_start, prev_window_outflow, cur_window_outflow) =
            effective_window_state(self, current_timestamp);

        remaining_capacity_from_state(
            self.max_outflow,
            self.window_duration,
            window_start,
            prev_window_outflow,
            cur_window_outflow,
            current_timestamp,
        )
    }
```
