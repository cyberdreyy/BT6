[1](#0-0)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L23-34)
```text
As stake-state transitions and rewards are computed only at the stake pool level, the delegation pool
gets outdated. To mitigate this, at any interaction with the delegation pool, a process of synchronization
to the underlying stake pool is executed before the requested operation itself.

At synchronization:
 - stake deviations between the two pools are actually the rewards produced in the meantime.
 - the commission fee is extracted from the rewards, the remaining stake is distributed to the internal
pool_u64 pools and then the commission stake used to buy shares for operator.
 - if detecting that the lockup expired on the stake pool, the delegation pool will isolate its
pending_inactive stake (now inactive) and create a new pool_u64 to host future pending_inactive stake
scheduled this newly started lockup.
Detecting a lockup expiration on the stake pool resumes to detecting new inactive stake.
```
