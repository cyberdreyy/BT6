No vulnerability found for this question.

**Analysis:**

The rounding behavior described is a known, intentional, and already-tested property of the `delegation_pool` shares accounting — not an exploitable vulnerability.

`coins_to_transfer_to_ensure_min_stake` and `coins_to_redeem_to_ensure_min_stake` only adjust the `amount` parameter to keep balances above `MIN_COINS_ON_SHARES_POOL` on either side of a transfer between `active_shares` and `pending_inactive_shares_pool`, they do not redirect value to a third party or create net gains for anyone [1](#0-0) .

The underlying `pool_u64::shares_to_amount`/`amount_to_shares` conversions always use floor division (`multiply_then_divide` → `math64::mul_div`), so every redeem/buy-in step can lose at most a fraction of a coin (bounded to ~1 unit per operation) [2](#0-1) . This loss is not "unrecoverable" in the sense of being stolen: it simply stays in the pool's `total_coins` accounting and is effectively redistributed pro-rata to remaining shareholders (including the delegator on future rewards synchronization) — it does not change who can withdraw, unlock, reactivate, or earn stake.

This exact `reactivate_stake`/`unlock` cycling behavior, including the exact 1-coin rounding losses per cycle, is explicitly covered by the existing unit test `test_min_stake_is_preserved`, which walks through many small unlock/reactivate cycles and asserts the exact (documented) rounding losses at each step [3](#0-2) . Additional comments in other tests (e.g. lines 2524-2552) explicitly document and assert this same "N coins lost at redeem due to shares being burned" behavior as expected [4](#0-3) .

Since:
1. The loss is bounded to ~1 unit per operation (dust-level, matching the exact "1 unit per cycle" tolerance mentioned in the question's own proof idea),
2. The loss never leaves the pool or benefits an attacker — it stays as pool `total_coins` and is not "permanently unrecoverable" for the pool as a whole,
3. This exact scenario is already covered by production unit tests as expected behavior,

this does not meet the Decision Standard's bar of changing who can withdraw/unlock/reactivate/earn value or of stranding value in a way that isn't already accounted for and tested.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1068-1099)
```text
    fun coins_to_redeem_to_ensure_min_stake(
        src_shares_pool: &pool_u64::Pool,
        shareholder: address,
        amount: u64,
    ): u64 {
        // find how many coins would be redeemed if supplying `amount`
        let redeemed_coins = src_shares_pool.shares_to_amount(amount_to_shares_to_redeem(src_shares_pool, shareholder, amount));
        // if balance drops under threshold then redeem it entirely
        let src_balance = src_shares_pool.balance(shareholder);
        if (src_balance - redeemed_coins < MIN_COINS_ON_SHARES_POOL) {
            amount = src_balance;
        };
        amount
    }

    fun coins_to_transfer_to_ensure_min_stake(
        src_shares_pool: &pool_u64::Pool,
        dst_shares_pool: &pool_u64::Pool,
        shareholder: address,
        amount: u64,
    ): u64 {
        // find how many coins would be redeemed from source if supplying `amount`
        let redeemed_coins = src_shares_pool.shares_to_amount(amount_to_shares_to_redeem(src_shares_pool, shareholder, amount));
        // if balance on destination would be less than threshold then redeem difference to threshold
        let dst_balance = dst_shares_pool.balance(shareholder);
        if (dst_balance + redeemed_coins < MIN_COINS_ON_SHARES_POOL) {
            // `redeemed_coins` >= `amount` - 1 as redeem can lose at most 1 coin
            amount = MIN_COINS_ON_SHARES_POOL - dst_balance + 1;
        };
        // check if new `amount` drops balance on source under threshold and adjust
        coins_to_redeem_to_ensure_min_stake(src_shares_pool, shareholder, amount)
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L2521-2553)
```text
        assert_delegation(delegator_address, pool_address, 1009999999, 0, 0);
        assert_pending_withdrawal(delegator_address, pool_address, false, 0, false, 0);

        unlock_with_min_stake_disabled(delegator, pool_address, 2);
        // request 2 coins * 909.99 / 919.1 = 1.98 shares to redeem * 1.01 price -> 1 coins out
        // with 1 coins buy 1 * 100 / 101 = 0.99 shares in pending_inactive pool * 1.01 -> 0 coins in
        // 1 coins lost at redeem due to 1.98 - 1.01 shares being burned + 1 coins extracted
        synchronize_delegation_pool(pool_address);
        assert_delegation(delegator_address, pool_address, 1009999997, 0, 0);
        // the pending withdrawal has been created as > 0 pending_inactive shares have been bought
        assert_pending_withdrawal(delegator_address, pool_address, true, 0, false, 0);

        // successfully delete the pending withdrawal (redeem all owned shares even worth 0 coins)
        reactivate_stake(delegator, pool_address, 1);
        assert_delegation(delegator_address, pool_address, 1009999997, 0, 0);
        assert_pending_withdrawal(delegator_address, pool_address, false, 0, false, 0);

        // unlock min coins to own some pending_inactive balance (have to disable min-balance checks)
        unlock_with_min_stake_disabled(delegator, pool_address, 3);
        // request 3 coins * 909.99 / 919.09 = 2.97 shares to redeem * 1.01 price -> 2 coins out
        // with 2 coins buy 2 * 100 / 101 = 1.98 shares in pending_inactive pool * 1.01 -> 1 coins in
        // 1 coins lost at redeem due to 2.97 - 2 * 1.01 shares being burned + 2 coins extracted
        synchronize_delegation_pool(pool_address);
        assert_delegation(delegator_address, pool_address, 1009999994, 0, 1);
        // the pending withdrawal has been created as > 0 pending_inactive shares have been bought
        assert_pending_withdrawal(delegator_address, pool_address, true, 0, false, 1);

        reactivate_stake(delegator, pool_address, 1);
        // redeem 1 coins >= delegator balance -> all shares are redeemed and pending withdrawal is deleted
        assert_delegation(delegator_address, pool_address, 1009999995, 0, 0);
        // the pending withdrawal has been deleted as delegator has 0 pending_inactive shares now
        assert_pending_withdrawal(delegator_address, pool_address, false, 0, false, 0);
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L3908-4012)
```text
    #[test(aptos_framework = @aptos_framework, validator = @0x123, delegator1 = @0x010, delegator2 = @0x020)]
    public entry fun test_min_stake_is_preserved(
        aptos_framework: &signer,
        validator: &signer,
        delegator1: &signer,
        delegator2: &signer,
    ) acquires DelegationPoolOwnership, DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage, DelegationPoolAllowlisting {
        initialize_for_test(aptos_framework);
        initialize_test_validator(validator, 100 * ONE_APT, true, false);

        let validator_address = signer::address_of(validator);
        let pool_address = get_owned_pool_address(validator_address);

        let delegator1_address = signer::address_of(delegator1);
        account::create_account_for_test(delegator1_address);

        let delegator2_address = signer::address_of(delegator2);
        account::create_account_for_test(delegator2_address);

        // add stake without fees as validator is not active yet
        stake::mint(delegator1, 50 * ONE_APT);
        add_stake(delegator1, pool_address, 50 * ONE_APT);
        stake::mint(delegator2, 16 * ONE_APT);
        add_stake(delegator2, pool_address, 16 * ONE_APT);

        // validator becomes active and share price is 1
        end_aptos_epoch();

        assert_delegation(delegator1_address, pool_address, 5000000000, 0, 0);
        // pending_inactive balance would be under threshold => move MIN_COINS_ON_SHARES_POOL coins
        unlock(delegator1, pool_address, MIN_COINS_ON_SHARES_POOL - 1);
        assert_delegation(delegator1_address, pool_address, 3999999999, 0, 1000000001);

        // pending_inactive balance is over threshold
        reactivate_stake(delegator1, pool_address, 1);
        assert_delegation(delegator1_address, pool_address, 4000000000, 0, 1000000000);

        // pending_inactive balance would be under threshold => move entire balance
        reactivate_stake(delegator1, pool_address, 1);
        assert_delegation(delegator1_address, pool_address, 5000000000, 0, 0);

        // active balance would be under threshold => move entire balance
        unlock(delegator1, pool_address, 5000000000 - (MIN_COINS_ON_SHARES_POOL - 1));
        assert_delegation(delegator1_address, pool_address, 0, 0, 5000000000);

        // active balance would be under threshold => move MIN_COINS_ON_SHARES_POOL coins
        reactivate_stake(delegator1, pool_address, 1);
        assert_delegation(delegator1_address, pool_address, 1000000001, 0, 3999999999);

        // active balance is over threshold
        unlock(delegator1, pool_address, 1);
        assert_delegation(delegator1_address, pool_address, 1000000000, 0, 4000000000);

        // pending_inactive balance would be under threshold => move entire balance
        reactivate_stake(delegator1, pool_address, 4000000000 - (MIN_COINS_ON_SHARES_POOL - 1));
        assert_delegation(delegator1_address, pool_address, 5000000000, 0, 0);

        // active + pending_inactive balance < 2 * MIN_COINS_ON_SHARES_POOL
        // stake can live on only one of the shares pools
        assert_delegation(delegator2_address, pool_address, 16 * ONE_APT, 0, 0);
        unlock(delegator2, pool_address, 1);
        assert_delegation(delegator2_address, pool_address, 0, 0, 16 * ONE_APT);
        reactivate_stake(delegator2, pool_address, 1);
        assert_delegation(delegator2_address, pool_address, 16 * ONE_APT, 0, 0);

        unlock(delegator2, pool_address, ONE_APT);
        assert_delegation(delegator2_address, pool_address, 0, 0, 16 * ONE_APT);
        reactivate_stake(delegator2, pool_address, 2 * ONE_APT);
        assert_delegation(delegator2_address, pool_address, 16 * ONE_APT, 0, 0);

        // share price becomes 1.01 on both pools
        unlock(delegator1, pool_address, 1);
        assert_delegation(delegator1_address, pool_address, 3999999999, 0, 1000000001);
        end_aptos_epoch();
        assert_delegation(delegator1_address, pool_address, 4039999998, 0, 1010000001);

        // pending_inactive balance is over threshold
        reactivate_stake(delegator1, pool_address, 10000001);
        assert_delegation(delegator1_address, pool_address, 4049999998, 0, 1000000001);

        // 1 coin < 1.01 so no shares are redeemed
        reactivate_stake(delegator1, pool_address, 1);
        assert_delegation(delegator1_address, pool_address, 4049999998, 0, 1000000001);

        // pending_inactive balance is over threshold
        // requesting 2 coins actually redeems 1 coin from pending_inactive pool
        reactivate_stake(delegator1, pool_address, 2);
        assert_delegation(delegator1_address, pool_address, 4049999999, 0, 1000000000);

        // 1 coin < 1.01 so no shares are redeemed
        reactivate_stake(delegator1, pool_address, 1);
        assert_delegation(delegator1_address, pool_address, 4049999999, 0, 1000000000);

        // pending_inactive balance would be under threshold => move entire balance
        reactivate_stake(delegator1, pool_address, 2);
        assert_delegation(delegator1_address, pool_address, 5049999999, 0, 0);

        // pending_inactive balance would be under threshold => move MIN_COINS_ON_SHARES_POOL coins
        unlock(delegator1, pool_address, MIN_COINS_ON_SHARES_POOL - 1);
        assert_delegation(delegator1_address, pool_address, 4049999998, 0, 1000000000);

        // pending_inactive balance would be under threshold => move entire balance
        reactivate_stake(delegator1, pool_address, 1);
        assert_delegation(delegator1_address, pool_address, 5049999998, 0, 0);
    }
```

**File:** aptos-move/framework/aptos-stdlib/sources/pool_u64.move (L242-264)
```text
    /// Return the number of coins `shares` are worth in `self`.
    /// `shares` needs to big enough to avoid rounding number.
    public fun shares_to_amount(self: &Pool, shares: u64): u64 {
        self.shares_to_amount_with_total_coins(shares, self.total_coins)
    }

    /// Return the number of coins `shares` are worth in `self` with a custom total coins number.
    /// `shares` needs to big enough to avoid rounding number.
    public fun shares_to_amount_with_total_coins(self: &Pool, shares: u64, total_coins: u64): u64 {
        // No shares or coins yet so shares are worthless.
        if (self.total_coins == 0 || self.total_shares == 0) {
            0
        } else {
            // Shares price = total_coins / total existing shares.
            // Shares worth = shares * shares price = shares * total_coins / total existing shares.
            // We rearrange the calc and do multiplication first to avoid rounding errors.
            self.multiply_then_divide(shares, total_coins, self.total_shares)
        }
    }

    public fun multiply_then_divide(self: &Pool, x: u64, y: u64, z: u64): u64 {
        math64::mul_div(x, y, z)
    }
```
