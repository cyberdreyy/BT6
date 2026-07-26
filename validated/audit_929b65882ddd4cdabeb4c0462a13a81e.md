### Title
`StakingPool.withdraw_rewards` silently caps rewards but burns the full pool-token amount, permanently shortchanging the withdrawer — (`File: crates/sui-framework/packages/sui-system/sources/staking_pool.move`)

---

### Summary

`withdraw_rewards` applies a defensive `.min(pool.rewards_pool.value())` cap that silently reduces the SUI paid to a withdrawer when the rewards pool is short. However, `request_withdraw_stake` still records the **full, uncapped** `pool_token_withdraw_amount` in `pending_pool_token_withdraw`. At the next epoch boundary, `pool_token_balance` is reduced by the full token amount while `sui_balance` is only reduced by the smaller actual payout. The "missing" rewards remain in `rewards_pool` and inflate the exchange rate for all remaining stakers, permanently transferring value away from the withdrawer.

---

### Finding Description

**`withdraw_rewards` (lines 424–441):**

```move
fun withdraw_rewards(
    pool: &mut StakingPool,
    principal_withdraw_amount: u64,
    pool_token_withdraw_amount: u64,
    epoch: u64,
): Balance<SUI> {
    let exchange_rate = pool.pool_token_exchange_rate_at_epoch(epoch);
    let total_sui_withdraw_amount = exchange_rate.get_sui_amount(pool_token_withdraw_amount);
    let mut reward_withdraw_amount = if (total_sui_withdraw_amount >= principal_withdraw_amount) {
        total_sui_withdraw_amount - principal_withdraw_amount
    } else 0;

    // This may happen when we are withdrawing everything from the pool and
    // the rewards pool balance may be less than reward_withdraw_amount.
    // TODO: FIGURE OUT EXACTLY WHY THIS CAN HAPPEN.
    reward_withdraw_amount = reward_withdraw_amount.min(pool.rewards_pool.value());
    pool.rewards_pool.split(reward_withdraw_amount)
}
```

The `TODO` comment confirms the developers know the rewards pool can be short but have not resolved it. The `min()` prevents an abort but silently reduces the payout.

**`request_withdraw_stake` (lines 157–193):**

```move
let rewards_withdraw = pool.withdraw_rewards(
    principal_withdraw_amount,
    pool_token_withdraw_amount,
    ctx.epoch(),
);
let total_sui_withdraw_amount = principal_withdraw_amount + rewards_withdraw.value();

pool.pending_total_sui_withdraw = pool.pending_total_sui_withdraw + total_sui_withdraw_amount;
pool.pending_pool_token_withdraw =
    pool.pending_pool_token_withdraw + pool_token_withdraw_amount; // ← full amount, never adjusted
```

`pool_token_withdraw_amount` is **never recomputed** to reflect the reduced payout. The full token count is burned regardless of how much SUI was actually delivered.

**Epoch-boundary settlement (`process_pending_stake_withdraw`, lines 374–395):**

```move
pool.sui_balance = pool.sui_balance - pool.pending_total_sui_withdraw; // actual (reduced) payout
pool.pool_token_balance = pool.pool_token_balance - pool.pending_pool_token_withdraw; // full tokens
```

After settlement:
- `sui_balance` is reduced by the capped (smaller) amount.
- `pool_token_balance` is reduced by the full token amount.
- The unclaimed rewards remain in `rewards_pool` and are still counted in `sui_balance` for future exchange-rate calculations.
- The new exchange rate (`sui_balance / pool_token_balance`) is **inflated**, benefiting remaining stakers at the withdrawer's expense.

---

### Impact Explanation

The withdrawer permanently loses the difference between their fair-share rewards and the capped payout. Those SUI remain in `rewards_pool` and are redistributed to all remaining stakers through a higher exchange rate. This is a direct, irreversible transfer of value from the withdrawer to other stakers — matching the "harmful smart-contract behavior" impact class. The loss is not bounded to 1 MIST; it equals the full rewards-pool shortfall at the time of withdrawal, which can be arbitrarily large (e.g., after multiple safe-mode epochs with no reward distribution).

---

### Likelihood Explanation

The rewards pool can fall short of the computed `reward_withdraw_amount` in at least two documented scenarios:

1. **Safe-mode epochs** — rewards are not deposited, so `rewards_pool` does not grow while `sui_balance` and the exchange rate diverge. Tests in `rewards_distribution_tests.move` (e.g., `process_pending_stake_withdraw_no_underflow_in_safe_mode_1`) explicitly exercise this path and show `pool_token_balance < pending_pool_token_withdraw`.
2. **Concurrent withdrawals within the same epoch** — multiple stakers withdraw in the same epoch; the first withdrawals drain `rewards_pool`, leaving later withdrawals short.

Any ordinary SUI holder with a `StakedSui` object can trigger this path by calling `sui_system::request_withdraw_stake` — no privileged access is required.

---

### Recommendation

Mirror the fix suggested in the external report: when `withdraw_rewards` returns fewer SUI than the exchange rate implies, recompute `pool_token_withdraw_amount` proportionally before recording it in `pending_pool_token_withdraw`.

```move
// In request_withdraw_stake, after calling withdraw_rewards:
let actual_sui_out = principal_withdraw_amount + rewards_withdraw.value();
let fair_sui_out   = exchange_rate.get_sui_amount(pool_token_withdraw_amount);

let adjusted_pool_token_withdraw = if (actual_sui_out < fair_sui_out && fair_sui_out > 0) {
    // scale pool tokens down to match actual payout
    mul_div!(pool_token_withdraw_amount, actual_sui_out, fair_sui_out)
} else {
    pool_token_withdraw_amount
};

pool.pending_total_sui_withdraw  += actual_sui_out;
pool.pending_pool_token_withdraw += adjusted_pool_token_withdraw;
```

This ensures the exchange rate at the next epoch boundary is not inflated at the withdrawer's expense.

---

### Proof of Concept

**Setup:**
- Pool has `sui_balance = 200 SUI`, `pool_token_balance = 100 tokens`, `rewards_pool = 5 SUI` (pool is short — e.g., after safe-mode epochs).
- Exchange rate: 2 SUI per token.
- Alice staked 100 SUI at epoch 0 (1:1 rate), receiving 100 pool tokens recorded in her `StakedSui`.

**Alice calls `request_withdraw_stake`:**

1. `withdraw_from_principal` returns `pool_token_withdraw_amount = 100`, `principal_withdraw = 100 SUI`.
2. `withdraw_rewards` computes `reward_withdraw_amount = get_sui_amount(100) - 100 = 200 - 100 = 100 SUI`.
3. `rewards_pool.value() = 5 SUI` → capped: `reward_withdraw_amount = 5 SUI`.
4. Alice receives `100 + 5 = 105 SUI` (fair share was `200 SUI`).
5. `pending_total_sui_withdraw += 105`, `pending_pool_token_withdraw += 100` (full, uncapped).

**Epoch boundary:**

- `sui_balance = 200 - 105 = 95 SUI` (but 95 SUI of rewards remain in `rewards_pool`).
- `pool_token_balance = 100 - 100 = 0`.
- New exchange rate: `95 / 0` → effectively all remaining SUI is redistributed to any remaining stakers.

Alice lost `95 SUI` of rewards she was entitled to. Those SUI remain in the pool and benefit other stakers. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/sui-framework/packages/sui-system/sources/staking_pool.move (L176-185)
```text
    let rewards_withdraw = pool.withdraw_rewards(
        principal_withdraw_amount,
        pool_token_withdraw_amount,
        ctx.epoch(),
    );
    let total_sui_withdraw_amount = principal_withdraw_amount + rewards_withdraw.value();

    pool.pending_total_sui_withdraw = pool.pending_total_sui_withdraw + total_sui_withdraw_amount;
    pool.pending_pool_token_withdraw =
        pool.pending_pool_token_withdraw + pool_token_withdraw_amount;
```

**File:** crates/sui-framework/packages/sui-system/sources/staking_pool.move (L374-395)
```text
fun process_pending_stake_withdraw(pool: &mut StakingPool) {
    pool.sui_balance = if (pool.sui_balance >= pool.pending_total_sui_withdraw) {
        pool.sui_balance - pool.pending_total_sui_withdraw
    } else {
        let diff = pool.pending_total_sui_withdraw - pool.sui_balance;
        // While this key is expected to be removed in the next call to `process_pending_stake`,
        // we do not call `process_pending_stake` for inactive pools — skip the bookkeeping.
        if (!pool.is_inactive()) {
            pool.extra_fields.add(UnderflowSuiBalance {}, diff);
        };
        0
    };

    pool.pool_token_balance = if (pool.pool_token_balance >= pool.pending_pool_token_withdraw) {
        pool.pool_token_balance - pool.pending_pool_token_withdraw
    } else {
        0
    };

    pool.pending_total_sui_withdraw = 0;
    pool.pending_pool_token_withdraw = 0;
}
```

**File:** crates/sui-framework/packages/sui-system/sources/staking_pool.move (L424-441)
```text
fun withdraw_rewards(
    pool: &mut StakingPool,
    principal_withdraw_amount: u64,
    pool_token_withdraw_amount: u64,
    epoch: u64,
): Balance<SUI> {
    let exchange_rate = pool.pool_token_exchange_rate_at_epoch(epoch);
    let total_sui_withdraw_amount = exchange_rate.get_sui_amount(pool_token_withdraw_amount);
    let mut reward_withdraw_amount = if (total_sui_withdraw_amount >= principal_withdraw_amount) {
        total_sui_withdraw_amount - principal_withdraw_amount
    } else 0;

    // This may happen when we are withdrawing everything from the pool and
    // the rewards pool balance may be less than reward_withdraw_amount.
    // TODO: FIGURE OUT EXACTLY WHY THIS CAN HAPPEN.
    reward_withdraw_amount = reward_withdraw_amount.min(pool.rewards_pool.value());
    pool.rewards_pool.split(reward_withdraw_amount)
}
```
