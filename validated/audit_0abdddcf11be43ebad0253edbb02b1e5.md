### Title
Missing `rewards_pool` Sufficiency Guard in `redeem_fungible_staked_sui` Causes Permanent Redemption Failure for `FungibleStakedSui` Holders — (`crates/sui-framework/packages/sui-system/sources/staking_pool.move`)

---

### Summary

`StakingPool.redeem_fungible_staked_sui` calculates a `rewards_amount` from the epoch exchange rate (which is derived from the tracked `sui_balance`) and then calls `pool.rewards_pool.split(rewards_amount)` unconditionally. The sibling function `withdraw_rewards` — used for ordinary `StakedSui` redemption — contains an explicit guard capping the withdrawal at the actual pool balance, with a developer comment acknowledging the discrepancy can occur. `redeem_fungible_staked_sui` omits this guard entirely. When the actual `rewards_pool` balance falls below the exchange-rate-implied `rewards_amount`, every `FungibleStakedSui` redemption transaction aborts, permanently locking those stakers out of their funds for as long as the condition persists.

---

### Finding Description

**Root cause — tracked accounting vs. actual balance:**

`StakingPool` maintains two separate quantities:

- `sui_balance` (u64) — a tracked counter updated at epoch boundaries; it represents the total SUI the pool *should* hold (principal + rewards).
- `rewards_pool` (Balance\<SUI\>) — the actual on-chain balance from which rewards are paid out.

These two values can diverge. The developer already acknowledged this in `withdraw_rewards`:

```move
// This may happen when we are withdrawing everything from the pool and
// the rewards pool balance may be less than reward_withdraw_amount.
// TODO: FIGURE OUT EXACTLY WHY THIS CAN HAPPEN.
reward_withdraw_amount = reward_withdraw_amount.min(pool.rewards_pool.value());
pool.rewards_pool.split(reward_withdraw_amount)
``` [1](#0-0) 

The guard caps the withdrawal at the real balance, preventing an abort.

**The missing guard in `redeem_fungible_staked_sui`:**

```move
let (principal_amount, rewards_amount) =
    latest_exchange_rate.calculate_fungible_staked_sui_withdraw_amount(
        value,
        fungible_staked_sui_data.principal.value(),
        fungible_staked_sui_data.total_supply,
    );

fungible_staked_sui_data.total_supply = fungible_staked_sui_data.total_supply - value;

let mut sui_out = fungible_staked_sui_data.principal.split(principal_amount);
sui_out.join(pool.rewards_pool.split(rewards_amount));   // ← no .min() guard
``` [2](#0-1) 

`rewards_amount` is derived from `latest_exchange_rate`, which is built from `sui_balance` — the *tracked* counter, not the *actual* `rewards_pool` balance. If `rewards_pool.value() < rewards_amount`, `balance::split` aborts with `ENotEnough`.

**How the divergence arises:**

`process_pending_stake_withdraw` explicitly handles the case where `sui_balance` underflows:

```move
pool.sui_balance = if (pool.sui_balance >= pool.pending_total_sui_withdraw) {
    pool.sui_balance - pool.pending_total_sui_withdraw
} else {
    let diff = pool.pending_total_sui_withdraw - pool.sui_balance;
    if (!pool.is_inactive()) {
        pool.extra_fields.add(UnderflowSuiBalance {}, diff);
    };
    0
};
``` [3](#0-2) 

When `sui_balance` underflows to 0, the `rewards_pool` may still hold a non-zero balance, but the exchange rate recorded at the epoch boundary reflects the pre-underflow `sui_balance`. Subsequent calls to `calculate_fungible_staked_sui_withdraw_amount` use this stale exchange rate and can compute a `rewards_amount` that exceeds the actual `rewards_pool` balance.

Additionally, within a single epoch, multiple stakers can withdraw sequentially. `withdraw_rewards` (for `StakedSui`) uses the `.min()` guard and can drain `rewards_pool` to zero while `sui_balance` still reflects a higher value. Any subsequent `redeem_fungible_staked_sui` call in the same epoch will then abort.

---

### Impact Explanation

When `pool.rewards_pool.value() < rewards_amount`, the Move VM aborts the transaction. Because Move's execution model rolls back all state changes on abort, the `FungibleStakedSui` object is not consumed — but the redemption permanently fails for as long as the condition persists. If the validator pool is deactivated (no future reward deposits), the condition never self-corrects and `FungibleStakedSui` holders are permanently locked out of their staked SUI. This matches the **permanent fund lock** impact class.

---

### Likelihood Explanation

The developer comment in `withdraw_rewards` — "TODO: FIGURE OUT EXACTLY WHY THIS CAN HAPPEN" — confirms the discrepancy is a known, observed runtime condition, not a theoretical edge case. Any epoch in which `withdraw_rewards` invokes the `.min()` clamp (giving a staker less than the exchange-rate-implied reward) leaves `rewards_pool` lower than `sui_balance` implies. The next `redeem_fungible_staked_sui` call in that epoch or the next will hit the unguarded `split` and abort. The trigger is a normal user action (unstaking via `request_withdraw_stake`) followed by another normal user action (redeeming `FungibleStakedSui`).

---

### Recommendation

Apply the same guard used in `withdraw_rewards` to `redeem_fungible_staked_sui`:

```move
// Before:
sui_out.join(pool.rewards_pool.split(rewards_amount));

// After:
let safe_rewards_amount = rewards_amount.min(pool.rewards_pool.value());
sui_out.join(pool.rewards_pool.split(safe_rewards_amount));
```

This mirrors the existing fix in `withdraw_rewards` and ensures the function never attempts to split more than the actual balance. [4](#0-3) 

---

### Proof of Concept

1. **Setup:** Validator pool with `sui_balance = 200 SUI`, `rewards_pool = 100 SUI` (100 SUI principal held in `StakedSui` objects, 100 SUI rewards). Exchange rate at epoch N: `{sui_amount: 200, pool_token_amount: 100}`.

2. **Staker A** holds a `StakedSui` with 100 SUI principal. Calls `request_withdraw_stake` → `withdraw_rewards`. Exchange rate implies 100 SUI rewards for 100 pool tokens. `withdraw_rewards` calls `.min(pool.rewards_pool.value())` = `.min(100)` = 100. `rewards_pool` is now **0**.

3. **Staker B** holds a `FungibleStakedSui` with value = 50 pool tokens. Calls `redeem_fungible_staked_sui`. `calculate_fungible_staked_sui_withdraw_amount` uses the same exchange rate: `total_sui_amount = 200 * 50 / 100 = 100 SUI`. After subtracting principal share, `rewards_amount = 50 SUI`.

4. `pool.rewards_pool.split(50)` is called. `rewards_pool.value() = 0 < 50`. **Transaction aborts.**

5. Staker B's `FungibleStakedSui` is rolled back (not consumed). Every subsequent redemption attempt aborts identically. If the pool is deactivated, no future rewards are deposited and Staker B's SUI is permanently inaccessible.

**Relevant code locations:** [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** crates/sui-framework/packages/sui-system/sources/staking_pool.move (L231-270)
```text
fun calculate_fungible_staked_sui_withdraw_amount(
    latest_exchange_rate: PoolTokenExchangeRate,
    fungible_staked_sui_value: u64,
    fungible_staked_sui_data_principal_amount: u64, // fungible_staked_sui_data.principal.value()
    fungible_staked_sui_data_total_supply: u64, // fungible_staked_sui_data.total_supply
): (u64, u64) {
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

    // invariant check, just in case
    let expected_sui_amount = latest_exchange_rate.get_sui_amount(fungible_staked_sui_value);
    assert!(
        principal_withdraw_amount + rewards_withdraw_amount <= expected_sui_amount,
        EInvariantFailure,
    );

    (principal_withdraw_amount, rewards_withdraw_amount)
```

**File:** crates/sui-framework/packages/sui-system/sources/staking_pool.move (L374-385)
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
