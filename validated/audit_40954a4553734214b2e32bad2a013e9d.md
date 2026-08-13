## Title
Group-level rate limiter check-without-update allows repeated withdrawals to bypass the daily/hourly outflow cap - ([File: programs/marginfi/src/utils/general.rs])

### Summary
This is the closest analog to the Bluefin "Inconsistent Assert Statement" bug class in marginfi-v2. The Bluefin issue was that an assertion validated a requested amount against an available balance, but never subtracted the amount already "reserved" (`pending_profit_amount`) by prior calls, so repeating the same call kept passing the same stale check. In marginfi's `record_withdrawal_outflow`, the **group-level** rate limit check works the same way: it is read-only against the `MarginfiGroup.rate_limiter` state, which is only mutated later by an off-chain-aggregated admin instruction (`update_group_rate_limiter`). Nothing in the hot path decrements the checked "remaining capacity" for outflows already performed since the last admin settlement, so a user can repeat withdrawals (or split into many bank/instrument withdraw calls) and each one independently passes the same stale "remaining capacity" check.

### Finding Description
`record_withdrawal_outflow` in [1](#0-0)  performs two rate-limit steps:
1. The **bank-level** rate limiter is updated inline and atomically (`bank.rate_limiter.try_record_outflow(...)`), which is safe.
2. The **group-level** rate limiter is only *checked*, never updated, in the same call: [2](#0-1) 
```
if group.rate_limiter.hourly.is_enabled() {
    let remaining = group.rate_limiter.hourly.effective_remaining_capacity(clock.unix_timestamp);
    if value.to_num::<i64>() > remaining { return Err(...); }
}
if group.rate_limiter.daily.is_enabled() {
    let remaining = group.rate_limiter.daily.effective_remaining_capacity(clock.unix_timestamp);
    if value.to_num::<i64>() > remaining { return Err(...); }
}
```
The `remaining` value comes from `group.rate_limiter`, which is only mutated by the admin/delegate-flow-admin instruction `update_group_rate_limiter`, driven by off-chain aggregation of `RateLimitFlowEvent`s emitted here [3](#0-2) . The design is explicitly documented: "The group rate limiter is only checked read-only during user actions, then settled later from aggregated events" [4](#0-3) .

Because this check is stateless within the settlement interval, any number of withdrawals made across multiple banks/instructions (in the same transaction or in sequential transactions before the admin next calls `update_group_rate_limiter`) each individually re-evaluate against the *same* unmutated `remaining` value — exactly the "repeat the same call, assertion keeps passing" pattern described in the Bluefin report, where `pending_profit_amount` was never subtracted from the checked balance. Here, the analogous "pending" quantity is the cumulative USD outflow already performed by unsettled withdraws in the current window, which is never subtracted from `remaining` before the next check.

### Impact Explanation
This allows an attacker (or just uncoordinated normal users, but most severely an attacker with multiple bank positions) to exceed the intended daily/hourly group-level withdrawal cap by an unbounded multiple, defeating the purpose of the group rate limiter as a circuit-breaker against abused/compromised risk workflows or mass-drain events. Since the check is USD-based and applies across `lending_account_withdraw`, and the kamino/juplend/drift/solend withdraw variants, a single account (or several colluding accounts) can withdraw far more than the configured group cap in a single settlement window by issuing many separate withdraw instructions/transactions, each passing the same stale check. This does not directly break individual bank solvency (bank-level checks and health checks still apply), but it defeats a key protocol-level financial safety control whose entire purpose is to bound outflow velocity — a durable inconsistency between configured risk limits and actual enforced behavior with real financial effect (the group can be drained beyond its configured "cap" before the admin catches up).

### Likelihood Explanation
Likelihood is high for the specific safety property being bypassed: it requires no privileged access, no signer other than a normal marginfi account holder, and can be reliably triggered by simply issuing multiple withdrawals (across positions/banks) within one settlement window — a normal permissionless action. The only "cost" is needing multiple withdrawable positions or splitting one withdrawal into several bank instructions, both trivially achievable. However, this is a documented, presumably accepted architectural tradeoff (the guide describes it plainly as a "read-only checked, later settled" design with `RateLimitFlowEvent` explicitly called "an indexing aid, not a source of truth"), so it may be judged as accepted risk rather than an unintended vulnerability — this reduces confidence that it should be treated as a novel finding versus a known/accepted design limitation.

### Recommendation
Track an in-transaction (or in-flight/unsettled) cumulative outflow counter that is checked and decremented atomically within `record_withdrawal_outflow`, or make the group-level rate limiter state part of the same writable account touched during a withdrawal (at the cost of contention) so that the "remaining capacity" check reflects amounts already consumed by not-yet-settled withdrawals, analogous to subtracting `pending_profit_amount` in the Bluefin fix.

### Proof of Concept
1. Admin configures a group daily rate limit of $X via `configure_group_rate_limits`.
2. A user account holds several distinct asset positions (or splits their balance across several instrument integrations).
3. Within one settlement window (before `update_group_rate_limiter` is next called), the user issues N separate `lending_account_withdraw` (or kamino/juplend/drift/solend withdraw) instructions, each with a USD value just under $X.
4. Because `record_withdrawal_outflow`'s group-level check only reads the last-settled `group.rate_limiter` state and never mutates it, each of the N withdrawals independently passes the "amount <= remaining capacity" check, letting the user withdraw up to N × $X in total, far exceeding the configured $X daily cap, before the off-chain aggregator/admin settlement can catch up via `update_group_rate_limiter`.

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

**File:** guides/ADMIN/RATE_LIMITS_AND_DELEVERAGE_WITHDRAW_LIMITS.md (L14-20)
```markdown
- Bank-level rate limiting is updated inline because the bank account is already writable.
- Group-level rate limiting is checked read-only during user actions, then settled later from
  aggregated events.
- Deleverage withdraw limits are also checked read-only during the withdraw, then settled later from
  aggregated events.

This avoids serializing all activity in a group through one writable group account.
```
