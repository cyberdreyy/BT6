### Title
Wrong assumption of Solana slot time in `MAX_RATE_LIMIT_UPDATE_LAG_SLOTS` / `MAX_DELEVERAGE_WITHDRAW_LIMIT_UPDATE_LAG_SLOTS` staleness windows - ([File: programs/marginfi/src/instructions/marginfi_group/update_group_rate_limiter.rs])

### Summary
Both `update_group_rate_limiter` and `update_deleverage_withdrawals` gate the `delegate_flow_admin`'s permissioned settlement instruction with a hardcoded slot-count staleness bound that is derived from an assumed constant Solana slot time of 400ms, mirroring the reported "wrong assumption of block time" bug class (hardcoded voting windows sized off an assumed block time that doesn't match the chain's actual/variable block time).

### Finding Description
`update_group_rate_limiter.rs` defines: [1](#0-0) 
and enforces it as: [2](#0-1) 

The same pattern is used in the deleverage withdraw path: [3](#0-2) [4](#0-3) 

Both constants assume "~400ms/slot" to derive "~10 minutes" (1,500 slots). Unlike the interest-rate accrual logic and rate-limiter windows elsewhere in the codebase, which correctly use `clock.unix_timestamp` (wall-clock seconds) for all duration math (`SECONDS_PER_YEAR`, `HOURLY_RESET_DURATION`, `DAILY_RESET_INTERVAL`, `maybe_advance_window`), these two staleness checks instead measure elapsed slots and treat that slot count as a proxy for elapsed wall-clock time: [5](#0-4) [6](#0-5) 

Solana's actual slot production rate is not a fixed 400ms — it varies with network congestion and historically has run slower (Solana mainnet average slot time has at various points been closer to 450-600ms, with periods of significant slowdown during outages/congestion). If the real average slot time diverges from the 400ms assumption baked into `MAX_RATE_LIMIT_UPDATE_LAG_SLOTS`/`MAX_DELEVERAGE_WITHDRAW_LIMIT_UPDATE_LAG_SLOTS`, the actual wall-clock duration represented by 1,500 slots will differ from the intended "~10 minutes," analogous to the reported governance bug where 15s-block-time-derived limits were wrong for a 5s-block-time chain.

### Impact Explanation
If real slot time is longer than 400ms (which happens during congestion), 1,500 slots corresponds to a shorter-than-intended wall-clock staleness window. This causes the permissioned `delegate_flow_admin` settlement instructions (`update_group_rate_limiter`, `update_deleverage_withdrawals`) to reject valid, only-slightly-delayed off-chain aggregated batches as stale (`GroupRateLimiterUpdateStale` / `DeleverageWithdrawalUpdateStale`) more often than intended, potentially stalling the settlement of aggregated `RateLimitFlowEvent`/`DeleverageWithdrawFlowEvent` data into the group's rate-limiter/withdraw-flow state. Conversely, if slot time is ever faster than assumed, the staleness window becomes wider than intended, permitting older data to be admitted as if fresh. This is a liveness/consistency issue in a delegated, restricted-authority maintenance path rather than a direct fund-loss bug, since the caller must still be the authorized `delegate_flow_admin` (`has_one = delegate_flow_admin @ MarginfiError::Unauthorized`), and event ordering/no-overlap is separately enforced by `validate_event_slots`.

### Likelihood Explanation
Low-to-medium. The assumption only matters when actual slot production deviates meaningfully and durably from 400ms/slot, which does happen on Solana during congestion or validator issues, but the effect is a moderate skew in a staleness window (not a hard security boundary like Berachain's governance voting periods) and requires the delegate_flow_admin path to be actively used near that boundary to matter operationally.

### Recommendation
Replace slot-count-based staleness checks with `clock.unix_timestamp`-based checks, consistent with the rest of the codebase's time-duration handling (e.g., mirror the pattern in `state/rate_limiter.rs`), so the staleness bound is defined directly in seconds rather than through an assumed slot-time conversion factor.

### Proof of Concept
Not applicable as an on-chain exploit PoC — this is a parameter-derivation/liveness concern, not a state-corruption or fund-loss exploit. Conceptually: if average slot time increases (e.g. to 600ms during congestion), 1,500 slots elapse in ~15 minutes instead of ~10, so the intended "10 minute" leeway for `event_end_slot` staleness is effectively unchanged in slot terms but the actual permitted delay window changes in wall-clock terms compared to what the comment/design intends, which can be demonstrated purely by comparing `clock.slot` progression against `clock.unix_timestamp` progression in a test harness like `programs/marginfi/tests/admin_actions/rate_limiter.rs`.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/update_group_rate_limiter.rs (L9-9)
```rust
const MAX_RATE_LIMIT_UPDATE_LAG_SLOTS: u64 = 1_500; // ~10 minutes at ~400ms/slot
```

**File:** programs/marginfi/src/instructions/marginfi_group/update_group_rate_limiter.rs (L43-46)
```rust
    check!(
        clock.slot.saturating_sub(event_end_slot) <= MAX_RATE_LIMIT_UPDATE_LAG_SLOTS,
        MarginfiError::GroupRateLimiterUpdateStale
    );
```

**File:** programs/marginfi/src/instructions/marginfi_group/update_deleverage_withdrawals.rs (L6-6)
```rust
const MAX_DELEVERAGE_WITHDRAW_LIMIT_UPDATE_LAG_SLOTS: u64 = 1_500; // ~10 minutes at ~400ms/slot
```

**File:** programs/marginfi/src/instructions/marginfi_group/update_deleverage_withdrawals.rs (L38-41)
```rust
    check!(
        clock.slot.saturating_sub(event_end_slot) <= MAX_DELEVERAGE_WITHDRAW_LIMIT_UPDATE_LAG_SLOTS,
        MarginfiError::DeleverageWithdrawalUpdateStale
    );
```

**File:** type-crate/src/constants.rs (L33-35)
```rust
pub const SECONDS_PER_YEAR: I80F48 = I80F48!(31_536_000);
pub const DAILY_RESET_INTERVAL: i64 = 24 * 60 * 60; // 24 hours
pub const HOURLY_RESET_DURATION: u64 = 60 * 60; // 1 hour in seconds
```

**File:** programs/marginfi/src/state/rate_limiter.rs (L58-94)
```rust
    fn initialize(&mut self, max_outflow: u64, window_duration: u64, current_timestamp: i64) {
        self.max_outflow = max_outflow;
        self.window_duration = window_duration;
        self.window_start = current_timestamp;
        self.prev_window_outflow = 0;
        self.cur_window_outflow = 0;
    }

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
