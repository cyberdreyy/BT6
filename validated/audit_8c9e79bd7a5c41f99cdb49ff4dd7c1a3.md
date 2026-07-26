### Title
`redeem_fungible_staked_sui` Aborts When `rewards_pool` Is Depleted Within an Epoch Due to Missing `.min()` Guard - (File: `crates/sui-framework/packages/sui-system/sources/staking_pool.move`)

---

### Summary

`redeem_fungible_staked_sui` in `staking_pool.move` computes a `rewards_amount` from the epoch-start exchange rate and then unconditionally calls `pool.rewards_pool.split(rewards_amount)`. If regular stakers have already withdrawn rewards during the same epoch — reducing `rewards_pool` below the computed amount — the `split` call aborts and the FSS holder cannot redeem their tokens until the next epoch. The sibling function `withdraw_rewards` (used by `request_withdraw_stake`) already acknowledges and guards against exactly this condition with a `.min(pool.rewards_pool.value())` clamp, but `redeem_fungible_staked_sui` omits it.

---

### Finding Description

**Root cause — missing `.min()` guard in `redeem_fungible_staked_sui`**

`withdraw_rewards`, called by `request_withdraw_stake`, explicitly guards against `rewards_pool` being insufficient:

```move
// This may happen when we are withdrawing everything from the pool and
// the rewards pool balance may be less than reward_withdraw_amount.
// TODO: FIGURE OUT EXACTLY WHY THIS CAN HAPPEN.
reward_withdraw_amount = reward_withdraw_amount.min(pool.rewards_pool.value());
pool.rewards_pool.split(reward_withdraw_amount)
``` [1](#0-0) 

`redeem_fungible_staked_sui` has no such guard. It computes `rewards_amount` from the epoch-start exchange rate and immediately splits it from `rewards_pool`:

```move
sui_out.join(pool.rewards_pool.split(rewards_amount));
``` [2](#0-1) 

**Why the divergence occurs**

The exchange rate for the current epoch is recorded at epoch-start via `process_pending_stakes_and_withdraws` and reflects `sui_balance` (which includes the full `rewards_pool` at that moment): [3](#0-2) 

Within the epoch, `request_withdraw_stake` immediately splits rewards from `rewards_pool` (reducing it), but `sui_balance` and the exchange rate are **not updated** until the next epoch boundary. So the exchange rate's `sui_amount` remains stale — it still reflects the original `rewards_pool` balance — while the actual `rewards_pool` shrinks with every regular-staker withdrawal.

`redeem_fungible_staked_sui` then calls `calculate_fungible_staked_sui_withdraw_amount`, which derives `rewards_amount` from this stale exchange rate:

```move
let total_sui_amount = latest_exchange_rate.get_sui_amount(
    fungible_staked_sui_data_total_supply,
);
let total_rewards = total_sui_amount - fungible_staked_sui_data_principal_amount;
let rewards_withdraw_amount = mul_div!(
    fungible_staked_sui_value,
    total_rewards,
    fungible_staked_sui_data_total_supply,
);
``` [4](#0-3) 

If `rewards_amount > pool.rewards_pool.value()`, `balance::split` aborts the entire transaction.

---

### Impact Explanation

An FSS (`FungibleStakedSui`) holder's `redeem_fungible_staked_sui` transaction aborts with a Move abort when `rewards_pool` has been partially or fully depleted by regular stakers within the same epoch. The FSS object is not destroyed (Move atomicity rolls back the deletion), so the user can retry — but only after the next epoch boundary when `deposit_rewards` replenishes `rewards_pool` and the exchange rate is updated. This constitutes a temporary fund lock and harmful smart-contract behavior: a valid user action fails unexpectedly with no on-chain indication of when it will succeed.

---

### Likelihood Explanation

The condition is reachable by any ordinary SUI holder:

1. Multiple regular stakers call `request_withdraw_stake` during an epoch, each immediately reducing `rewards_pool` via `withdraw_rewards` (with its `.min()` guard silently capping their individual payout).
2. After enough withdrawals, `rewards_pool` falls below the `rewards_amount` that `calculate_fungible_staked_sui_withdraw_amount` computes for an FSS holder.
3. The FSS holder's `redeem_fungible_staked_sui` call aborts.

No privileged access is required. The trigger is ordinary staker behavior. The scenario is most likely at the start of a new epoch when `rewards_pool` is freshly populated and many stakers withdraw simultaneously, or in pools with a high ratio of regular-StakedSui withdrawals relative to FSS holders.

---

### Recommendation

Apply the same `.min(pool.rewards_pool.value())` clamp used in `withdraw_rewards` before splitting from `rewards_pool` in `redeem_fungible_staked_sui`:

```move
// In redeem_fungible_staked_sui, replace:
sui_out.join(pool.rewards_pool.split(rewards_amount));

// With:
let actual_rewards = rewards_amount.min(pool.rewards_pool.value());
sui_out.join(pool.rewards_pool.split(actual_rewards));
```

This mirrors the existing guard in `withdraw_rewards` and ensures the function never aborts due to an insufficient `rewards_pool` balance. The user receives slightly less than the exchange-rate-implied amount (bounded by rounding), consistent with the behavior already accepted for regular stakers.

---

### Proof of Concept

**State setup (within one epoch):**

| Variable | Value |
|---|---|
| `pool.sui_balance` (epoch-start) | 200 SUI |
| `pool.rewards_pool` (epoch-start) | 100 SUI |
| Exchange rate | `{sui_amount: 200, pool_token_amount: 100}` |
| `FungibleStakedSuiData.total_supply` | 50 pool tokens |
| `FungibleStakedSuiData.principal` | 50 SUI |
| FSS holder's `value` | 50 pool tokens (100% of FSS supply) |

**Step 1 — Regular staker withdraws all rewards:**

`request_withdraw_stake` calls `withdraw_rewards`, which computes `reward_withdraw_amount = 100` and clamps it to `min(100, 100) = 100`. `rewards_pool` is now **0**.

**Step 2 — FSS holder calls `redeem_fungible_staked_sui`:**

`calculate_fungible_staked_sui_withdraw_amount` computes (using the stale exchange rate):
- `total_sui_amount = get_sui_amount(50) = 200 * 50 / 100 = 100`
- `principal_amount = min(50, 100) = 50`
- `total_rewards = 100 - 50 = 50`
- `rewards_amount = mul_div(50, 50, 50) = 50`

Then:
```move
pool.rewards_pool.split(50)  // ABORTS: rewards_pool.value() == 0
``` [5](#0-4) 

The FSS holder's transaction aborts. They cannot redeem until the next epoch when `deposit_rewards` is called. [6](#0-5)

### Citations

**File:** crates/sui-framework/packages/sui-system/sources/staking_pool.move (L195-227)
```text
public(package) fun redeem_fungible_staked_sui(
    pool: &mut StakingPool,
    fungible_staked_sui: FungibleStakedSui,
    ctx: &TxContext,
): Balance<SUI> {
    let FungibleStakedSui { id, pool_id, value } = fungible_staked_sui;
    assert!(pool_id == object::id(pool), EWrongPool);

    id.delete();

    let latest_exchange_rate = pool.pool_token_exchange_rate_at_epoch(ctx.epoch());
    let fungible_staked_sui_data: &mut FungibleStakedSuiData =
        &mut pool.extra_fields[FungibleStakedSuiDataKey {}];

    let (
        principal_amount,
        rewards_amount,
    ) = latest_exchange_rate.calculate_fungible_staked_sui_withdraw_amount(
        value,
        fungible_staked_sui_data.principal.value(),
        fungible_staked_sui_data.total_supply,
    );

    fungible_staked_sui_data.total_supply = fungible_staked_sui_data.total_supply - value;

    let mut sui_out = fungible_staked_sui_data.principal.split(principal_amount);
    sui_out.join(pool.rewards_pool.split(rewards_amount));

    pool.pending_total_sui_withdraw = pool.pending_total_sui_withdraw + sui_out.value();
    pool.pending_pool_token_withdraw = pool.pending_pool_token_withdraw + value;

    sui_out
}
```

**File:** crates/sui-framework/packages/sui-system/sources/staking_pool.move (L237-261)
```text
    // 1. if the entire FungibleStakedSuiData supply is redeemed, how much sui should we receive?
    let total_sui_amount = latest_exchange_rate.get_sui_amount(
        fungible_staked_sui_data_total_supply,
    );

    // min with total_sui_amount to prevent underflow
    let fungible_staked_sui_data_principal_amount = fungible_staked_sui_data_principal_amount.min(
        total_sui_amount,
    );

    // 2. how much do we need to withdraw from the rewards pool?
    let total_rewards = total_sui_amount - fungible_staked_sui_data_principal_amount;

    // 3. proportionally withdraw from both wrt the fungible_staked_sui_value.
    let principal_withdraw_amount = mul_div!(
        fungible_staked_sui_value,
        fungible_staked_sui_data_principal_amount,
        fungible_staked_sui_data_total_supply,
    );

    let rewards_withdraw_amount = mul_div!(
        fungible_staked_sui_value,
        total_rewards,
        fungible_staked_sui_data_total_supply,
    );
```

**File:** crates/sui-framework/packages/sui-system/sources/staking_pool.move (L350-353)
```text
public(package) fun deposit_rewards(pool: &mut StakingPool, rewards: Balance<SUI>) {
    pool.sui_balance = pool.sui_balance + rewards.value();
    pool.rewards_pool.join(rewards);
}
```

**File:** crates/sui-framework/packages/sui-system/sources/staking_pool.move (L355-370)
```text
public(package) fun process_pending_stakes_and_withdraws(pool: &mut StakingPool, ctx: &TxContext) {
    let new_epoch = ctx.epoch() + 1;
    pool.process_pending_stake_withdraw();
    pool.process_pending_stake();
    pool
        .exchange_rates
        .add(
            new_epoch,
            PoolTokenExchangeRate {
                sui_amount: pool.sui_balance,
                pool_token_amount: pool.pool_token_balance,
            },
        );

    pool.check_balance_invariants(new_epoch);
}
```

**File:** crates/sui-framework/packages/sui-system/sources/staking_pool.move (L436-440)
```text
    // This may happen when we are withdrawing everything from the pool and
    // the rewards pool balance may be less than reward_withdraw_amount.
    // TODO: FIGURE OUT EXACTLY WHY THIS CAN HAPPEN.
    reward_withdraw_amount = reward_withdraw_amount.min(pool.rewards_pool.value());
    pool.rewards_pool.split(reward_withdraw_amount)
```
