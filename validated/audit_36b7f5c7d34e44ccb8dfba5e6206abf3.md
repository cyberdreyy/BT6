[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.spec.move (L328-341)
```text
    spec set_beneficiary_for_operator(operator: &signer, new_beneficiary: address) {
        // TODO: temporary mockup
        pragma verify = false;
    }

    spec beneficiary_for_operator(operator: address): address {
        // TODO: temporary mockup
        pragma verify = false;
    }

    spec pending_attribution_snapshot(staker: address, operator: address, account: address): u64 {
        pragma verify = false;
    }

```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.spec.move (L343-365)
```text
    spec distribute(staker: address, operator: address) {
        // TODO: Call `distribute_internal` and could not verify `update_distribution_pool`.
        pragma aborts_if_is_partial;

        include ContractExistsAbortsIf;
    }

    /// The StakePool exists under the pool_address of StakingContract.
    /// The value of inactive and pending_inactive in the stake_pool is up to MAX_U64.
    spec distribute_internal(
        staker: address,
        operator: address,
        staking_contract: &mut StakingContract,
    ) {
        // TODO: Call `update_distribution_pool` and could not verify `update_distribution_pool`.
        pragma aborts_if_is_partial;
        let pool_address = staking_contract.pool_address;
        let stake_pool = borrow_global<stake::StakePool>(pool_address);
        aborts_if !exists<stake::StakePool>(pool_address);
        aborts_if stake_pool.inactive.value + stake_pool.pending_inactive.value
            > MAX_U64;
        aborts_if !exists<stake::StakePool>(staking_contract.owner_cap.pool_address);
    }
```
