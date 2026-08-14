### Title
Reconfiguring bank/group rate limits resets the outflow window to zero, letting users double-spend across the rate-limit boundary - (File: `programs/marginfi/src/state/rate_limiter.rs`)

### Summary
`RateLimitWindowImpl::initialize`, invoked by `configure_hourly` / `configure_daily` whenever an admin (or `delegate_limit_admin`) calls `configure_bank_rate_limits` or `configure_group_rate_limits`, unconditionally zeroes `prev_window_outflow` and `cur_window_outflow` and restarts `window_start` at the current timestamp. This mirrors the Volt `MultiRateLimited._updateAddress` bug: updating the rate-limit configuration fully replenishes the "buffer" (used capacity) instead of preserving already-consumed capacity capped by the new limit.

### Finding Description
`initialize` resets tracked usage to zero on every call: [1](#0-0) 

`configure_hourly`/`configure_daily` call `initialize` directly, with no attempt to carry over the existing `cur_window_outflow`/`prev_window_outflow` state: [2](#0-1) 

These are reachable via the admin-facing instructions `configure_bank_rate_limits` and `configure_group_rate_limits`, which either the group `admin` or the `delegate_limit_admin` can call at any time to adjust hourly/daily caps: [3](#0-2) [4](#0-3) 

Because the reset only touches the window(s) that receive a `Some(..)` value (partial updates preserve the untouched window, as the test suite documents), any legitimate reconfiguration of an active window — even one that only tweaks the cap slightly — instantly restores full outflow capacity for that window: [5](#0-4) 

An attacker who observes a pending `configure_bank_rate_limits`/`configure_group_rate_limits` transaction (e.g. in the mempool, or by monitoring routine admin/ops changes) can:
1. Exhaust the current window's outflow capacity (e.g. withdraw/borrow up to the existing hourly/daily cap) just before the admin's update lands.
2. As soon as the admin's transaction executes, `initialize` resets `cur_window_outflow`/`prev_window_outflow` to `0`, instantly restoring full capacity under the new cap regardless of how recently the old cap was fully consumed.
3. Immediately withdraw/borrow up to the new cap again — bypassing the very throttling window the admin's config was intended to enforce, and getting two consumption cycles inside what should be one rate-limit window.

This directly matches the reported bug class: rate-limit reconfiguration silently replenishes the used-buffer/window state to full, rather than preserving prior consumption capped by the new limit.

### Impact Explanation
This breaks the intended throttling guarantee of both the bank-level native-token rate limiter and the group-level USD rate limiter, both of which exist specifically to bound outflow/borrow exposure per hour/day as a risk control (per `guides/ADMIN/RATE_LIMITS_AND_DELEVERAGE_WITHDRAW_LIMITS.md`). An attacker can effectively double their allowed outflow within a short window any time an admin performs an otherwise-routine limit adjustment (raising, lowering, or even reaffirming a cap), undermining a defense-in-depth control meant to cap outflow risk during an incident. The financial exposure is bounded by whatever the rate limit itself is meant to cap (native token/USD outflow), so it is a real, if situational, financial-control bypass rather than a full protocol drain.

### Likelihood Explanation
Requires the rate limiter to be enabled and an admin/`delegate_limit_admin` to touch the corresponding window's config while it has accumulated near-max usage, and for an attacker to time their outflow around that update. This is plausible: rate limits are expected to be adjusted periodically (per the guide), and Solana transactions are visible pre-confirmation, giving an attacker a window to race the update. It is not a purely theoretical or admin-only-triggered issue — the attacker is an unprivileged user reacting to an admin action, and no attacker-side privilege is required.

### Recommendation
When reconfiguring an already-enabled window, preserve consumed capacity relative to the new cap instead of zeroing it, analogous to the Volt fix (`bufferStored = min(newCap, oldBuffer)`). Concretely, in `initialize`/`configure_hourly`/`configure_daily`, compute the window's *effective remaining capacity* at the current timestamp before resetting, then re-derive `cur_window_outflow` (and `prev_window_outflow`) so that already-consumed capacity carries over, clamped to the new `max_outflow`, rather than unconditionally setting both to `0`.

### Proof of Concept
1. Admin enables bank hourly rate limit at 100 tokens: `configure_bank_rate_limits(hourly_max_outflow = Some(100), ..)`.
2. User borrows/withdraws 100 tokens, fully consuming `cur_window_outflow = 100` (verified by the existing test pattern in `tests/specs/basic/17_rateLimiter.spec.ts` lines 497-519, where a further 1-token borrow fails with "Bank hourly rate limit exceeded").
3. Admin submits `configure_bank_rate_limits(hourly_max_outflow = Some(100), ..)` again (or any change to the hourly cap) mid-window — this calls `configure_hourly` → `initialize`, resetting `cur_window_outflow` to `0` per `programs/marginfi/src/state/rate_limiter.rs` lines 58-64.
4. User immediately borrows/withdraws another 100 tokens successfully, having consumed 200 tokens of outflow within a single nominal hourly window instead of the intended 100.

### Citations

**File:** programs/marginfi/src/state/rate_limiter.rs (L58-64)
```rust
    fn initialize(&mut self, max_outflow: u64, window_duration: u64, current_timestamp: i64) {
        self.max_outflow = max_outflow;
        self.window_duration = window_duration;
        self.window_start = current_timestamp;
        self.prev_window_outflow = 0;
        self.cur_window_outflow = 0;
    }
```

**File:** programs/marginfi/src/state/rate_limiter.rs (L260-268)
```rust
            fn configure_hourly(&mut self, max_outflow: u64, current_timestamp: i64) {
                self.hourly
                    .initialize(max_outflow, HOURLY_RESET_DURATION, current_timestamp);
            }

            fn configure_daily(&mut self, max_outflow: u64, current_timestamp: i64) {
                self.daily
                    .initialize(max_outflow, DAILY_RESET_INTERVAL as u64, current_timestamp);
            }
```

**File:** programs/marginfi/src/instructions/marginfi_group/configure_rate_limits.rs (L19-51)
```rust
pub fn configure_bank_rate_limits(
    ctx: Context<ConfigureBankRateLimits>,
    hourly_max_outflow: Option<u64>,
    daily_max_outflow: Option<u64>,
) -> MarginfiResult {
    let mut bank = ctx.accounts.bank.load_mut()?;
    let clock = Clock::get()?;

    if let Some(hourly) = hourly_max_outflow {
        check!(
            is_valid_rate_limit_amount(hourly),
            MarginfiError::InvalidConfig
        );
        bank.rate_limiter
            .configure_hourly(hourly, clock.unix_timestamp);
        msg!(
            "Bank hourly rate limit configured: {} native tokens",
            hourly
        );
    }

    if let Some(daily) = daily_max_outflow {
        check!(
            is_valid_rate_limit_amount(daily),
            MarginfiError::InvalidConfig
        );
        bank.rate_limiter
            .configure_daily(daily, clock.unix_timestamp);
        msg!("Bank daily rate limit configured: {} native tokens", daily);
    }

    Ok(())
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/configure_rate_limits.rs (L80-111)
```rust
pub fn configure_group_rate_limits(
    ctx: Context<ConfigureGroupRateLimits>,
    hourly_max_outflow_usd: Option<u64>,
    daily_max_outflow_usd: Option<u64>,
) -> MarginfiResult {
    let mut group = ctx.accounts.marginfi_group.load_mut()?;
    let clock = Clock::get()?;

    if let Some(hourly) = hourly_max_outflow_usd {
        check!(
            is_valid_rate_limit_amount(hourly),
            MarginfiError::InvalidConfig
        );
        group
            .rate_limiter
            .configure_hourly(hourly, clock.unix_timestamp);
        msg!("Group hourly rate limit configured: {} USD", hourly);
    }

    if let Some(daily) = daily_max_outflow_usd {
        check!(
            is_valid_rate_limit_amount(daily),
            MarginfiError::InvalidConfig
        );
        group
            .rate_limiter
            .configure_daily(daily, clock.unix_timestamp);
        msg!("Group daily rate limit configured: {} USD", daily);
    }

    Ok(())
}
```

**File:** tests/specs/basic/17_rateLimiter.spec.ts (L449-466)
```typescript
    // Partial update: only change bankHourly and groupDaily, preserve others with null
    await setRateLimits({
      bankHourly: usdcNative(75),
      bankDaily: null,
      groupHourly: null,
      groupDaily: new BN(300),
    });

    const [bankAfter, groupAfter] = await Promise.all([
      program.account.bank.fetch(bankKeypairUsdc.publicKey),
      program.account.marginfiGroup.fetch(marginfiGroup.publicKey),
    ]);

    assertBNEqual(bankAfter.rateLimiter.hourly.maxOutflow, usdcNative(75)); // updated
    assertBNEqual(bankAfter.rateLimiter.daily.maxOutflow, usdcNative(100)); // preserved
    assertBNEqual(groupAfter.rateLimiter.hourly.maxOutflow, 50); // preserved
    assertBNEqual(groupAfter.rateLimiter.daily.maxOutflow, 300); // updated
  });
```
