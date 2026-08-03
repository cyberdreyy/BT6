No vulnerability found for this question.

**Analysis:**

The proposed attack path doesn't work because Aptos already handles this exact rounding scenario deterministically, not via a bypassable post-hoc assert.

In `unlock_internal`, the amount to redeem is **pre-adjusted** by `coins_to_transfer_to_ensure_min_stake` *before* any shares are redeemed [1](#0-0) . This helper computes what the destination (`pending_inactive`) and source (`active`) balances would become after the requested transfer, and if either would land strictly between `0` and `MIN_COINS_ON_SHARES_POOL`, it forcibly rounds the transferred amount up (destination) or down to zero (source) so the resulting balance is always either `>= MIN_COINS_ON_SHARES_POOL` or exactly `0` [2](#0-1) , relying on `coins_to_redeem_to_ensure_min_stake` which redeems the entire balance if leftover would be under the threshold [3](#0-2) .

Because of this pre-adjustment, `unlock` never actually leaves a "dust" active balance strictly below `MIN_COINS_ON_SHARES_POOL`; there is no `assert_min_active_balance` call inside `unlock_internal` to bypass at all — the invariant is enforced by construction, not by a post-transfer check that a rounding trick could dodge. `assert_min_active_balance`/`assert_min_pending_inactive_balance` are only invoked after `add_stake` and `reactivate_stake`, where the same pre-adjustment logic applies before the assertion is reached [4](#0-3) .

This exact invariant — "the delegator's active or pending inactive stake will always meet or exceed the minimum allowed value" — is documented and stated as audited in the module's high-level spec requirements [5](#0-4) . Existing unit tests exercise precisely the "unlock amount at MIN_COINS_ON_SHARES_POOL - 1" boundary across repeated unlock/reactivate cycles and confirm the balance snaps to either the threshold or zero, never landing in the disallowed dust range [6](#0-5) .

Additionally, the cited source file for this finding (`types/src/account_config/events/token_deposit_event.rs`) has no relation to `delegation_pool.move` or `pool_u64` share accounting — this appears to be a mismatched file attribution in the automated finding, which further indicates the report is not grounded in the actual vulnerable code path.

Per the Review Path step 4 ("Reject if role checks and accounting invariants already block the path"), this finding is rejected: the accounting invariant is enforced pre-transfer for `unlock`/`reactivate_stake`, preventing the rounding-based bypass described in the question.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1068-1081)
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
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1083-1099)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1501-1505)
```text
        stake::add_stake(&retrieve_stake_pool_owner(pool), amount);

        // but buy shares for delegator just for the remaining amount after fee
        buy_in_active_shares(pool, delegator_address, amount - add_stake_fee);
        assert_min_active_balance(pool, delegator_address);
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1540-1558)
```text
    fun unlock_internal(
        delegator_address: address,
        pool_address: address,
        amount: u64
    ) acquires DelegationPool, GovernanceRecords {
        assert!(delegator_address != NULL_SHAREHOLDER, error::invalid_argument(ECANNOT_UNLOCK_NULL_SHAREHOLDER));

        // fail unlock of more stake than `active` on the stake pool
        let (active, _, _, _) = stake::get_stake(pool_address);
        assert!(amount <= active, error::invalid_argument(ENOT_ENOUGH_ACTIVE_STAKE_TO_UNLOCK));

        let pool = borrow_global_mut<DelegationPool>(pool_address);
        amount = coins_to_transfer_to_ensure_min_stake(
            &pool.active_shares,
            pending_inactive_shares_pool(pool),
            delegator_address,
            amount,
        );
        amount = redeem_active_shares(pool, delegator_address, amount);
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L3936-3959)
```text
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
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.spec.move (L65-73)
```text
    /// No.: 9
    /// Requirement: The delegator's active or pending inactive stake will always meet or exceed the minimum allowed
    /// value.
    /// Criticality: Medium
    /// Implementation: The add_stake, unlock and reactivate_stake functions ensure the active_shares or
    /// pending_inactive_shares balance for the delegator is greater than or equal to the MIN_COINS_ON_SHARES_POOL
    /// value.
    /// Enforcement: Audited the comparison of active_shares or inactive_shares balance for the delegator with the
    /// MIN_COINS_ON_SHARES_POOL value.
```
