[1](#0-0)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L121-141)
```text
        old_commission_percentage: u64,
        new_commission_percentage: u64
    }

    #[
        resource_group_member(
            group = aptos_framework::staking_contract::StakingGroupContainer
        )
    ]
    struct StakingGroupUpdateCommissionEvent has key {
        update_commission_events: EventHandle<UpdateCommissionEvent>
    }

    #[event]
    struct CreateStakingContract has drop, store {
        operator: address,
        voter: address,
        pool_address: address,
        principal: u64,
        commission_percentage: u64
    }
```
