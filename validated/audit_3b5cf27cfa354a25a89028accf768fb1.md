No vulnerability found for this question.

**Analysis:**

`vest()` and `vest_many()` are permissionless by design — they take only a `contract_address: address` argument and no signer, meaning the module intentionally allows *anyone* to call them as a keeper-style function. [1](#0-0) 

The function logic is fully time-gated and deterministic — it computes `last_completed_period` from `timestamp::now_seconds()` and the immutable `vesting_schedule.start_timestamp_secs`/`period_duration`, and only proceeds if `last_completed_period >= next_period_to_vest`. It cannot be forced to release a period earlier than what real elapsed time already permits; it can at most "catch up" on periods that are already due. [2](#0-1) 

`unlock_stake` merely calls `staking_contract::unlock_stake`, moving the coins from active to `pending_inactive` within the *same* stake pool owned by the vesting contract — it does not withdraw, redirect, or send funds to any account, attacker or otherwise. [3](#0-2) 

Funds moved to `pending_inactive` are still subject to the stake pool's lockup expiration before they become withdrawable via `distribute()`, so calling `vest()` early (i.e., as soon as a period is due) confers no early-withdrawal advantage to anyone, attacker or shareholder. [4](#0-3) 

Accounting updates (`remaining_grant`, `vesting_schedule.last_vested_period`) are strictly derived from the deterministic schedule and elapsed time, not from any attacker-supplied value, so there is no way for an unrelated caller to corrupt balances or redirect value to themselves — the module docs explicitly describe this "anyone can trigger vest for the schedule" flow as intended behavior. [5](#0-4) 

Since the call cannot unlock ahead of the actual elapsed-time schedule, does not redirect funds to the caller, and does not bypass the stake pool's lockup before withdrawal becomes possible, this does not meet the review's required impact of unauthorized withdrawal, redirection, or reactivate/lock-timing exploitation.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L11-24)
```text
/// Shareholder flow:
/// 1. Admin calls create_vesting_contract with a schedule of [3/48, 3/48, 1/48] with a vesting cliff of 1 year and
/// vesting period of 1 month.
/// 2. After a month, a shareholder calls unlock_rewards to request rewards. They can also call vest() which would also
/// unlocks rewards but since the 1 year cliff has not passed (vesting has not started), vest() would not release any of
/// the original grant.
/// 3. After the unlocked rewards become fully withdrawable (as it's subject to staking lockup), shareholders can call
/// distribute() to send all withdrawable funds to all shareholders based on the original grant's shares structure.
/// 4. After 1 year and 1 month, the vesting schedule now starts. Shareholders call vest() to unlock vested coins. vest()
/// checks the schedule and unlocks 3/48 of the original grant in addition to any accumulated rewards since last
/// unlock_rewards(). Once the unlocked coins become withdrawable, shareholders can call distribute().
/// 5. Assuming the shareholders forgot to call vest() for 2 months, when they call vest() again, they will unlock vested
/// tokens for the next period since last vest. This would be for the first month they missed. They can call vest() a
/// second time to unlock for the second month they missed.
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L654-665)
```text
    /// Unlock any vested portion of the grant.
    public entry fun vest(contract_address: address) acquires VestingContract {
        // Unlock all rewards first, if any.
        unlock_rewards(contract_address);

        // Unlock the vested amount. This amount will become withdrawable when the underlying stake pool's lockup
        // expires.
        let vesting_contract = borrow_global_mut<VestingContract>(contract_address);
        // Short-circuit if vesting hasn't started yet.
        if (vesting_contract.vesting_schedule.start_timestamp_secs > timestamp::now_seconds()) {
            return
        };
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L667-693)
```text
        // Check if the next vested period has already passed. If not, short-circuit since there's nothing to vest.
        let vesting_schedule = &mut vesting_contract.vesting_schedule;
        let last_vested_period = vesting_schedule.last_vested_period;
        let next_period_to_vest = last_vested_period + 1;
        let last_completed_period =
            (timestamp::now_seconds() - vesting_schedule.start_timestamp_secs) / vesting_schedule.period_duration;
        if (last_completed_period < next_period_to_vest) {
            return
        };

        // Calculate how much has vested, excluding rewards.
        // Index is 0-based while period is 1-based so we need to subtract 1.
        let schedule = &vesting_schedule.schedule;
        let schedule_index = next_period_to_vest - 1;
        let vesting_fraction = if (schedule_index < schedule.length()) {
            schedule[schedule_index]
        } else {
            // Last vesting schedule fraction will repeat until the grant runs out.
            schedule[schedule.length() - 1]
        };
        let total_grant = vesting_contract.grant_pool.total_coins();
        let vested_amount = fixed_point32::multiply_u64(total_grant, vesting_fraction);
        // Cap vested amount by the remaining grant amount so we don't try to distribute more than what's remaining.
        vested_amount = min(vested_amount, vesting_contract.remaining_grant);
        vesting_contract.remaining_grant -= vested_amount;
        vesting_schedule.last_vested_period = next_period_to_vest;
        unlock_stake(vesting_contract, vested_amount);
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L718-756)
```text
    /// Distribute any withdrawable stake from the stake pool.
    public entry fun distribute(contract_address: address) acquires VestingContract {
        assert_active_vesting_contract(contract_address);






























                admin: vesting_contract.admin,
                vesting_contract_address: contract_address,
                amount: total_distribution_amount,
            },
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L1066-1069)
```text
    fun unlock_stake(vesting_contract: &VestingContract, amount: u64) {
        let contract_signer = &get_vesting_account_signer_internal(vesting_contract);
        staking_contract::unlock_stake(contract_signer, vesting_contract.staking.operator, amount);
    }
```
