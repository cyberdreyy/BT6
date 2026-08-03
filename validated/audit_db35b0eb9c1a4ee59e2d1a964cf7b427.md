[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L41-49)
```text
 - unlocking and/or unlocked stake originating from different real lockups are never mixed together into
the same pool_u64. This invalidates the accounting of which rewards belong to whom.
 - no delegator can have unlocking and/or unlocked stake (pending withdrawals) in different OLCs. This ensures
delegators do not have to keep track of the OLCs when they unlocked. When creating a new pending withdrawal,
the existing one is executed (withdrawn) if is already inactive.
 - <code>add_stake</code> fees are always refunded, but only after the epoch when they have been charged ends.
 - withdrawing pending_inactive stake (when validator had gone inactive before its lockup expired)
does not inactivate any stake additional to the requested one to ensure OLC would not advance indefinitely.
 - the pending withdrawal exists at an OLC iff delegator owns some shares within the shares pool of that OLC.
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L110-120)
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

```
