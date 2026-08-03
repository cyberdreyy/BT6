No vulnerability found for this question.

**Analysis:**

The claimed double-counting bug does not exist in the actual code. Both `stake::unlock` and `stake::withdraw` are gated by `OwnerCapability`, requiring the caller to already hold the pool's owner capability — an unprivileged, non-owner caller cannot invoke these entrypoints against someone else's pool at all [1](#0-0) [2](#0-1) .

Even granting the owner's own actions, the accounting cannot double-count because:

1. `unlock_with_cap` caps the amount to move from `active` to `pending_inactive` by the actual `active` coin balance via `min(amount, coin::value(&stake_pool.active))`, then performs `coin::extract`/`coin::merge` on real `Coin<AptosCoin>` resources — a value-conserving transfer, not an independent counter increment [3](#0-2) .
2. `withdraw_with_cap` caps `withdraw_amount` by the actual `inactive` coin balance via `min(withdraw_amount, coin::value(&stake_pool.inactive))` before extracting, so it is impossible to withdraw more than what is actually held as `inactive` [4](#0-3) .
3. The transition from `pending_inactive` to `inactive` only happens once, at epoch boundary (`update_stake_pool`/`distribute_rewards`), via `coin::extract_all`/`coin::merge`, guarded by `locked_until_secs` comparison — repeated epoch advances without new `unlock` calls just find `pending_inactive` empty and merge nothing [5](#0-4) .

Since Move's `Coin<T>` type enforces conservation of value (an `extract` strictly decreases one balance while the paired `merge` strictly increases another by the exact same amount, and there's no way to fabricate extra coins), there is no arithmetic path by which repeated `unlock`/epoch-advance/`withdraw` cycles — regardless of how the `u64 amount` argument is encoded on the wire — can inflate `inactive` beyond the cumulative principal + rewards actually present in the `StakePool` resource. The delegation_pool layer built on top (`unlock_internal`, `withdraw_internal`) adds share-accounting on top of this same conservation-safe base and includes explicit synchronization/anti-double-count logic (e.g., tracking `total_coins_inactive` and excess pending_inactive escaping) [6](#0-5) .

There is also no unprivileged, non-owner path into `stake::unlock`/`stake::withdraw` as required by the review bounds — the premise assumes bypassing the `OwnerCapability` check, which the review standard explicitly rejects.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1141-1147)
```text
    public entry fun unlock(owner: &signer, amount: u64) acquires OwnerCapability, StakePool {
        assert_reconfig_not_in_progress();
        let owner_address = signer::address_of(owner);
        assert_owner_cap_exists(owner_address);
        let ownership_cap = borrow_global<OwnerCapability>(owner_address);
        unlock_with_cap(amount, ownership_cap);
    }
```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1160-1163)
```text
        // Cap amount to unlock by maximum active stake.
        let amount = min(amount, coin::value(&stake_pool.active));
        let unlocked_stake = coin::extract(&mut stake_pool.active, amount);
        coin::merge<AptosCoin>(&mut stake_pool.pending_inactive, unlocked_stake);
```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1169-1177)
```text
    public entry fun withdraw(
        owner: &signer, withdraw_amount: u64
    ) acquires OwnerCapability, StakePool, ValidatorSet {
        let owner_address = signer::address_of(owner);
        assert_owner_cap_exists(owner_address);
        let ownership_cap = borrow_global<OwnerCapability>(owner_address);
        let coins = withdraw_with_cap(ownership_cap, withdraw_amount);
        coin::deposit<AptosCoin>(owner_address, coins);
    }
```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1197-1203)
```text
        // Cap withdraw amount by total inactive coins.
        withdraw_amount = min(withdraw_amount, coin::value(&stake_pool.inactive));
        if (withdraw_amount == 0) return coin::zero<AptosCoin>();

        event::emit(WithdrawStake { pool_address, amount_withdrawn: withdraw_amount });

        coin::extract(&mut stake_pool.inactive, withdraw_amount)
```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1952-1959)
```text
        // Pending inactive stake is only fully unlocked and moved into inactive if the current lockup cycle has expired
        let current_lockup_expiration = stake_pool.locked_until_secs;
        if (get_reconfig_start_time_secs() >= current_lockup_expiration) {
            coin::merge(
                &mut stake_pool.inactive,
                coin::extract_all(&mut stake_pool.pending_inactive)
            );
        };
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1651-1676)
```text
        let stake_pool_owner = &retrieve_stake_pool_owner(pool);
        // stake pool will inactivate entire pending_inactive stake at `stake::withdraw` to make it withdrawable
        // however, bypassing the inactivation of excess stake (inactivated but not withdrawn) ensures
        // the OLC is not advanced indefinitely on `unlock`-`withdraw` paired calls
        if (can_withdraw_pending_inactive(pool_address)) {
            // get excess stake before being entirely inactivated
            let (_, _, _, pending_inactive) = stake::get_stake(pool_address);
            if (withdrawal_olc.index == pool.observed_lockup_cycle.index) {
                // `amount` less excess if withdrawing pending_inactive stake
                pending_inactive -= amount
            };
            // escape excess stake from inactivation
            stake::reactivate_stake(stake_pool_owner, pending_inactive);
            stake::withdraw(stake_pool_owner, amount);
            // restore excess stake to the pending_inactive state
            stake::unlock(stake_pool_owner, pending_inactive);
        } else {
            // no excess stake if `stake::withdraw` does not inactivate at all
            stake::withdraw(stake_pool_owner, amount);
        };
        aptos_account::transfer(stake_pool_owner, delegator_address, amount);

        // commit withdrawal of possibly inactive stake to the `total_coins_inactive`
        // known by the delegation pool in order to not mistake it for slashing at next synchronization
        let (_, inactive, _, _) = stake::get_stake(pool_address);
        pool.total_coins_inactive = inactive;
```
