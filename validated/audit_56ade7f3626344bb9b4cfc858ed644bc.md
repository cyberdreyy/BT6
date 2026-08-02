## Finding: A single opted-out shareholder can permanently block `distribute()` for an entire vesting pool [1](#0-0) 

### Title
Single shareholder opting out of direct coin transfers can revert `distribute()` for all shareholders and permanently block vesting-pool termination — ([File: aptos-move/framework/aptos-framework/sources/vesting.move])

### Summary
`vesting::distribute()` iterates over every shareholder of a vesting pool in a single loop and calls `aptos_account::deposit_coins` for each one. If the deposit to *any single* shareholder aborts, the abort propagates and the entire `distribute()` transaction reverts — no shareholder receives their share, exactly the "one bad order breaks the whole batch" pattern from the CrabNetting report. Unlike `staking_contract::distribute_internal` (single recipient), `vesting::distribute()` fans out to many independent parties in one atomic loop with no per-recipient isolation.

### Finding Description
`create_vesting_contract` only asserts that the `withdrawal_address` is registered for APT; it never requires shareholders to be registered for APT: [2](#0-1) 

`aptos_account::deposit_coins` aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` if the recipient is not registered for the coin type AND has opted out of direct coin transfers via `set_allow_direct_coin_transfers`: [3](#0-2) [4](#0-3) 

`distribute()` loops over `grant_pool.shareholders()` and unconditionally calls `deposit_coins` for each shareholder (or their beneficiary) inside a single `for_each_ref` closure with no per-iteration error handling: [5](#0-4) 

Any shareholder address is a completely permissionless, unprivileged actor: it need only be added to the vesting pool by the admin at creation time (a normal, expected onboarding step — not a special privilege), and then that shareholder (or anyone controlling that address) can call the permissionless `set_allow_direct_coin_transfers(signer, false)` on their own account. Since the address was never forced to register for AptosCoin, the next `distribute()` call by *anyone* aborts inside the loop, reverting the whole distribution for every shareholder in the pool, not just the griefing one.

The blast radius is worse than in staking_contract, because `terminate_vesting_contract` and `admin_withdraw` both funnel through `distribute()`/`withdraw_stake()` first: [6](#0-5) 
`terminate_vesting_contract` calls `distribute(contract_address)` unconditionally before doing anything else, and `admin_withdraw` requires the contract to already be `VESTING_POOL_TERMINATED`. If `distribute()` reverts every time due to the griefing shareholder, the admin can never terminate the contract, and `admin_withdraw` can never be reached — permanently stranding the withdrawal_address's and every other shareholder's funds inside the vesting contract's stake pool.

### Impact Explanation
This is a genuine unprivileged griefing/lockup vector matching the required impact "Permanent lock or non-recoverable loss of claim rights in stake, delegation, commission, beneficiary, or vesting flows": one shareholder (an ordinary, non-privileged participant) can indefinitely block reward/vested-coin distribution for every other shareholder in the same pool, and can block the admin from ever terminating the pool and reclaiming remaining grant funds via `admin_withdraw`, since both paths call `distribute()` unconditionally before other logic executes.

### Likelihood Explanation
High under the required threat model: the only precondition is being added as a shareholder to a vesting contract (an expected, unprivileged role) and calling a single permissionless framework function (`set_allow_direct_coin_transfers`) on one's own account. No special role, keys, or governance access is needed, and the griefer bears no cost beyond forgoing their own distributions.

### Recommendation
In `distribute()` (and analogous multi-recipient loops), do not let a single recipient's failed deposit abort the whole batch. Either:
- Pre-check each recipient with `coin::is_account_registered` / `can_receive_direct_coin_transfers` before calling `deposit_coins`, and skip/queue that recipient's share (e.g., accumulate into a per-shareholder claimable balance) instead of aborting, or
- Wrap the per-shareholder deposit so a single failure only skips that shareholder rather than reverting the transaction, mirroring the fix recommended in the referenced report (skip invalid legs instead of failing the whole batch operation).

### Proof of Concept
1. Admin creates a vesting contract with shareholders `[S1, S2, S3]` via `create_vesting_contract`; only `withdrawal_address` is required to be APT-registered, `S1..S3` are not.
2. `S1` never registers for AptosCoin and calls `aptos_account::set_allow_direct_coin_transfers(S1_signer, false)`.
3. Time passes, rewards/vested coins accrue; anyone calls `vesting::distribute(contract_address)`.
4. The `for_each_ref` loop reaches `S1`, calls `aptos_account::deposit_coins(S1, ...)`, which hits `!coin::is_account_registered<AptosCoin>(S1)` and `!can_receive_direct_coin_transfers(S1)`, aborting with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`.
5. The abort reverts the entire `distribute()` call — `S2` and `S3` receive nothing.
6. Admin attempts `terminate_vesting_contract`, which calls `distribute()` first and aborts the same way — the pool can never be terminated nor `admin_withdraw` reached while `S1` keeps opting out.

**Caveat / uncertainty:** I was not able to fully confirm from the indexed content whether an intervening framework check (e.g., an implicit re-registration or governance-imposed override) exists elsewhere that would neutralize this specific path; the analysis is based on the `distribute()`, `aptos_account::deposit_coins`, and `create_vesting_contract` code shown above. A background Devin session with full repository access should re-verify end-to-end via a Move unit test (creating a vesting contract with an unregistered, opted-out shareholder and asserting `distribute()` aborts) before treating this as fully confirmed.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L549-576)
```text
        assert!(
            !system_addresses::is_reserved_address(withdrawal_address),
            error::invalid_argument(EINVALID_WITHDRAWAL_ADDRESS),
        );
        assert_account_is_registered_for_apt(withdrawal_address);
        assert!(shareholders.length() > 0, error::invalid_argument(ENO_SHAREHOLDERS));
        assert!(
            buy_ins.length() == shareholders.length(),
            error::invalid_argument(ESHARES_LENGTH_MISMATCH),
        );

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
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L722-740)
```text
        let vesting_contract = borrow_global_mut<VestingContract>(contract_address);
        let coins = withdraw_stake(vesting_contract, contract_address);
        let total_distribution_amount = coin::value(&coins);
        if (total_distribution_amount == 0) {
            coin::destroy_zero(coins);
            return
        };

        // Distribute coins to all shareholders in the vesting contract.
        let grant_pool = &vesting_contract.grant_pool;
        let shareholders = &grant_pool.shareholders();
        shareholders.for_each_ref(|shareholder| {
            let shareholder = *shareholder;
            let shares = pool_u64::shares(grant_pool, shareholder);
            let amount = pool_u64::shares_to_amount_with_total_coins(grant_pool, shares, total_distribution_amount);
            let share_of_coins = coin::extract(&mut coins, amount);
            let recipient_address = get_beneficiary(vesting_contract, shareholder);
            aptos_account::deposit_coins(recipient_address, share_of_coins);
        });
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L770-810)
```text
    /// Terminate the vesting contract and send all funds back to the withdrawal address.
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

    /// Withdraw all funds to the preset vesting contract's withdrawal address. This can only be called if the contract
    /// has already been terminated.
    public entry fun admin_withdraw(admin: &signer, contract_address: address) acquires VestingContract {
        let vesting_contract = borrow_global<VestingContract>(contract_address);
        assert!(
            vesting_contract.state == VESTING_POOL_TERMINATED,
            error::invalid_state(EVESTING_CONTRACT_STILL_ACTIVE)
        );

        let vesting_contract = borrow_global_mut<VestingContract>(contract_address);
        verify_admin(admin, vesting_contract);
        let coins = withdraw_stake(vesting_contract, contract_address);
        let amount = coin::value(&coins);
        if (amount == 0) {
            coin::destroy_zero(coins);
            return
```

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L111-131)
```text
    public fun deposit_coins<CoinType>(
        to: address, coins: Coin<CoinType>
    ) acquires DirectTransferConfig {
        if (!account::exists_at(to)) {
            create_account(to);
            spec {
                // TODO(fa_migration)
                // assert coin::spec_is_account_registered<AptosCoin>(to);
                // assume aptos_std::type_info::type_of<CoinType>() == aptos_std::type_info::type_of<AptosCoin>() ==>
                //     coin::spec_is_account_registered<CoinType>(to);
            };
        };
        if (!coin::is_account_registered<CoinType>(to)) {
            assert!(
                can_receive_direct_coin_transfers(to),
                error::permission_denied(EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS)
            );
            coin::register<CoinType>(&create_signer(to));
        };
        coin::deposit<CoinType>(to, coins)
    }
```

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L187-210)
```text
    /// Set whether `account` can receive direct transfers of coins that they have not explicitly registered to receive.
    public entry fun set_allow_direct_coin_transfers(
        account: &signer, allow: bool
    ) acquires DirectTransferConfig {
        let addr = signer::address_of(account);
        if (exists<DirectTransferConfig>(addr)) {
            let direct_transfer_config = borrow_global_mut<DirectTransferConfig>(addr);
            // Short-circuit to avoid emitting an event if direct transfer config is not changing.
            if (direct_transfer_config.allow_arbitrary_coin_transfers == allow) { return };

            direct_transfer_config.allow_arbitrary_coin_transfers = allow;

            emit(
                DirectCoinTransferConfigUpdated {
                    account: addr,
                    new_allow_direct_transfers: allow
                }
            );
        } else {
            let direct_transfer_config = DirectTransferConfig {
                allow_arbitrary_coin_transfers: allow,
                update_coin_transfer_events: new_event_handle<
                    DirectCoinTransferConfigUpdatedEvent>(account)
            };
```
