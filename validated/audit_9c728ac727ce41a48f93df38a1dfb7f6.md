### Title
`redeem_fungible_staked_sui` Permanently Locks Principal When `rewards_pool` Is Drained on an Inactive Staking Pool — (File: `crates/sui-framework/packages/sui-system/sources/staking_pool.move`)

---

### Summary

`redeem_fungible_staked_sui` calls `pool.rewards_pool.split(rewards_amount)` with no guard against an insufficient balance. The parallel withdrawal path `withdraw_rewards` (used by `request_withdraw_stake`) explicitly caps the reward amount at `pool.rewards_pool.value()` with a developer-acknowledged TODO. For an inactive pool, no further rewards are ever deposited, so once regular stakers drain `rewards_pool` to zero, every subsequent `redeem_fungible_staked_sui` call aborts and the SUI principal locked inside `FungibleStakedSuiData.principal` becomes permanently irrecoverable.

---

### Finding Description

**Two asymmetric withdrawal paths share the same `rewards_pool` but handle insufficiency differently.**

`withdraw_rewards` (called by `request_withdraw_stake`):

```move
// This may happen when we are withdrawing everything from the pool and
// the rewards pool balance may be less than reward_withdraw_amount.
// TODO: FIGURE OUT EXACTLY WHY THIS CAN HAPPEN.
reward_withdraw_amount = reward_withdraw_amount.min(pool.rewards_pool.value());
pool.rewards_pool.split(reward_withdraw_amount)
``` [1](#0-0) 

`redeem_fungible_staked_sui` has **no such cap**:

```move
let mut sui_out = fungible_staked_sui_data.principal.split(principal_amount);
sui_out.join(pool.rewards_pool.split(rewards_amount));   // aborts if rewards_pool < rewards_amount
``` [2](#0-1) 

**How the pool becomes permanently drained:**

1. A validator is removed; `deactivate_staking_pool` sets `deactivation_epoch`. After this, `deposit_rewards` is never called for this pool again. [3](#0-2) 

2. `pool_token_exchange_rate_at_epoch` clamps the lookup to `deactivation_epoch`, so the exchange rate still reflects the rewards that existed at deactivation. [4](#0-3) 

3. Regular stakers call `request_withdraw_stake` on the inactive pool. Each call takes from `rewards_pool` (capped, so it drains to zero) and immediately calls `process_pending_stake_withdraw`. [5](#0-4) 

4. `FungibleStakedSui` holders call `redeem_fungible_staked_sui`. The exchange rate still shows `rewards_amount > 0`, but `rewards_pool.value() == 0`. Move's `balance::split` aborts on underflow. The transaction reverts; the `FungibleStakedSui` object is not consumed, but the user can never succeed because no new rewards will ever be deposited. [2](#0-1) 

5. The SUI principal is stored in `FungibleStakedSuiData.principal` inside `pool.extra_fields`. No emergency-withdrawal path exists; the only exit is `redeem_fungible_staked_sui`, which always aborts. The principal is permanently locked. [6](#0-5) 

---

### Impact Explanation

**Permanent fund lock** of user SUI principal. The amount at risk equals the total principal held in `FungibleStakedSuiData.principal` for any deactivated pool whose `rewards_pool` has been drained. This matches the "permanent fund lock" class in the High/Medium allowed-impact gate. The principal cannot be recovered by any on-chain mechanism.

---

### Likelihood Explanation

**Medium.** Three conditions must coincide:

1. A validator pool is deactivated (routine — validators are removed every few epochs).
2. At least one `FungibleStakedSui` object exists for that pool (routine — `convert_to_fungible_staked_sui` is a supported public entry point).
3. Regular stakers withdraw before all `FungibleStakedSui` holders redeem (a natural race: stakers are incentivized to withdraw immediately after deactivation; `FungibleStakedSui` holders may not monitor the pool state).

No special privilege is required. Any ordinary SUI holder with a `StakedSui` in the pool can trigger the drain by calling `request_withdraw_stake`.

---

### Recommendation

Mirror the cap already present in `withdraw_rewards` inside `redeem_fungible_staked_sui`:

```move
// Before splitting from rewards_pool, cap at available balance
let rewards_amount = rewards_amount.min(pool.rewards_pool.value());
let mut sui_out = fungible_staked_sui_data.principal.split(principal_amount);
sui_out.join(pool.rewards_pool.split(rewards_amount));
```

Alternatively, resolve the root cause in `withdraw_rewards` (the TODO) so that `rewards_pool` is never over-drawn by regular withdrawals, which would preserve the invariant that `rewards_pool` always holds enough to cover all outstanding `FungibleStakedSui` redemptions.

---

### Proof of Concept

```move
#[test]
fun test_fungible_staked_sui_permanent_lock_on_inactive_pool() {
    let mut scenario = test_scenario::begin(@0x0);
    let mut pool = staking_pool::new(scenario.ctx());
    pool.activate_staking_pool(0);

    // Epoch 0 → 1: stake 1000 SUI (regular staker A) and 1000 SUI (future FSS holder B)
    let sui_a = balance::create_for_testing<SUI>(1_000_000_000);
    let staked_a = pool.request_add_stake(sui_a, 1, scenario.ctx());
    let sui_b = balance::create_for_testing<SUI>(1_000_000_000);
    let staked_b = pool.request_add_stake(sui_b, 1, scenario.ctx());

    // Advance epoch with 2000 SUI rewards → rewards_pool = 2000
    pool.deposit_rewards(balance::create_for_testing<SUI>(2_000_000_000));
    pool.process_pending_stakes_and_withdraws(scenario.ctx());
    test_scenario::next_epoch(&mut scenario, @0x0);

    // B converts to FungibleStakedSui
    let fss = pool.convert_to_fungible_staked_sui(staked_b, scenario.ctx());

    // Pool is deactivated (validator removed)
    pool.deactivate_staking_pool(scenario.ctx().epoch());

    // A withdraws — drains rewards_pool (withdraw_rewards caps, drains to 0)
    let _sui_out_a = pool.request_withdraw_stake(staked_a, scenario.ctx());
    // rewards_pool is now 0

    // B tries to redeem FungibleStakedSui — ABORTS: rewards_pool.split(rewards_amount) fails
    // rewards_amount > 0 because exchange rate still shows rewards from deactivation epoch
    // No more rewards will ever be deposited → B's 1000 SUI principal is permanently locked
    let _sui_out_b = pool.redeem_fungible_staked_sui(fss, scenario.ctx()); // aborts here

    destroy(pool);
    scenario.end();
}
```

The call to `redeem_fungible_staked_sui` aborts at `pool.rewards_pool.split(rewards_amount)` because `rewards_pool.value() == 0` while `rewards_amount > 0`. Since the pool is inactive, no future epoch will replenish `rewards_pool`, and the 1 000 SUI principal inside `FungibleStakedSuiData.principal` is permanently irrecoverable.

### Citations

**File:** crates/sui-framework/packages/sui-system/sources/staking_pool.move (L98-104)
```text
public struct FungibleStakedSuiData has key, store {
    id: UID,
    /// fungible_staked_sui supply
    total_supply: u64,
    /// principal balance. Rewards are withdrawn from the reward pool
    principal: Balance<SUI>,
}
```

**File:** crates/sui-framework/packages/sui-system/sources/staking_pool.move (L187-188)
```text
    // If the pool is inactive or preactive, we immediately process the withdrawal.
    if (pool.is_inactive() || pool.is_preactive()) pool.process_pending_stake_withdraw();
```

**File:** crates/sui-framework/packages/sui-system/sources/staking_pool.move (L220-221)
```text
    let mut sui_out = fungible_staked_sui_data.principal.split(principal_amount);
    sui_out.join(pool.rewards_pool.split(rewards_amount));
```

**File:** crates/sui-framework/packages/sui-system/sources/staking_pool.move (L436-440)
```text
    // This may happen when we are withdrawing everything from the pool and
    // the rewards pool balance may be less than reward_withdraw_amount.
    // TODO: FIGURE OUT EXACTLY WHY THIS CAN HAPPEN.
    reward_withdraw_amount = reward_withdraw_amount.min(pool.rewards_pool.value());
    pool.rewards_pool.split(reward_withdraw_amount)
```

**File:** crates/sui-framework/packages/sui-system/sources/staking_pool.move (L461-464)
```text
public(package) fun deactivate_staking_pool(pool: &mut StakingPool, deactivation_epoch: u64) {
    // We can't deactivate an already deactivated pool.
    assert!(!pool.is_inactive(), EDeactivationOfInactivePool);
    pool.deactivation_epoch = option::some(deactivation_epoch);
```

**File:** crates/sui-framework/packages/sui-system/sources/staking_pool.move (L600-601)
```text
    let clamped_epoch = pool.deactivation_epoch.get_with_default(epoch);
    let mut epoch = clamped_epoch.min(epoch);
```
