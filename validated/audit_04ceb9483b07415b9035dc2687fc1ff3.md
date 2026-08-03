[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L410-429)
```text
    /// Staker can call this function to create a simple staking contract with a specified operator.
    public entry fun create_staking_contract(
        staker: &signer,
        operator: address,
        voter: address,
        amount: u64,
        commission_percentage: u64,
        // Optional seed used when creating the staking contract account.
        contract_creation_seed: vector<u8>
    ) acquires Store {
        let staked_coins = coin::withdraw<AptosCoin>(staker, amount);
        create_staking_contract_with_coins(
            staker,
            operator,
            voter,
            staked_coins,
            commission_percentage,
            contract_creation_seed
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L486-500)
```text
        let pool_address = signer::address_of(&stake_pool_signer);
        staking_contracts.add(
            operator,
            StakingContract {
                principal,
                pool_address,
                owner_cap,
                commission_percentage,
                // Make sure we don't have too many pending recipients in the distribution pool.
                // Otherwise, a griefing attack is possible where the staker can keep switching operators and create too
                // many pending distributions. This can lead to out-of-gas failure whenever distribute() is called.
                distribution_pool: pool_u64::create(MAXIMUM_PENDING_DISTRIBUTIONS),
                signer_cap: stake_pool_signer_cap
            }
        );
```
