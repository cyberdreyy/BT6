### Title
`withdraw_rewards` Silently Caps Staker Reward Payout Without Revert, Permanently Consuming `StakedSui` — (File: `crates/sui-framework/packages/sui-system/sources/staking_pool.move`)

---

### Summary

`withdraw_rewards` in `staking_pool.move` silently caps the reward payout at `min(calculated_reward, pool.rewards_pool.value())`. Because `request_withdraw_stake` takes `StakedSui` by value and destroys it unconditionally before returning, a staker whose withdrawal races against a depleted rewards pool permanently loses the shortfall with no revert and no retry path. No minimum-output parameter exists for the caller to specify.

---

### Finding Description

The private function `withdraw_rewards` computes the reward a staker is owed from the exchange rate, then silently truncates it:

```move
// staking_pool.move lines 432–440
let mut reward_withdraw_amount = if (total_sui_withdraw_amount >= principal_withdraw_amount) {
    total_sui_withdraw_amount - principal_withdraw_amount
} else 0;

// This may happen when we are withdrawing everything from the pool and
// the rewards pool balance may be less than reward_withdraw_amount.
// TODO: FIGURE OUT EXACTLY WHY THIS CAN HAPPEN.
reward_withdraw_amount = reward_withdraw_amount.min(pool.rewards_pool.value());
pool.rewards_pool.split(reward_withdraw_amount)
``` [1](#0-0) 

The caller (`request_withdraw_stake`) takes `StakedSui` by value and passes it to `withdraw_from_principal`, which destructs it:

```move
// staking_pool.move lines 157–192
public(package) fun request_withdraw_stake(
    pool: &mut StakingPool,
    staked_sui: StakedSui,   // consumed by value
    ...
``` [2](#0-1) 

The public entry point reachable by any ordinary SUI holder is:

```move
// sui_system.move lines 267–274
public entry fun request_withdraw_stake(
    wrapper: &mut SuiSystemState,
    staked_sui: StakedSui,
    ctx: &mut TxContext,
) {
    let withdrawn_stake = wrapper.request_withdraw_stake_non_entry(staked_sui, ctx);
    transfer::public_transfer(withdrawn_stake.into_coin(ctx), ctx.sender());
}
``` [3](#0-2) 

The `rewards_pool` is a shared `Balance<SUI>` inside `StakingPool` that is funded once per epoch via `deposit_rewards`. During an epoch, every call to `request_withdraw_stake` drains from it. When the pool is depleted below the calculated reward for a later withdrawer, `withdraw_rewards` silently delivers less than owed. The `StakedSui` has already been destroyed, so the staker cannot retry. [4](#0-3) 

The developers acknowledge the condition with a `TODO: FIGURE OUT EXACTLY WHY THIS CAN HAPPEN` comment but leave no guard in place. [5](#0-4) 

---

### Impact Explanation

A staker who calls `request_withdraw_stake` when `pool.rewards_pool.value()` is less than their calculated reward receives `principal + actual_pool_balance_share` instead of `principal + calculated_reward`. The difference is permanently lost to the staker: their `StakedSui` is consumed and cannot be reused. The shortfall remains in the pool and accrues to other participants. This constitutes harmful smart-contract behavior — a user loses funds they are entitled to with no revert and no recourse.

---

### Likelihood Explanation

The `rewards_pool` is funded once per epoch. Within a single epoch, concurrent or sequential calls to `request_withdraw_stake` drain it. If aggregate withdrawals in an epoch exceed the deposited rewards (possible when many stakers exit simultaneously, or when a validator's rewards pool is small relative to pending withdrawals), later withdrawers are silently shorted. The condition is reachable by any ordinary SUI holder without any privileged access.

---

### Recommendation

Add a `min_sui_out: u64` parameter to `request_withdraw_stake` (and its public entry wrapper) and assert after computing the total payout:

```move
assert!(
    principal_withdraw_amount + rewards_withdraw.value() >= min_sui_out,
    ESlippageExceeded
);
```

Alternatively, abort inside `withdraw_rewards` when `pool.rewards_pool.value() < reward_withdraw_amount` rather than silently capping. Either approach ensures the `StakedSui` is not consumed when the pool cannot honour the full reward.

---

### Proof of Concept

1. Epoch N begins; `deposit_rewards` adds R SUI to `pool.rewards_pool`.
2. Staker A calls `request_withdraw_stake`; their calculated reward is R − 1. Pool now holds 1 MIST.
3. Staker B calls `request_withdraw_stake`; their calculated reward is 500 MIST.
4. Inside `withdraw_rewards` for B: `reward_withdraw_amount = 500`, `pool.rewards_pool.value() = 1`, so `reward_withdraw_amount = min(500, 1) = 1`.
5. B's `StakedSui` is destroyed. B receives `principal + 1 MIST` instead of `principal + 500 MIST`.
6. No error is raised. B has permanently lost 499 MIST of rewards with no retry path. [6](#0-5) [7](#0-6) [3](#0-2)

### Citations

**File:** crates/sui-framework/packages/sui-system/sources/staking_pool.move (L49-51)
```text
    /// The epoch stake rewards will be added here at the end of each epoch.
    rewards_pool: Balance<SUI>,
    /// Total number of pool tokens issued by the pool.
```

**File:** crates/sui-framework/packages/sui-system/sources/staking_pool.move (L157-193)
```text
public(package) fun request_withdraw_stake(
    pool: &mut StakingPool,
    staked_sui: StakedSui,
    ctx: &TxContext,
): Balance<SUI> {
    // stake is inactive and the pool is not preactive - allow direct withdraw
    // the reason why we exclude preactive pools is to avoid potential underflow
    // on subtraction, and we need to enforce `pending_stake_withdraw` call.
    if (staked_sui.stake_activation_epoch > ctx.epoch() && !pool.is_preactive()) {
        let principal = staked_sui.into_balance();
        pool.pending_stake = pool.pending_stake - principal.value();
        return principal
    };

    let (pool_token_withdraw_amount, mut principal_withdraw) = pool.withdraw_from_principal(
        staked_sui,
    );
    let principal_withdraw_amount = principal_withdraw.value();

    let rewards_withdraw = pool.withdraw_rewards(
        principal_withdraw_amount,
        pool_token_withdraw_amount,
        ctx.epoch(),
    );
    let total_sui_withdraw_amount = principal_withdraw_amount + rewards_withdraw.value();

    pool.pending_total_sui_withdraw = pool.pending_total_sui_withdraw + total_sui_withdraw_amount;
    pool.pending_pool_token_withdraw =
        pool.pending_pool_token_withdraw + pool_token_withdraw_amount;

    // If the pool is inactive or preactive, we immediately process the withdrawal.
    if (pool.is_inactive() || pool.is_preactive()) pool.process_pending_stake_withdraw();

    // TODO: implement withdraw bonding period here.
    principal_withdraw.join(rewards_withdraw);
    principal_withdraw
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

**File:** crates/sui-framework/packages/sui-system/sources/sui_system.move (L267-274)
```text
public entry fun request_withdraw_stake(
    wrapper: &mut SuiSystemState,
    staked_sui: StakedSui,
    ctx: &mut TxContext,
) {
    let withdrawn_stake = wrapper.request_withdraw_stake_non_entry(staked_sui, ctx);
    transfer::public_transfer(withdrawn_stake.into_coin(ctx), ctx.sender());
}
```
