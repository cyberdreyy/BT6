[1](#0-0)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L73-80)
```text
    struct StakingContract has store {
        // Recorded principal after the last commission distribution.
        // This is only used to calculate the commission the operator should be receiving.
        principal: u64,
        pool_address: address,
        // The stake pool's owner capability. This can be used to control funds in the stake pool.
        owner_cap: OwnerCapability,
        commission_percentage: u64,
```
