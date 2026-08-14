### Title
Group-Level Rate Limiter Allows Cumulative Outflow To Exceed Configured Cap Before Delegate Admin Settlement - ([File: programs/marginfi/src/utils/general.rs])

### Summary
The Bond report describes a circuit breaker that is checked but not enforced atomically: a taker can push `market.totalDebt` past `term.maxDebt` within a single transaction because the market only closes *after* the trade completes, letting the fastest taker extract value before the breaker takes effect. marginfi's group-level rate limiter has an analogous "check now, enforce later" gap: the group-wide USD outflow cap is validated read-only against a *stale* counter that is only mutated by a delayed, off-chain-aggregated admin transaction, so several borrow/withdraw calls can each pass the check independently and collectively blow through the configured cap before enforcement catches up.

### Finding Description
Borrow and withdraw instructions (regular, Kamino, Drift, Juplend, Solend) call `record_withdrawal_outflow`, which enforces the group-level cap by reading `group.rate_limiter.hourly/daily.effective_remaining_capacity()` off a `MarginfiGroup` account that is loaded **read-only** (`group.load()?`, not `load_mut()`), and only *emits* a `RateLimitFlowEvent` — it never mutates `group.rate_limiter.cur_window_outflow` in the user's own instruction: [1](#0-0) 

The actual counter mutation (`try_record_outflow`) on the group's rate limiter only happens in `update_group_rate_limiter`, an instruction restricted to `delegate_flow_admin`, which aggregates `RateLimitFlowEvent`s off-chain and posts a batched update, with a tolerated staleness window of up to `MAX_RATE_LIMIT_UPDATE_LAG_SLOTS` (~1,500 slots, ~10 minutes): [2](#0-1) 

The design is explicitly documented: "Group-level rate limiting is checked read-only during user actions, then settled later from aggregated events... This avoids serializing all activity in a group through one writable group account." [3](#0-2) 

Because every borrow/withdraw only reads the last-posted counter and never decrements it in-flight, any number of unprivileged accounts (or a single attacker splitting a large withdrawal across multiple transactions/multiple banks) can each independently observe the same "remaining capacity" and each pass the check, exactly like Bob repeatedly buying bond tokens against a `maxDebt` limit that is only enforced after the fact. The cumulative real outflow can therefore exceed the group's configured hourly/daily USD cap by a multiple of the intended limit, bounded only by the number of transactions processed before the delegate admin's next `update_group_rate_limiter` call (which itself can lag up to ~10 minutes, or longer if the admin simply hasn't run yet).

### Impact Explanation
The group rate limiter exists specifically as a circuit-breaker-style defense against rapid, large-scale outflow across an entire group (e.g. during a depeg, oracle manipulation window, or panic run on a bank) — the same threat model as the Bond `maxDebt` circuit breaker. Because the enforcement counter is stale for up to the full aggregation window, the protection can be bypassed by concurrent/rapid transactions, allowing far more value to leave the group's banks than the admin intended to permit before further outflows are blocked. This is a financially meaningful degradation of a stated safety control, though it is a soft rate-limit bypass (bounded by aggregation lag) rather than an unbounded drain, and per-bank rate limiting (which is updated inline, synchronously) still applies as a secondary check.

### Likelihood Explanation
Exploitation only requires sending ordinary, permissionless `lending_account_borrow`/`lending_account_withdraw` (or integration equivalents) instructions — no privileged role is needed. The condition for exploitation (multiple outflow transactions landing within the same unsettled aggregation window) is a normal, expected occurrence rather than an edge case, since the system is explicitly designed to only settle the group counter periodically. Any user or set of users transacting faster than the delegate admin's aggregation cadence will trivially exceed the intended cap.

### Recommendation
Consider bounding the exploitable window more tightly (e.g., much shorter `MAX_RATE_LIMIT_UPDATE_LAG_SLOTS`, or a per-block/per-slot cap in addition to the batched cap), or introduce an interim in-memory/PDA-based provisional reservation that is updated synchronously per-transaction (even if lightweight) so concurrent transactions cannot all read the same stale "remaining capacity." Alternatively, treat the group-level limiter as a secondary/soft signal only, and rely on the synchronously-updated bank-level rate limiter (and other bank caps such as `borrow_limit`/`deposit_limit`, which correctly revert on breach as shown in `change_liability_shares`) as the primary hard circuit breaker for any given bank: [4](#0-3) 

### Proof of Concept
1. Admin sets `groupHourly` outflow cap to, e.g., $10 (as in the existing test `tests/specs/basic/17_rateLimiter.spec.ts`).
2. Attacker (or several colluding/independent users) sends multiple borrow/withdraw transactions in rapid succession, each for an amount just under the currently-known "remaining capacity" (all reading the same stale `group.rate_limiter` state because no transaction mutates it).
3. Each transaction independently passes `record_withdrawal_outflow`'s `effective_remaining_capacity` check and succeeds, since the group account is read-only in this path: [5](#0-4) 
4. Only after the `delegate_flow_admin` later calls `update_group_rate_limiter` (which can lag up to ~10 minutes per `MAX_RATE_LIMIT_UPDATE_LAG_SLOTS`) does the on-chain counter reflect the true aggregate outflow, by which point the cumulative outflow may already be several multiples of the configured $10 cap — mirroring the Bond scenario where the fastest taker extracts payout tokens before the `maxDebt` circuit breaker closes the market.

### Citations

**File:** programs/marginfi/src/utils/general.rs (L483-522)
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

**File:** programs/marginfi/src/instructions/marginfi_group/update_group_rate_limiter.rs (L9-46)
```rust
const MAX_RATE_LIMIT_UPDATE_LAG_SLOTS: u64 = 1_500; // ~10 minutes at ~400ms/slot

/// (delegate_flow_admin only) Update the group rate limiter inflow/outflow state.
///
/// The delegate flow admin aggregates `RateLimitFlowEvent` events off-chain,
/// computes the USD-denominated inflows and outflows, and calls this instruction
/// at intervals to update the group rate limiter state.
///
/// This avoids requiring the group account to be writable (mut) in every user-facing
/// instruction, which would serialize all transactions for a group into a single slot.
pub fn update_group_rate_limiter(
    ctx: Context<UpdateGroupRateLimiter>,
    outflow_usd: Option<u64>,
    inflow_usd: Option<u64>,
    update_seq: u64,
    event_start_slot: u64,
    event_end_slot: u64,
) -> MarginfiResult {
    let mut group = ctx.accounts.marginfi_group.load_mut()?;
    let clock = Clock::get()?;

    check!(
        outflow_usd.is_some() || inflow_usd.is_some(),
        MarginfiError::GroupRateLimiterUpdateEmpty
    );
    validate_event_slots(
        event_start_slot,
        event_end_slot,
        group.rate_limiter_last_admin_update_slot,
    )?;
    check!(
        event_end_slot <= clock.slot,
        MarginfiError::GroupRateLimiterUpdateFutureSlot
    );
    check!(
        clock.slot.saturating_sub(event_end_slot) <= MAX_RATE_LIMIT_UPDATE_LAG_SLOTS,
        MarginfiError::GroupRateLimiterUpdateStale
    );
```

**File:** guides/ADMIN/RATE_LIMITS_AND_DELEVERAGE_WITHDRAW_LIMITS.md (L14-47)
```markdown
- Bank-level rate limiting is updated inline because the bank account is already writable.
- Group-level rate limiting is checked read-only during user actions, then settled later from
  aggregated events.
- Deleverage withdraw limits are also checked read-only during the withdraw, then settled later from
  aggregated events.

This avoids serializing all activity in a group through one writable group account.

## Group Rate Limit Flow

### 1. Configuration

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
- A `RateLimitFlowEvent` is emitted for off-chain aggregation.
```

**File:** programs/marginfi/src/state/bank.rs (L373-383)
```rust
        if !bypass_borrow_limit && shares.is_positive() && self.config.is_borrow_limit_active() {
            let total_liability_amount =
                self.get_liability_amount(self.total_liability_shares.into())?;
            let borrow_limit = I80F48::from_num(self.config.borrow_limit);

            if total_liability_amount >= borrow_limit {
                let liab_num: f64 = total_liability_amount.to_num();
                let borrow_num: f64 = borrow_limit.to_num();
                msg!("amt: {:?} borrow lim: {:?}", liab_num, borrow_num);
                return err!(MarginfiError::BankLiabilityCapacityExceeded);
            }
```
