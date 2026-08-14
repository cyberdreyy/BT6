Confirmed root cause with exact line numbers. This is enough to finalize the analog write-up.

### Title
Rate limiter cross-window netting error inflates remaining outflow capacity, bypassing bank/group borrow and withdraw caps - (File: `programs/marginfi/src/state/rate_limiter.rs`)

### Summary
Marginfi's `RateLimitWindow` implements a two-bucket (`prev_window_outflow`/`cur_window_outflow`) sliding-window limiter for bank-level (native token) and group-level (USD) borrow/withdraw caps [1](#0-0) . `try_record_outflow` charges `cur_window_outflow`, while `record_inflow` (called on deposit/repay) always subtracts from `cur_window_outflow` regardless of which bucket the original outflow lives in [2](#0-1) . Remaining capacity is computed from a linearly-decaying weighted blend of `prev_window_outflow` plus the raw `cur_window_outflow` [3](#0-2) . Because the offsetting credit in `cur_window_outflow` does not decay in step with the weight applied to `prev_window_outflow`, an outflow charged just before a window boundary and repaid just after it produces a capacity credit that grows over the remainder of the window, eventually exceeding the configured cap.

### Finding Description
`maybe_advance_window` rolls `cur_window_outflow` into `prev_window_outflow` when a window boundary is crossed [4](#0-3) . If a user borrows/withdraws `amount` in the last instant of window N, it lands in `cur_window_outflow`; once the boundary passes it becomes `prev_window_outflow = amount` in window N+1. A repay/deposit of `amount` immediately after the boundary calls `record_inflow`, which subtracts `amount` from the *new* `cur_window_outflow`, making it `-amount` [5](#0-4) .

`remaining_capacity_from_state` then computes:
```
weighted_prev = prev_window_outflow * (window_duration - elapsed) / window_duration
total_net_outflow = weighted_prev + cur_window_outflow
remaining = max_outflow - total_net_outflow
``` [6](#0-5) 

Right after the boundary (`elapsed ≈ 0`), `weighted_prev ≈ amount` and `cur_window_outflow = -amount`, so `total_net_outflow ≈ 0` and `remaining ≈ max_outflow` — capacity is correctly restored. But `weighted_prev` decays linearly toward `0` as `elapsed` grows toward `window_duration`, while `cur_window_outflow` stays pinned at `-amount` (no further decay mechanism exists for it). As a result `total_net_outflow` drifts from `0` down to `-amount`, so `remaining` grows from `max_outflow` up to `max_outflow + amount` by the end of the window. This lets the same account (or any account against the shared bank/group limiter) draw an *additional* `amount` of outflow within that window on top of the configured cap, purely by timing a borrow/withdraw at the tail of one window and repaying/depositing at the head of the next.

This affects both `BankRateLimiter` (native token amounts, checked/updated inline in `record_withdrawal_outflow`) [7](#0-6)  and `GroupRateLimiter` (USD, checked read-only via `effective_remaining_capacity` in the same function and settled later by the admin) [8](#0-7) , since both share the identical `RateLimitWindow`/`impl_dual_window_rate_limiter!` machinery [9](#0-8) .

### Impact Explanation
Rate limits exist specifically to cap net outflow (borrow/withdraw exposure) per hour/day as a circuit breaker against runaway drains, oracle manipulation windows, or bad-debt cascades [10](#0-9) . The netting flaw lets any permissionless user bypass the configured hourly/daily cap by an amount up to the size of a single prior outflow, simply by timing a borrow/withdraw at the end of one window and a repay/deposit at the start of the next, then waiting out the rest of the window before drawing again. Since bank-level limits gate real token outflow directly and group-level limits gate the last line of defense across an entire group, this undermines the intended risk-management guarantee with direct financial exposure (over-cap borrowing/withdrawal), without needing to keep capital locked.

### Likelihood Explanation
The trigger requires only ordinary permissionless actions (borrow/withdraw then repay/deposit) timed around a window boundary — no privileged role, oracle manipulation, or race condition is needed, and the exact boundary timing is public (window durations/start are on-chain state). Any protocol relying on `hourly`/`daily` rate limits being enforced as strict caps is affected whenever an attacker chooses to time flows this way.

### Recommendation
Track the offsetting inflow against the bucket it originated from (or timestamp-tag outflows so inflows unwind the correct bucket first), rather than always crediting `cur_window_outflow`. Alternatively, decay the inflow credit with the same weighting applied to `prev_window_outflow` so the net exposure calculation remains monotonic and never exceeds `max_outflow` regardless of when the offsetting inflow occurs relative to the window boundary. Add a regression test that borrows near a window boundary, repays just after it, and asserts `effective_remaining_capacity`/`remaining_capacity` never exceeds `max_outflow` at any later point in the same window.

### Proof of Concept
Using `RateLimitWindow` directly (mirrors the unit tests in `programs/marginfi/src/state/rate_limiter.rs`):
1. `window.initialize(max_outflow = 1000, window_duration = 3600, current_timestamp = 0)`.
2. At `t = 3599`: `window.try_record_outflow(1000, 3599)` succeeds; `remaining_capacity(3599) == 0`. `cur_window_outflow = 1000`.
3. At `t = 3601` (boundary crossed): `window.record_inflow(1000, 3601)` → `maybe_advance_window` sets `prev_window_outflow = 1000`, `cur_window_outflow = 0`, `window_start = 3600`; then inflow sets `cur_window_outflow = -1000`.
4. `remaining_capacity(3601)`: `elapsed = 1`, `weighted_prev ≈ 999.72`, `total_net_outflow ≈ -0.28`, `remaining ≈ 1000` (correct, as expected).
5. At `t = 7199` (just before the *next* boundary, no further transactions): `elapsed = 3599`, `weighted_prev = 1000 * (3600-3599)/3600 ≈ 0.28`, `total_net_outflow ≈ 0.28 - 1000 = -999.72`, `remaining ≈ 1999.72`.
6. At this point `window.try_record_outflow(1999, 7199)` succeeds even though `max_outflow = 1000`, proving the configured hourly cap has been bypassed by nearly 2x using only a single borrow/repay pair timed around one window boundary. [3](#0-2)

### Citations

**File:** type-crate/src/types/rate_limiter.rs (L10-37)
```rust
/// A sliding window rate limiter that tracks net outflow over a time window.
/// Uses weighted blend of previous and current windows for smooth transitions.
///
/// Net outflow = (withdraws + borrows) - (deposits + repays).
/// A negative net outflow increases remaining capacity for subsequent outflows.
#[repr(C)]
#[cfg_attr(feature = "anchor", derive(AnchorDeserialize, AnchorSerialize))]
#[derive(Clone, Copy, Default, Zeroable, Pod, Debug, PartialEq, Eq)]
pub struct RateLimitWindow {
    /// Maximum net outflow allowed per window (0 = disabled).
    /// For bank-level: denominated in native tokens.
    /// For group-level: denominated in USD.
    pub max_outflow: u64,

    /// Window duration in seconds (e.g., 3600 for hourly, 86400 for daily).
    pub window_duration: u64,

    /// Unix timestamp when the current window started.
    pub window_start: i64,

    /// Net outflow accumulated in the previous window.
    /// Signed to allow tracking when inflows exceed outflows.
    pub prev_window_outflow: i64,

    /// Net outflow accumulated in the current window.
    /// Signed to allow tracking when inflows exceed outflows.
    pub cur_window_outflow: i64,
}
```

**File:** programs/marginfi/src/state/rate_limiter.rs (L66-94)
```rust
    fn maybe_advance_window(&mut self, current_timestamp: i64) {
        if !self.is_enabled() || self.window_duration == 0 {
            return;
        }

        let elapsed = current_timestamp.saturating_sub(self.window_start);
        if elapsed < 0 {
            return;
        }

        let elapsed = elapsed as u64;

        if elapsed >= self.window_duration * 2 {
            // More than 2 windows have passed, reset completely
            self.prev_window_outflow = 0;
            self.cur_window_outflow = 0;
            self.window_start = current_timestamp;
        } else if elapsed >= self.window_duration {
            // One window has passed, shift current to previous
            self.prev_window_outflow = self.cur_window_outflow;
            self.cur_window_outflow = 0;
            // Advance window_start by one duration (not to current_timestamp)
            // This keeps the window boundaries aligned
            self.window_start = self
                .window_start
                .saturating_add(self.window_duration as i64);
        }
        // Otherwise, still within current window, no changes needed
    }
```

**File:** programs/marginfi/src/state/rate_limiter.rs (L128-158)
```rust
    fn try_record_outflow(&mut self, amount: u64, current_timestamp: i64) -> MarginfiResult<()> {
        self.maybe_advance_window(current_timestamp);

        if !self.is_enabled() {
            return Ok(());
        }

        let amount = amount_as_i64(amount).ok_or(MarginfiError::InternalLogicError)?;
        let remaining = self.remaining_capacity(current_timestamp);
        if amount > remaining {
            return Err(MarginfiError::InternalLogicError.into());
        }

        self.cur_window_outflow = self.cur_window_outflow.saturating_add(amount);

        Ok(())
    }

    fn record_inflow(&mut self, amount: u64, current_timestamp: i64) {
        self.maybe_advance_window(current_timestamp);

        if !self.is_enabled() {
            return;
        }

        // Inflow reduces net outflow. Unlike an oversized outflow (rejected),
        // an oversized inflow is clamped to the max representable credit so a
        // legitimate large deposit is not trapped behind the outflow cap.
        let inflow = amount_as_i64(amount).unwrap_or(i64::MAX);
        self.cur_window_outflow = self.cur_window_outflow.saturating_sub(inflow);
    }
```

**File:** programs/marginfi/src/state/rate_limiter.rs (L199-246)
```rust
fn remaining_capacity_from_state(
    max_outflow: u64,
    window_duration: u64,
    window_start: i64,
    prev_window_outflow: i64,
    cur_window_outflow: i64,
    current_timestamp: i64,
) -> i64 {
    let Some(max_outflow) = amount_as_i64(max_outflow) else {
        return 0;
    };

    if window_duration == 0 {
        return max_outflow;
    }

    // Calculate elapsed time in current window
    let elapsed = current_timestamp.saturating_sub(window_start);
    if elapsed < 0 {
        return 0;
    }
    let elapsed = elapsed as u64;

    if elapsed >= window_duration {
        // We're past the window, only cur_window matters (it would become prev)
        // and it would be reset, so full capacity available
        return max_outflow;
    }

    // Weight the previous window by remaining time fraction
    // remaining_time = window_duration - elapsed
    // weight = remaining_time / window_duration
    let remaining_time = window_duration.saturating_sub(elapsed);

    // Use signed i128 arithmetic so the full i64 state space, including
    // i64::MIN, remains representable during weighting.
    let weighted_prev = (prev_window_outflow as i128)
        .saturating_mul(remaining_time as i128)
        .checked_div(window_duration as i128)
        .unwrap_or(0);

    // Total net outflow = weighted_prev + cur_window_outflow
    let total_net_outflow = weighted_prev.saturating_add(cur_window_outflow as i128);

    // Remaining capacity = max_outflow - total_net_outflow
    // If total_net_outflow is negative (more inflows), we have extra capacity
    clamp_i128_to_i64((max_outflow as i128).saturating_sub(total_net_outflow))
}
```

**File:** programs/marginfi/src/state/rate_limiter.rs (L248-336)
```rust
macro_rules! impl_dual_window_rate_limiter {
    (
        $impl_trait:ident for $type:ty,
        hourly_error: $hourly_err:ident,
        daily_error: $daily_err:ident,
        log_prefix: $prefix:literal
    ) => {
        impl $impl_trait for $type {
            fn is_enabled(&self) -> bool {
                self.hourly.is_enabled() || self.daily.is_enabled()
            }

            fn configure_hourly(&mut self, max_outflow: u64, current_timestamp: i64) {
                self.hourly
                    .initialize(max_outflow, HOURLY_RESET_DURATION, current_timestamp);
            }

            fn configure_daily(&mut self, max_outflow: u64, current_timestamp: i64) {
                self.daily
                    .initialize(max_outflow, DAILY_RESET_INTERVAL as u64, current_timestamp);
            }

            fn try_record_outflow(
                &mut self,
                amount: u64,
                current_timestamp: i64,
            ) -> MarginfiResult<()> {
                // Advance windows before computing remaining capacity to avoid boundary gaps.
                self.hourly.maybe_advance_window(current_timestamp);
                self.daily.maybe_advance_window(current_timestamp);

                // An amount that does not fit in i64 cannot be represented and
                // is treated as exceeding every window.
                let amount_i64 = amount_as_i64(amount);
                let exceeds = |remaining: i64| match amount_i64 {
                    Some(a) => a > remaining,
                    None => true,
                };

                if self.hourly.is_enabled() {
                    let remaining = self.hourly.remaining_capacity(current_timestamp);
                    if exceeds(remaining) {
                        msg!(
                            concat!(
                                $prefix,
                                " hourly rate limit exceeded: amount={}, remaining={}"
                            ),
                            amount,
                            remaining
                        );
                        return err!(MarginfiError::$hourly_err);
                    }
                }

                if self.daily.is_enabled() {
                    let remaining = self.daily.remaining_capacity(current_timestamp);
                    if exceeds(remaining) {
                        msg!(
                            concat!(
                                $prefix,
                                " daily rate limit exceeded: amount={}, remaining={}"
                            ),
                            amount,
                            remaining
                        );
                        return err!(MarginfiError::$daily_err);
                    }
                }

                // Both checks passed, record the outflow.
                if self.hourly.is_enabled() {
                    self.hourly.try_record_outflow(amount, current_timestamp)?;
                }
                if self.daily.is_enabled() {
                    self.daily.try_record_outflow(amount, current_timestamp)?;
                }

                Ok(())
            }

            fn record_inflow(&mut self, amount: u64, current_timestamp: i64) {
                if self.hourly.is_enabled() {
                    self.hourly.record_inflow(amount, current_timestamp);
                }
                if self.daily.is_enabled() {
                    self.daily.record_inflow(amount, current_timestamp);
                }
            }
        }
```

**File:** programs/marginfi/src/utils/general.rs (L464-481)
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
```

**File:** programs/marginfi/src/utils/general.rs (L493-511)
```rust
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

**File:** guides/ADMIN/RATE_LIMITS_AND_DELEVERAGE_WITHDRAW_LIMITS.md (L28-37)
```markdown
- Bank rate limits: `configure_bank_rate_limits`
  - Tracks native token net outflow on the bank account.
- Group rate limits: `configure_group_rate_limits`
  - Tracks USD net outflow across the whole group.

Each has hourly and daily windows.

- `0` disables that window.
- Deposits/repays release capacity.
- Withdraws/borrows consume capacity.
```
