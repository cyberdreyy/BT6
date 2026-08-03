**No vulnerability found for this question.**

Rationale: The premise requires an "attacker-shareholder" to call `staking_contract::add_stake` on the vesting contract's underlying stake pool, but that function is gated to the staker itself — it requires `signer::address_of(staker)` to match the account that owns the `Store` resource (i.e., the vesting contract's own resource account) and calls `assert_staking_contract_exists(staker_address, operator)` before withdrawing coins from that same signer and invoking `stake::add_stake_with_cap` with the internally-held `OwnerCapability`. [1](#0-0) 

A shareholder recorded in a `VestingContract`'s shareholder map has no signer capability over the vesting resource account and no `OwnerCapability` — only the admin can obtain the vesting account's signer internally (e.g. via `get_vesting_account_signer_internal`) for privileged operations like `update_operator`. [2](#0-1) 

There is no exposed entrypoint letting an arbitrary shareholder add stake to the vesting contract's pool, so the described front-running of `add_stake` by an unprivileged shareholder cannot occur. Additionally, the `pending_active_stake` check exists specifically to prevent this exact scenario: `terminate_vesting_contract` reads live stake state and reliably aborts with `EPENDING_STAKE_FOUND` if any pending-active stake exists at termination time, exactly as the proof idea itself confirms. [3](#0-2) 

Since the attack requires a capability the attacker cannot obtain, and the existing assertion already blocks the stated outcome (as acknowledged in the proof idea), this does not constitute a valid, unprivileged-input-driven vulnerability under the review's decision standard.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L514-531)
```text
    /// Add more stake to an existing staking contract.
    public entry fun add_stake(
        staker: &signer, operator: address, amount: u64
    ) acquires Store {
        let staker_address = signer::address_of(staker);
        assert_staking_contract_exists(staker_address, operator);

        let store = borrow_global_mut<Store>(staker_address);
        let staking_contract = store.staking_contracts.borrow_mut(&operator);

        // Add the stake to the stake pool.
        let staked_coins = coin::withdraw<AptosCoin>(staker, amount);
        stake::add_stake_with_cap(&staking_contract.owner_cap, staked_coins);

        staking_contract.principal += amount;
        let pool_address = staking_contract.pool_address;
        emit(AddStake { operator, pool_address, amount });
    }
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L771-793)
```text
    public entry fun terminate_vesting_contract(admin: &signer, contract_address: address) acquires VestingContract {
        assert_active_vesting_contract(contract_address);

        // Distribute all withdrawable coins, which should have been from previous rewards withdrawal or vest.
        distribute(contract_address);

        let vesting_contract = borrow_global_mut<VestingContract>(contract_address);
        verify_admin(admin, vesting_contract);
        let (active_stake, _, pending_active_stake, _) = stake::get_stake(vesting_contract.staking.pool_address);
        assert!(pending_active_stake == 0, error::invalid_state(EPENDING_STAKE_FOUND));

        // Unlock all remaining active stake.
        vesting_contract.state = VESTING_POOL_TERMINATED;
        vesting_contract.remaining_grant = 0;
        unlock_stake(vesting_contract, active_stake);

        emit(
            Terminate {
                admin: vesting_contract.admin,
                vesting_contract_address: contract_address,
            },
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L823-835)
```text
    public entry fun update_operator(
        admin: &signer,
        contract_address: address,
        new_operator: address,
        commission_percentage: u64,
    ) acquires VestingContract {
        let vesting_contract = borrow_global_mut<VestingContract>(contract_address);
        verify_admin(admin, vesting_contract);
        let contract_signer = &get_vesting_account_signer_internal(vesting_contract);
        let old_operator = vesting_contract.staking.operator;
        staking_contract::switch_operator(contract_signer, old_operator, new_operator, commission_percentage);
        vesting_contract.staking.operator = new_operator;
        vesting_contract.staking.commission_percentage = commission_percentage;
```
