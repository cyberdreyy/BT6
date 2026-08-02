[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L370-377)
```text
    #[view]
    /// Return the address of the stake pool to be created with the provided staker, operator and seed.
    public fun get_expected_stake_pool_address(
        staker: address, operator: address, contract_creation_seed: vector<u8>
    ): address {
        let seed = create_resource_account_seed(staker, operator, contract_creation_seed);
        account::create_resource_address(&staker, seed)
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L1041-1053)
```text
    /// Create the seed to derive the resource account address.
    fun create_resource_account_seed(
        staker: address, operator: address, contract_creation_seed: vector<u8>
    ): vector<u8> {
        let seed = bcs::to_bytes(&staker);
        seed.append(bcs::to_bytes(&operator));
        // Include a salt to avoid conflicts with any other modules out there that might also generate
        // deterministic resource accounts for the same staker + operator addresses.
        seed.append(SALT);
        // Add an extra salt given by the staker in case an account with the same address has already been created.
        seed.append(contract_creation_seed);
        seed
    }
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L560-592)
```text
        // Create a coins pool to track shareholders and shares of the grant.
        let grant = coin::zero<AptosCoin>();
        let grant_amount = 0;
        let grant_pool = pool_u64::create(MAXIMUM_SHAREHOLDERS);
        shareholders.for_each_ref(|shareholder| {
            let shareholder: address = *shareholder;
            let (_, buy_in) = simple_map::remove(&mut buy_ins, &shareholder);
            let buy_in_amount = coin::value(&buy_in);
            coin::merge(&mut grant, buy_in);
            pool_u64::buy_in(
                &mut grant_pool,
                shareholder,
                buy_in_amount,
            );
            grant_amount += buy_in_amount;
        });
        assert!(grant_amount > 0, error::invalid_argument(EZERO_GRANT));

        // If this is the first time this admin account has created a vesting contract, initialize the admin store.
        let admin_address = signer::address_of(admin);
        if (!exists<AdminStore>(admin_address)) {
            move_to(admin, AdminStore {
                vesting_contracts: vector::empty<address>(),
                nonce: 0,
                create_events: new_event_handle<CreateVestingContractEvent>(admin),
            });
        };

        // Initialize the vesting contract in a new resource account. This allows the same admin to create multiple
        // pools.
        let (contract_signer, contract_signer_cap) = create_vesting_contract_account(admin, contract_creation_seed);
        let pool_address = staking_contract::create_staking_contract_with_coins(
            &contract_signer, operator, voter, grant, commission_percentage, contract_creation_seed);
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L1028-1050)
```text
    /// Create a salt for generating the resource accounts that will be holding the VestingContract.
    /// This address should be deterministic for the same admin and vesting contract creation nonce.
    fun create_vesting_contract_account(
        admin: &signer,
        contract_creation_seed: vector<u8>,
    ): (signer, SignerCapability) acquires AdminStore {
        let admin_store = borrow_global_mut<AdminStore>(signer::address_of(admin));
        let seed = bcs::to_bytes(&signer::address_of(admin));
        seed.append(bcs::to_bytes(&admin_store.nonce));
        admin_store.nonce += 1;

        // Include a salt to avoid conflicts with any other modules out there that might also generate
        // deterministic resource accounts for the same admin address + nonce.
        seed.append(VESTING_POOL_SALT);
        seed.append(contract_creation_seed);

        let (account_signer, signer_cap) = account::create_resource_account(admin, seed);
        // Register the vesting contract account to receive APT as it'll be sent to it when claiming unlocked stake from
        // the underlying staking contract.
        coin::register<AptosCoin>(&account_signer);

        (account_signer, signer_cap)
    }
```
