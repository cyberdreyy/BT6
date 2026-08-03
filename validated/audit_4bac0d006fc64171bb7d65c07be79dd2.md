No vulnerability found for this question.

**Analysis supporting this conclusion:**

`vesting::distribute` is intentionally an unprivileged, callable-by-anyone entry function [1](#0-0) . Its safety does not depend on caller identity — it depends on the underlying stake-pool accounting being caller-independent:

1. `distribute` invokes `withdraw_stake`, which calls `staking_contract::distribute` and then withdraws whatever balance actually lands in the vesting contract's account [2](#0-1) .

2. `staking_contract::distribute`/`distribute_internal` ultimately bottoms out in `stake::withdraw_with_cap`, which caps the withdrawable amount strictly by the `StakePool.inactive` coin balance — not by `pending_inactive`, and not influenced by how many times or who calls it [3](#0-2) . Coins only move from `pending_inactive` to `inactive` automatically once `locked_until_secs` has actually passed (during `withdraw_with_cap`'s own lockup-expiry check, or during epoch transitions), never as a side effect of calling `distribute` early or repeatedly.

3. The vesting-schedule accounting (`remaining_grant`) is only decremented inside `vest()`, based on `vesting_schedule.last_vested_period` progression — `distribute()` never touches `remaining_grant` [4](#0-3) . So repeated/early calls to `distribute` cannot desynchronize or "permanently understate" `remaining_grant`; that value is governed solely by `vest()`'s own schedule check (`last_completed_period < next_period_to_vest` short-circuit) [5](#0-4) .

4. `distribute` pays out shares proportional to `grant_pool` shares against whatever `total_distribution_amount` was actually withdrawn — it cannot pay more than what is actually inactive/withdrawable in the stake pool [6](#0-5) .

Because the amount withdrawable is gated by the stake pool's own lockup-expiry logic (`locked_until_secs`) and not by transaction origin or call frequency, an unprivileged caller invoking `distribute`/`distribute_many` any number of times before `fast_forward_to_unlock`/lockup expiry simply withdraws zero (or whatever was already legitimately unlocked) — it cannot force payout of principal ahead of the vesting schedule or corrupt `remaining_grant`. The formal spec for `distribute` (`DistributeInternalAbortsIf`) also encodes this bound explicitly via `inactive + pending_inactive` and the `stake::withdraw_with_cap` lockup check [7](#0-6) . This is a documented, intended design ("Allow anyone to distribute already unlocked funds") rather than a role-check gap [8](#0-7) .

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L667-675)
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
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L677-693)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L718-723)
```text
    /// Distribute any withdrawable stake from the stake pool.
    public entry fun distribute(contract_address: address) acquires VestingContract {
        assert_active_vesting_contract(contract_address);

        let vesting_contract = borrow_global_mut<VestingContract>(contract_address);
        let coins = withdraw_stake(vesting_contract, contract_address);
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L730-740)
```text
        // Distribute coins to all shareholders in the vesting contract.
        let grant_pool = &vesting_contract.grant_pool;
        let shareholders = &grant_pool.shareholders();
        shareholders.for_each_ref(|shareholder| {
            let shareholder = *shareholder;
            let shares = pool_u64::shares(grant_pool, shareholder);
            let amount = pool_u64::shares_to_amount_with_total_coins(grant_pool, shares, total_distribution_amount);
            let share_of_coins = coin::extract(&mut coins, amount);
            let recipient_address = get_beneficiary(vesting_contract, shareholder);
            aptos_account::deposit_coins(recipient_address, share_of_coins);
        });
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L1071-1078)
```text
    fun withdraw_stake(vesting_contract: &VestingContract, contract_address: address): Coin<AptosCoin> {
        // Claim any withdrawable distribution from the staking contract. The withdrawn coins will be sent directly to
        // the vesting contract's account.
        staking_contract::distribute(contract_address, vesting_contract.staking.operator);
        let withdrawn_coins = coin::balance<AptosCoin>(contract_address);
        let contract_signer = &get_vesting_account_signer_internal(vesting_contract);
        coin::withdraw<AptosCoin>(contract_signer, withdrawn_coins)
    }
```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1180-1204)
```text
    public fun withdraw_with_cap(
        owner_cap: &OwnerCapability, withdraw_amount: u64
    ): Coin<AptosCoin> acquires StakePool, ValidatorSet {
        assert_reconfig_not_in_progress();
        let pool_address = owner_cap.pool_address;
        assert_stake_pool_exists(pool_address);
        let stake_pool = borrow_global_mut<StakePool>(pool_address);
        // There's an edge case where a validator unlocks their stake and leaves the validator set before
        // the stake is fully unlocked (the current lockup cycle has not expired yet).
        // This can leave their stake stuck in pending_inactive even after the current lockup cycle expires.
        if (get_validator_state(pool_address) == VALIDATOR_STATUS_INACTIVE
            && timestamp::now_seconds() >= stake_pool.locked_until_secs) {
            let pending_inactive_stake =
                coin::extract_all(&mut stake_pool.pending_inactive);
            coin::merge(&mut stake_pool.inactive, pending_inactive_stake);
        };

        // Cap withdraw amount by total inactive coins.
        withdraw_amount = min(withdraw_amount, coin::value(&stake_pool.inactive));
        if (withdraw_amount == 0) return coin::zero<AptosCoin>();

        event::emit(WithdrawStake { pool_address, amount_withdrawn: withdraw_amount });

        coin::extract(&mut stake_pool.inactive, withdraw_amount)
    }
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.spec.move (L583-610)
```text
    spec schema DistributeInternalAbortsIf {
        staker: address;    // The verification below does not contain the loop in staking_contract::update_distribution_pool().
        operator: address;
        staking_contract: staking_contract::StakingContract;

        let pool_address = staking_contract.pool_address;
        aborts_if !exists<stake::StakePool>(pool_address);
        let stake_pool = global<stake::StakePool>(pool_address);
        let inactive = stake_pool.inactive.value;
        let pending_inactive = stake_pool.pending_inactive.value;
        aborts_if inactive + pending_inactive > MAX_U64;

        // verify stake::withdraw_with_cap()
        let total_potential_withdrawable = inactive + pending_inactive;
        let pool_address_1 = staking_contract.owner_cap.pool_address;
        aborts_if !exists<stake::StakePool>(pool_address_1);
        let stake_pool_1 = global<stake::StakePool>(pool_address_1);
        aborts_if !exists<stake::ValidatorSet>(@aptos_framework);
        let validator_set = global<stake::ValidatorSet>(@aptos_framework);
        let inactive_state = !stake::spec_contains(validator_set.pending_active, pool_address_1)
            && !stake::spec_contains(validator_set.active_validators, pool_address_1)
            && !stake::spec_contains(validator_set.pending_inactive, pool_address_1);
        let inactive_1 = stake_pool_1.inactive.value;
        let pending_inactive_1 = stake_pool_1.pending_inactive.value;
        let new_inactive_1 = inactive_1 + pending_inactive_1;
        aborts_if inactive_state && timestamp::spec_now_seconds() >= stake_pool_1.locked_until_secs
            && inactive_1 + pending_inactive_1 > MAX_U64;
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L840-840)
```text
    /// Allow anyone to distribute already unlocked funds. This does not affect reward compounding and therefore does
```
