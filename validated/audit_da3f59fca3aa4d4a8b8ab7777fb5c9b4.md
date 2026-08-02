[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L186-186)
```text
    const EALREADY_VOTED_BEFORE_ENABLE_PARTIAL_VOTING: u64 = 17;
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L899-907)
```text
    #[view]
    /// Return the beneficiary address of the operator.
    public fun beneficiary_for_operator(operator: address): address acquires BeneficiaryForOperator {
        if (exists<BeneficiaryForOperator>(operator)) {
            return borrow_global<BeneficiaryForOperator>(operator).beneficiary_for_operator
        } else {
            operator
        }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L909-933)
```text
    /// Enable partial governance voting on a stake pool. The voter of this stake pool will be managed by this module.
    /// The existing voter will be replaced. The function is permissionless.
    public entry fun enable_partial_governance_voting(
        pool_address: address,
    ) acquires DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage {
        assert_delegation_pool_exists(pool_address);
        // synchronize delegation and stake pools before any user operation.
        synchronize_delegation_pool(pool_address);

        let delegation_pool = borrow_global<DelegationPool>(pool_address);
        let stake_pool_signer = retrieve_stake_pool_owner(delegation_pool);
        // delegated_voter is managed by the stake pool itself, which signer capability is managed by DelegationPool.
        // So voting power of this stake pool can only be used through this module.
        stake::set_delegated_voter(&stake_pool_signer, signer::address_of(&stake_pool_signer));

        move_to(&stake_pool_signer, GovernanceRecords {
            votes: smart_table::new(),
            votes_per_proposal: smart_table::new(),
            vote_delegation: smart_table::new(),
            delegated_votes: smart_table::new(),
            vote_events: account::new_event_handle<VoteEvent>(&stake_pool_signer),
            create_proposal_events: account::new_event_handle<CreateProposalEvent>(&stake_pool_signer),
            delegate_voting_power_events: account::new_event_handle<DelegateVotingPowerEvent>(&stake_pool_signer),
        });
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L960-965)
```text
        aptos_governance::assert_proposal_expiration(pool_address, proposal_id);
        assert!(voting_power > 0, error::invalid_argument(ENO_VOTING_POWER));

        let governance_records = borrow_global_mut<GovernanceRecords>(pool_address);
        // Check a edge case during the transient period of enabling partial governance voting.
        assert_and_update_proposal_used_voting_power(governance_records, pool_address, proposal_id, voting_power);
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1033-1036)
```text
    fun assert_min_active_balance(pool: &DelegationPool, delegator_address: address) {
        let balance = pool.active_shares.balance(delegator_address);
        assert!(balance >= MIN_COINS_ON_SHARES_POOL, error::invalid_argument(EDELEGATOR_ACTIVE_BALANCE_TOO_LOW));
    }
```
