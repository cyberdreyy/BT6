### Title
`withdraw_rewards` silently caps staker reward payout to available pool balance, causing permanent reward loss - (File: `crates/sui-framework/packages/sui-system/sources/staking_pool.move`)

### Summary

The `withdraw_rewards` function in `staking_pool.move` contains a `.min()` cap that silently truncates a staker's reward payout to whatever SUI happens to be in `rewards_pool` at withdrawal time. When the pool is undersupplied, the staker receives fewer rewards than the exchange rate entitles them to, the shortfall is never recorded, and the lost SUI is permanently unrecoverable. This is the direct Sui analog of the `safeRewardTransfer` silent-failure pattern from the external report.

### Finding Description

`withdraw_rewards` computes `reward_withdraw_amount` from the historical exchange rate — the mathematically correct amount of SUI rewards the staker has earned — and then unconditionally clamps it:

```move
// This may happen when we are withdrawing everything from the pool and
// the rewards pool balance may be less than reward_withdraw_amount.
// TODO: FIGURE OUT EXACTLY WHY THIS CAN HAPPEN.
reward_withdraw_amount = reward_withdraw_amount.min(pool.rewards_pool.value());
pool.rewards_pool.split(reward_withdraw_amount)
``` [1](#0-0) 

The caller `request_withdraw_stake` joins whatever `withdraw_rewards` returns directly onto the principal and returns it to the staker, with no check that the returned amount equals the computed entitlement:

```move
let rewards_withdraw = pool.withdraw_rewards(
    principal_withdraw_amount,
    pool_token_withdraw_amount,
    ctx.epoch(),
);
let total_sui_withdraw_amount = principal_withdraw_amount + rewards_withdraw.value();
pool.pending_total_sui_withdraw = pool.pending_total_sui_withdraw + total_sui_withdraw_amount;
...
principal_withdraw.join(rewards_withdraw);
principal_withdraw
``` [2](#0-1) 

Three properties make this a silent permanent loss:

1. **No revert.** The function does not abort when `pool.rewards_pool.value() < reward_withdraw_amount`; it silently delivers a smaller balance.
2. **No shortfall accounting.** `pending_total_sui_withdraw` is updated with the *reduced* amount, so the pool's bookkeeping never records that a debt exists. The missing SUI is not deferred, not queued, and not recoverable.
3. **Acknowledged but unexplained.** The `TODO` comment confirms the developers know the condition can occur but do not know why, meaning no upstream fix prevents it from being triggered.

The `rewards_pool` can be undersupplied relative to what the exchange rate promises whenever:
- Multiple stakers withdraw in the same epoch and the pool's `rewards_pool` is exhausted before the last withdrawal is processed.
- The pool operated in safe mode (epoch advancement skipped reward deposit), leaving `rewards_pool` lower than `sui_balance` implies.
- Integer-division rounding in `deposit_rewards` / `distribute_reward` accumulates a deficit over many epochs. [3](#0-2) 

### Impact Explanation

Any staker who calls `request_withdraw_stake` (routed through `sui_system::request_withdraw_stake`) when the pool's `rewards_pool` is undersupplied receives fewer SUI than the exchange rate entitles them to. The shortfall is silently discarded — it is not credited to the staker later, not returned to the storage fund, and not emitted as an event. This constitutes **permanent, unrecoverable loss of staking rewards** for ordinary SUI holders, matching the "permanent fund lock / harmful smart-contract behavior" High/Medium impact class.

### Likelihood Explanation

The condition is reachable from public input by any staker. The developers' own `TODO` comment confirms they cannot rule it out. The `UnderflowSuiBalance` mechanism in `process_pending_stake_withdraw` already demonstrates that the pool's internal balances can diverge from what the exchange rate implies: [4](#0-3) 

Safe-mode epochs (where `advance_epoch` is skipped) are a documented, production-observed scenario (see the epoch-560 special case in `sui_system_state_inner.move`) that can leave `rewards_pool` undersupplied relative to outstanding entitlements. [5](#0-4) 

### Recommendation

Replace the silent `.min()` cap with one of the following:

1. **Assert sufficiency** — abort with `EInsufficientRewardsPoolBalance` (error code 4, already defined) if `pool.rewards_pool.value() < reward_withdraw_amount`. This forces the root cause to be fixed rather than silently absorbed. [6](#0-5) 

2. **Record the shortfall** — if a cap is kept for liveness, store the deficit in `extra_fields` (analogous to `UnderflowSuiBalance`) and repay it at the next `deposit_rewards` call, so no staker permanently loses their entitlement.

### Proof of Concept

1. Validator pool has `sui_balance = 2000`, `rewards_pool = 500`, `pool_token_balance = 1000` (exchange rate 2:1 SUI per token).
2. Staker A holds a `StakedSui` with `principal = 1000` SUI, staked at the 1:1 epoch. Their pool tokens = 1000.
3. Staker A calls `request_withdraw_stake`.
4. `withdraw_rewards` computes `total_sui_withdraw_amount = get_sui_amount(1000) = 2000`, `reward_withdraw_amount = 2000 - 1000 = 1000`.
5. `pool.rewards_pool.value() = 500 < 1000`, so the cap fires: `reward_withdraw_amount = 500`.
6. Staker A receives `1000 (principal) + 500 (rewards) = 1500` SUI instead of the correct `2000` SUI.
7. The 500 SUI shortfall is never recorded. `pending_total_sui_withdraw` is set to `1500`, not `2000`. The missing 500 SUI is permanently lost. [7](#0-6)

### Citations

**File:** crates/sui-framework/packages/sui-system/sources/staking_pool.move (L15-19)
```text
const EInsufficientPoolTokenBalance: u64 = 0;
const EWrongPool: u64 = 1;
const EWithdrawAmountCannotBeZero: u64 = 2;
const EInsufficientSuiTokenBalance: u64 = 3;
const EInsufficientRewardsPoolBalance: u64 = 4;
```

**File:** crates/sui-framework/packages/sui-system/sources/staking_pool.move (L176-192)
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

    // If the pool is inactive or preactive, we immediately process the withdrawal.
    if (pool.is_inactive() || pool.is_preactive()) pool.process_pending_stake_withdraw();

    // TODO: implement withdraw bonding period here.
    principal_withdraw.join(rewards_withdraw);
    principal_withdraw
```

**File:** crates/sui-framework/packages/sui-system/sources/staking_pool.move (L349-353)
```text
/// Called at epoch advancement times to add rewards (in SUI) to the staking pool.
public(package) fun deposit_rewards(pool: &mut StakingPool, rewards: Balance<SUI>) {
    pool.sui_balance = pool.sui_balance + rewards.value();
    pool.rewards_pool.join(rewards);
}
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

**File:** crates/sui-framework/packages/sui-system/sources/sui_system_state_inner.move (L918-931)
```text
        // special case for epoch 560 -> 561 change bug. add extra subsidies for "safe mode"
        // where reward distribution was skipped. use distribution counter and epoch check to
        // avoiding affecting devnet and testnet
        if (self.stake_subsidy.get_distribution_counter() == 540 && old_epoch > 560) {
            // safe mode was entered on the change from 560 to 561. so 560 was the first epoch without proper subsidy distribution
            let first_safe_mode_epoch = 560;
            let safe_mode_epoch_count = old_epoch - first_safe_mode_epoch;
            safe_mode_epoch_count.do!(|_| {
                stake_subsidy.join(self.stake_subsidy.advance_epoch());
            });
            // done with catchup for safe mode epochs. distribution counter is now >540, we won't hit this again
            // fall through to the normal logic, which will add subsidies for the current epoch
        };
        stake_subsidy.join(self.stake_subsidy.advance_epoch());
```
