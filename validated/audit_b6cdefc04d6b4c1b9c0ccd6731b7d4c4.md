### Title
`redeem_fungible_staked_sui` Aborts on Insufficient `rewards_pool` Due to Missing `.min()` Guard — (`File: crates/sui-framework/packages/sui-system/sources/staking_pool.move`)

---

### Summary

`redeem_fungible_staked_sui` in `staking_pool.move` computes a `rewards_amount` from the epoch's exchange rate (a theoretical value derived from `sui_balance`) and then calls `pool.rewards_pool.split(rewards_amount)` without a `.min(pool.rewards_pool.value())` guard. The sibling function `withdraw_rewards` has an explicit guard for exactly this case, even noting with a `TODO` comment that the pool balance can fall below the calculated reward. When regular stakers drain `rewards_pool` within the same epoch via `request_withdraw_stake`, a subsequent `FungibleStakedSui` redemption will abort, permanently locking the user's stake until pool conditions change.

---

### Finding Description

**Root cause — monotonicity assumption violation:**

The exchange rate stored at the start of each epoch is computed from `pool.sui_balance`, which includes the rewards portion. During the epoch, `request_withdraw_stake` calls `withdraw_rewards`, which splits SUI out of `pool.rewards_pool` but does **not** immediately reduce `pool.sui_balance` (that reduction is deferred to the epoch boundary via `pending_total_sui_withdraw`). As a result, the exchange rate at the current epoch can imply more rewards than are actually present in `pool.rewards_pool`.

`withdraw_rewards` handles this with an explicit guard:

```move
// This may happen when we are withdrawing everything from the pool and
// the rewards pool balance may be less than reward_withdraw_amount.
// TODO: FIGURE OUT EXACTLY WHY THIS CAN HAPPEN.
reward_withdraw_amount = reward_withdraw_amount.min(pool.rewards_pool.value());
pool.rewards_pool.split(reward_withdraw_amount)
``` [1](#0-0) 

`redeem_fungible_staked_sui` has no such guard:

```move
let mut sui_out = fungible_staked_sui_data.principal.split(principal_amount);
sui_out.join(pool.rewards_pool.split(rewards_amount));   // ← no .min() guard
``` [2](#0-1) 

**Divergence mechanism:**

`deposit_rewards` increments both `sui_balance` and `rewards_pool`: [3](#0-2) 

But `withdraw_rewards` only decrements `rewards_pool`; `sui_balance` is not reduced until `process_pending_stake_withdraw` runs at the epoch boundary: [4](#0-3) 

So within an epoch, after regular stakers call `request_withdraw_stake`, `rewards_pool.value()` falls while the epoch's exchange rate (based on the unchanged `sui_balance`) stays high. `calculate_fungible_staked_sui_withdraw_amount` uses that stale-high exchange rate to compute `rewards_amount`:

```move
let total_sui_amount = latest_exchange_rate.get_sui_amount(
    fungible_staked_sui_data_total_supply,
);
...
let rewards_withdraw_amount = mul_div!(
    fungible_staked_sui_value,
    total_rewards,
    fungible_staked_sui_data_total_supply,
);
``` [5](#0-4) 

When `rewards_amount > pool.rewards_pool.value()`, the `split` call aborts the transaction.

---

### Impact Explanation

Any holder of a `FungibleStakedSui` object is unable to redeem it (call `redeem_fungible_staked_sui`) for the duration that `pool.rewards_pool.value() < rewards_amount`. Because Move aborts roll back all state changes, the user's `FungibleStakedSui` object is not destroyed — but the user is locked out of their funds until the pool's `rewards_pool` is replenished sufficiently. In a pool where regular stakers consistently drain rewards faster than new rewards are deposited, this lock can persist indefinitely, constituting a permanent fund lock. This matches the "permanent fund lock / harmful smart-contract behavior" impact class.

---

### Likelihood Explanation

The condition is reachable by any ordinary SUI holder:

1. Staker A converts `StakedSui` → `FungibleStakedSui` via `convert_to_fungible_staked_sui`.
2. Multiple regular stakers call `request_withdraw_stake` in the same epoch, draining `rewards_pool` via `withdraw_rewards` (each call is guarded by `.min()` so they succeed).
3. Staker A calls `redeem_fungible_staked_sui`; the exchange rate still reflects the pre-drain `sui_balance`, so `rewards_amount` exceeds the depleted `rewards_pool.value()`, and the transaction aborts.

No privileged access is required. The scenario is more likely in large, active pools with many concurrent unstakers.

---

### Recommendation

Apply the same `.min(pool.rewards_pool.value())` guard used in `withdraw_rewards` to the `rewards_amount` in `redeem_fungible_staked_sui`:

```move
// After calculate_fungible_staked_sui_withdraw_amount:
let rewards_amount = rewards_amount.min(pool.rewards_pool.value());
let mut sui_out = fungible_staked_sui_data.principal.split(principal_amount);
sui_out.join(pool.rewards_pool.split(rewards_amount));
```

This mirrors the fix implied by the external report: remove the implicit monotonicity assumption and use the live pool balance as the authoritative cap.

---

### Proof of Concept

```
Epoch N start:
  pool.sui_balance        = 1_000 SUI
  pool.rewards_pool       = 400 SUI   (rewards deposited this epoch)
  pool.pool_token_balance = 800 tokens
  exchange_rate[N]        = { sui: 1000, tokens: 800 }

  FungibleStakedSuiData:
    total_supply = 400 tokens
    principal    = 500 SUI

During epoch N:
  Regular staker calls request_withdraw_stake (200 tokens worth).
  withdraw_rewards computes reward = 250 SUI, clamped to min(250, 400) = 250.
  pool.rewards_pool = 400 - 250 = 150 SUI   ← drained
  pool.sui_balance  = 1_000 SUI              ← unchanged until epoch boundary

FungibleStakedSui holder calls redeem_fungible_staked_sui (value = 400 tokens):
  latest_exchange_rate = exchange_rate[N] = { sui: 1000, tokens: 800 }
  total_sui_amount = get_sui_amount(400) = 1000 * 400 / 800 = 500 SUI
  principal_clamped = min(500, 500) = 500
  total_rewards = 500 - 500 = 0 SUI   ← in this example rewards_amount = 0

  (Adjust example: principal = 200 SUI, total_supply = 400 tokens)
  total_sui_amount = 500 SUI
  principal_clamped = min(200, 500) = 200
  total_rewards = 500 - 200 = 300 SUI
  rewards_amount = 400 * 300 / 400 = 300 SUI

  pool.rewards_pool.split(300)  →  ABORT: 300 > 150 (pool.rewards_pool.value())
```

The transaction aborts. The `FungibleStakedSui` holder cannot redeem their stake. [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** crates/sui-framework/packages/sui-system/sources/staking_pool.move (L231-271)
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
}
```

**File:** crates/sui-framework/packages/sui-system/sources/staking_pool.move (L350-353)
```text
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
