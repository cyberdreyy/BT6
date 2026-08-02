## Analysis

I reviewed `delegation_pool::reactivate_stake`, `unlock`, `synchronize_delegation_pool`, and `calculate_stake_pool_drift` in `aptos-move/framework/aptos-framework/sources/delegation_pool.move`, plus the underlying `stake::reactivate_stake_with_cap` in `stake.move`.

**Why the described race does not exist:**

1. **Move transactions execute atomically and serially.** There is no mid-transaction "interleaving" between two delegators' calls. Each transaction (whether `unlock`, `reactivate_stake`, `withdraw`, or `add_stake`) runs to completion before the next one begins, so there is no window where one delegator's `reactivate_stake` executes concurrently with an in-flight commission calculation from another delegator's action.

2. **Every state-changing entrypoint synchronizes first.** Both `reactivate_stake` (line 1587) and `unlock` (line 1534) call `synchronize_delegation_pool(pool_address)` as their very first step, which invokes `calculate_stake_pool_drift` to compute and pay commission before any of that transaction's own share movement occurs. [1](#0-0) 

3. **Commission is computed as a checkpoint delta, not a snapshot at "epoch end."** `calculate_stake_pool_drift` computes `commission_pending_inactive` as the difference between the real on-chain `pending_inactive` balance (from `stake::get_stake`) and `pending_inactive_shares_pool(pool).total_coins()`, which was last updated at the previous synchronization point. [2](#0-1) 

4. **`redeem_inactive_shares` keeps internal accounting in lockstep with the real stake pool.** When a delegator calls `reactivate_stake`, `redeem_inactive_shares` reduces the pending_inactive shares pool's `total_coins` by exactly the amount moved, and `stake::reactivate_stake` reduces the actual on-chain `pending_inactive` balance by the same amount in the same transaction.
<invoke name="read_file">
<parameter name="repo_name">EzraCole/aptos-core--013</parameter>
<parameter name="file_path">aptos-move/framework/aptos-framework/sources/delegation_pool.move</parameter>
<parameter name="start_line">1590</parameter>
<parameter name="end_line">1600</parameter>
</invoke>

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1586-1599)
```text
        // synchronize delegation and stake pools before any user operation
        synchronize_delegation_pool(pool_address);

        let pool = borrow_global_mut<DelegationPool>(pool_address);
        amount = coins_to_transfer_to_ensure_min_stake(
            pending_inactive_shares_pool(pool),
            &pool.active_shares,
            delegator_address,
            amount,
        );
        let observed_lockup_cycle = pool.observed_lockup_cycle;
        amount = redeem_inactive_shares(pool, delegator_address, amount, observed_lockup_cycle);

        stake::reactivate_stake(&retrieve_stake_pool_owner(pool), amount);
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1899-1912)
```text
        // operator `pending_inactive` rewards not persisted yet to the pending_inactive shares pool
        let pool_pending_inactive = pending_inactive_shares_pool(pool).total_coins();
        let commission_pending_inactive = if (pending_inactive > pool_pending_inactive) {
            math64::mul_div(
                pending_inactive - pool_pending_inactive,
                pool.operator_commission_percentage,
                MAX_FEE
            )
        } else {
            // handle any slashing applied to `pending_inactive` stake
            0
        };

        (lockup_cycle_ended, active, pending_inactive, commission_active, commission_pending_inactive)
```
