[1](#0-0)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L91-104)
```text
    struct Store has key {
        staking_contracts: SimpleMap<address, StakingContract>,

        // Events.
        create_staking_contract_events: EventHandle<CreateStakingContractEvent>,
        update_voter_events: EventHandle<UpdateVoterEvent>,
        reset_lockup_events: EventHandle<ResetLockupEvent>,
        add_stake_events: EventHandle<AddStakeEvent>,
        request_commission_events: EventHandle<RequestCommissionEvent>,
        unlock_stake_events: EventHandle<UnlockStakeEvent>,
        switch_operator_events: EventHandle<SwitchOperatorEvent>,
        add_distribution_events: EventHandle<AddDistributionEvent>,
        distribute_events: EventHandle<DistributeEvent>
    }
```
