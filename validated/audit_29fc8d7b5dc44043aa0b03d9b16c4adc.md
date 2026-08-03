No vulnerability found for this question.

**Analysis supporting this conclusion:**

`stake::unlock` and `stake::reactivate_stake` are public entry functions, but both require the caller's own account to hold the `OwnerCapability` resource (via `assert_owner_cap_exists(owner_address)` and `borrow_global<OwnerCapability>(owner_address)`) before they touch `StakePool.active`/`pending_inactive`. [1](#0-0) [2](#0-1) 

For a delegation pool, that `OwnerCapability` is `move_to`'d onto the resource-account signer (`stake_pool_signer`) during `initialize_owner`, which is invoked as part of `stake::initialize_stake_owner(&stake_pool_signer, ...)` inside `initialize_delegation_pool`. [3](#0-2) [4](#0-3) 

The corresponding `SignerCapability` for that resource account is stored only inside the `DelegationPool` resource (`stake_pool_signer_cap`) and is only ever converted back into a `signer` via `retrieve_stake_pool_owner`, which is a private (`fun`, not `public fun`) helper reachable only from inside `delegation_pool.move`. [5](#0-4) 

Nothing in `delegation_pool.move` calls `stake::extract_owner_cap` / `stake::deposit_owner_cap` to move that `OwnerCapability` to any externally-controlled account — a full-text search of the module shows only a single incidental mention of `OwnerCapability` (in a comment), and no code path exposes it. Since the resource account address has no associated private key and cannot originate a transaction on its own, and the only in-module function that can produce a signer for it (`retrieve_stake_pool_owner`) is private, there is no reachable path for an unprivileged external signer to call `stake::unlock` or `stake::reactivate_stake` directly against a delegation-pool-owned `StakePool`. All mutation of that `StakePool`'s `active`/`pending_inactive` fields is therefore only reachable through `delegation_pool`'s own entry points (`unlock`, `reactivate_stake`, `withdraw`), which always call `synchronize_delegation_pool` first, as seen in `unlock_internal` and `reactivate_stake`. [6](#0-5) [7](#0-6) 

The premise that a "stake pool signer capability obtained only indirectly" is usable by an unprivileged caller does not hold — there is no such capability accessible outside the module, so `active_shares`/`pending_inactive_shares` desynchronization via direct `stake::unlock`/`stake::reactivate_stake` calls is not possible.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L760-807)
```text
    fun initialize_owner(owner: &signer) acquires AllowedValidators {
        let owner_address = signer::address_of(owner);
        assert!(is_allowed(owner_address), error::not_found(EINELIGIBLE_VALIDATOR));
        assert!(
            !stake_pool_exists(owner_address),
            error::already_exists(EALREADY_REGISTERED)
        );

        move_to(
            owner,
            StakePool {
                active: coin::zero<AptosCoin>(),
                pending_active: coin::zero<AptosCoin>(),
                pending_inactive: coin::zero<AptosCoin>(),
                inactive: coin::zero<AptosCoin>(),
                locked_until_secs: 0,
                operator_address: owner_address,
                delegated_voter: owner_address,
                // Events.
                initialize_validator_events: account::new_event_handle<
                    RegisterValidatorCandidateEvent>(owner),
                set_operator_events: account::new_event_handle<SetOperatorEvent>(owner),
                add_stake_events: account::new_event_handle<AddStakeEvent>(owner),
                reactivate_stake_events: account::new_event_handle<ReactivateStakeEvent>(
                    owner
                ),
                rotate_consensus_key_events: account::new_event_handle<
                    RotateConsensusKeyEvent>(owner),
                update_network_and_fullnode_addresses_events: account::new_event_handle<
                    UpdateNetworkAndFullnodeAddressesEvent>(owner),
                increase_lockup_events: account::new_event_handle<IncreaseLockupEvent>(
                    owner
                ),
                join_validator_set_events: account::new_event_handle<JoinValidatorSetEvent>(
                    owner
                ),
                distribute_rewards_events: account::new_event_handle<DistributeRewardsEvent>(
                    owner
                ),
                unlock_stake_events: account::new_event_handle<UnlockStakeEvent>(owner),
                withdraw_stake_events: account::new_event_handle<WithdrawStakeEvent>(owner),
                leave_validator_set_events: account::new_event_handle<
                    LeaveValidatorSetEvent>(owner)
            }
        );

        move_to(owner, OwnerCapability { pool_address: owner_address });
    }
```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L930-936)
```text

        assert_reconfig_not_in_progress();
        let owner_address = signer::address_of(owner);
        assert_owner_cap_exists(owner_address);
        let ownership_cap = borrow_global<OwnerCapability>(owner_address);
        reactivate_stake_with_cap(ownership_cap, amount);
    }
```

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1141-1147)
```text
    public entry fun unlock(owner: &signer, amount: u64) acquires OwnerCapability, StakePool {
        assert_reconfig_not_in_progress();
        let owner_address = signer::address_of(owner);
        assert_owner_cap_exists(owner_address);
        let ownership_cap = borrow_global<OwnerCapability>(owner_address);
        unlock_with_cap(amount, ownership_cap);
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L867-873)
```text
        let (stake_pool_signer, stake_pool_signer_cap) = account::create_resource_account(owner, seed);
        coin::register<AptosCoin>(&stake_pool_signer);

        // stake_pool_signer will be owner of the stake pool and have its `stake::OwnerCapability`
        let pool_address = signer::address_of(&stake_pool_signer);
        stake::initialize_stake_owner(&stake_pool_signer, 0, owner_address, owner_address);

```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1101-1105)
```text
    /// Retrieves the shared resource account owning the stake pool in order
    /// to forward a stake-management operation to this underlying pool.
    fun retrieve_stake_pool_owner(pool: &DelegationPool): signer {
        account::create_signer_with_capability(&pool.stake_pool_signer_cap)
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1540-1563)
```text
    fun unlock_internal(
        delegator_address: address,
        pool_address: address,
        amount: u64
    ) acquires DelegationPool, GovernanceRecords {
        assert!(delegator_address != NULL_SHAREHOLDER, error::invalid_argument(ECANNOT_UNLOCK_NULL_SHAREHOLDER));

        // fail unlock of more stake than `active` on the stake pool
        let (active, _, _, _) = stake::get_stake(pool_address);
        assert!(amount <= active, error::invalid_argument(ENOT_ENOUGH_ACTIVE_STAKE_TO_UNLOCK));

        let pool = borrow_global_mut<DelegationPool>(pool_address);
        amount = coins_to_transfer_to_ensure_min_stake(
            &pool.active_shares,
            pending_inactive_shares_pool(pool),
            delegator_address,
            amount,
        );
        amount = redeem_active_shares(pool, delegator_address, amount);

        stake::unlock(&retrieve_stake_pool_owner(pool), amount);

        buy_in_pending_inactive_shares(pool, delegator_address, amount);
        assert_min_pending_inactive_balance(pool, delegator_address);
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1574-1590)
```text
    /// Move `amount` of coins from pending_inactive to active.
    public entry fun reactivate_stake(
        delegator: &signer,
        pool_address: address,
        amount: u64
    ) acquires DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage, DelegationPoolAllowlisting {
        // short-circuit if amount to reactivate is 0 so no event is emitted
        if (amount == 0) { return };

        let delegator_address = signer::address_of(delegator);
        assert_delegator_allowlisted(pool_address, delegator_address);

        // synchronize delegation and stake pools before any user operation
        synchronize_delegation_pool(pool_address);

        let pool = borrow_global_mut<DelegationPool>(pool_address);
        amount = coins_to_transfer_to_ensure_min_stake(
```
