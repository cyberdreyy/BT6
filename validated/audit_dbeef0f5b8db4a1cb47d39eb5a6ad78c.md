[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L61-108)
```text
<li></li>
<ol>
    <li>A requests <code>unlock</code> for 100 stake</li>
    <li>Synchronization detects 200 - 100 active rewards which are entirely (0% commission) added to the active pool.</li>
    <li>100 coins = (100 * 100) / 200 = 50 shares are redeemed from the active pool and exchanged for 100 shares
into the pending_inactive one on A's behalf</li>
</ol>
<li>Delegator B adds 200 stake which is converted to (200 * 50) / 100 = 100 shares into the active pool</li>
<li>The stake pool earned rewards and now has 600 active and 200 pending_inactive stake.</li>
<li></li>
<ol>
    <li>A requests <code>reactivate_stake</code> for 100 stake</li>
    <li>
    Synchronization detects 600 - 300 active and 200 - 100 pending_inactive rewards which are both entirely
    distributed to their corresponding pools
    </li>
    <li>
    100 coins = (100 * 100) / 200 = 50 shares are redeemed from the pending_inactive pool and exchanged for
    (100 * 150) / 600 = 25 shares into the active one on A's behalf
    </li>
</ol>
<li>The lockup expires on the stake pool, inactivating the entire pending_inactive stake</li>
<li></li>
<ol>
    <li>B requests <code>unlock</code> for 100 stake</li>
    <li>
    Synchronization detects no active or pending_inactive rewards, but 0 -> 100 inactive stake on the stake pool,
    so it advances the observed lockup cycle and creates a pool_u64 for the new lockup, hence allowing previous
    pending_inactive shares to be redeemed</li>
    <li>
    100 coins = (100 * 175) / 700 = 25 shares are redeemed from the active pool and exchanged for 100 shares
    into the new pending_inactive one on B's behalf
    </li>
</ol>
<li>The stake pool earned rewards and now has some pending_inactive rewards.</li>
<li></li>
<ol>
    <li>A requests <code>withdraw</code> for its entire inactive stake</li>
    <li>
    Synchronization detects no new inactive stake, but some pending_inactive rewards which are distributed
    to the (2nd) pending_inactive pool
    </li>
    <li>
    A's 50 shares = (50 * 100) / 50 = 100 coins are redeemed from the (1st) inactive pool and 100 stake is
    transferred to A
    </li>
</ol>
</ol>
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L110-131)
```text
module aptos_framework::delegation_pool {
    use std::error;
    use std::features;
    use std::signer;
    use std::vector;

    use aptos_std::math64;
    use aptos_std::pool_u64_unbound as pool_u64;
    use aptos_std::table::{Self, Table};
    use aptos_std::smart_table::{Self, SmartTable};

    use aptos_framework::account;
    use aptos_framework::aptos_account;
    use aptos_framework::aptos_coin::AptosCoin;
    use aptos_framework::aptos_governance;
    use aptos_framework::coin;
    use aptos_framework::event::{Self, EventHandle, emit};
    use aptos_framework::stake;
    use aptos_framework::stake::get_operator;
    use aptos_framework::staking_config;
    use aptos_framework::timestamp;

```
