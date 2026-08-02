[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/account/account.move (L1140-1145)
```text
    public fun create_resource_address(source: &address, seed: vector<u8>): address {
        let bytes = bcs::to_bytes(source);
        bytes.append(seed);
        bytes.push_back(DERIVE_RESOURCE_ACCOUNT_SCHEME);
        from_bcs::to_address(hash::sha3_256(bytes))
    }
```

**File:** aptos-move/framework/aptos-framework/sources/account/account.move (L1156-1184)
```text
    public fun create_resource_account(source: &signer, seed: vector<u8>): (signer, SignerCapability) acquires Account {
        let resource_addr = create_resource_address(&signer::address_of(source), seed);
        let resource = if (exists_at(resource_addr)) {
            if (resource_exists_at(resource_addr)) {
            let account = &Account[resource_addr];
            assert!(
                account.signer_capability_offer.for.is_none(),
                error::already_exists(ERESOURCE_ACCCOUNT_EXISTS),
            );
            };
            assert!(
                get_sequence_number(resource_addr) == 0,
                error::invalid_state(EACCOUNT_ALREADY_USED),
            );
            create_signer(resource_addr)
        } else {
            create_account_unchecked(resource_addr)
        };

        // By default, only the SignerCapability should have control over the resource account and not the auth key.
        // If the source account wants direct control via auth key, they would need to explicitly rotate the auth key
        // of the resource account using the SignerCapability.
        rotate_authentication_key_internal(&resource, ZERO_AUTH_KEY);

        let account = &mut Account[resource_addr];
        account.signer_capability_offer.for = option::some(resource_addr);
        let signer_cap = SignerCapability { account: resource_addr };
        (resource, signer_cap)
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L977-998)
```text
    fun create_stake_pool(
        staker: &signer,
        operator: address,
        voter: address,
        contract_creation_seed: vector<u8>
    ): (signer, SignerCapability, OwnerCapability) {
        // Generate a seed that will be used to create the resource account that hosts the staking contract.
        let seed =
            create_resource_account_seed(
                signer::address_of(staker), operator, contract_creation_seed
            );

        let (stake_pool_signer, stake_pool_signer_cap) =
            account::create_resource_account(staker, seed);
        stake::initialize_stake_owner(&stake_pool_signer, 0, operator, voter);

        // Extract owner_cap from the StakePool, so we have control over it in the staking_contracts flow.
        // This is stored as part of the staking_contract. Thus, the staker would not have direct control over it without
        // going through well-defined functions in this module.
        let owner_cap = stake::extract_owner_cap(&stake_pool_signer);

        (stake_pool_signer, stake_pool_signer_cap, owner_cap)
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L867-872)
```text
        let (stake_pool_signer, stake_pool_signer_cap) = account::create_resource_account(owner, seed);
        coin::register<AptosCoin>(&stake_pool_signer);

        // stake_pool_signer will be owner of the stake pool and have its `stake::OwnerCapability`
        let pool_address = signer::address_of(&stake_pool_signer);
        stake::initialize_stake_owner(&stake_pool_signer, 0, owner_address, owner_address);
```
