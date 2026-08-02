## Analysis Summary

The ThorChain bug reduces to one core invariant: **a state-mutating operation that irreversibly commits value on behalf of multiple parties must not let one recipient's failure corrupt or block the outcome for the others.** In THORChain_Router, the flaw was silent-success (wrong event on failed transfer). Move's abort-on-failure semantics rule out that exact "silent success" pattern (a failed `coin` transfer aborts the whole transaction rather than emitting a false success event), so I searched for the inverse but equally damaging analog: **a single uncooperative recipient causing an atomic batch operation to permanently abort for everyone else sharing the same distribution.**

I traced `aptos_framework::vesting::distribute`, `aptos_framework::staking_contract::distribute_internal`, and `aptos_framework::delegation_pool` withdraw paths. The strongest, most directly attacker-controlled candidate is in `vesting.move`'s `distribute()`.

### Title
Single uncooperative shareholder can permanently freeze fund distribution for an entire vesting pool - (File: `aptos-move/framework/aptos-framework/sources/vesting.move`)

### Summary
`distribute()` iterates over *all* shareholders of a vesting contract in one atomic transaction and calls `aptos_account::deposit_coins` for each. Because `aptos_account::deposit_coins` aborts if the recipient is unregistered for `AptosCoin` **and** has opted out of direct coin transfers via `set_allow_direct_coin_transfers(false)`, any single shareholder can unilaterally make this deposit call abort. Since Move transactions are atomic and the loop has no per-recipient isolation, this reverts the *entire* `distribute()` call — permanently blocking withdrawal of vested grant and reward stake for every other shareholder in the same pool, not just the offending one.

### Finding Description
`distribute()` withdraws all currently-withdrawable stake and then pays every shareholder in a single loop: [1](#0-0) 

Each payout uses `aptos_account::deposit_coins`, which only succeeds unconditionally when the recipient is already registered for the coin type; otherwise it requires the recipient to allow arbitrary transfers: [2](#0-1) 

Any account holder can flip `allow_arbitrary_coin_transfers` to `false` on their own account, unprivileged, at any time: [3](#0-2) 

If a shareholder's address (or their default beneficiary, since `get_beneficiary` falls back to the shareholder itself) has never registered a `CoinStore`/primary store for `AptosCoin` and disables `allow_arbitrary_coin_transfers`, then `deposit_coins` will hit the `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` assertion and abort. Because Move transactions revert atomically on abort, this reverts the stake withdrawal (`withdraw_stake`) and every other shareholder's payout that was queued in the same `distribute()` call — even though those other shareholders did nothing wrong and have no way to exclude the misbehaving party from the shared pool.

The same batch-atomicity pattern also exists in `staking_contract::distribute_internal`, which pays out all pending commission/withdrawal distributions to the operator's beneficiary and the staker in a single loop and transaction: [4](#0-3) 

### Impact Explanation
This breaks the vesting/staking invariant that "Unlock, reactivate, withdraw, synchronize, and beneficiary-update paths must not redirect value or strand it permanently." Once triggered, `distribute()` (and `terminate_vesting_contract`, which itself calls `distribute()` before allowing termination) can never succeed for that vesting contract again while the poisoned shareholder's account remains unregistered and opted out — which the shareholder fully controls and can leave in that state indefinitely. This strands vested grant coins and unlock rewards for all other shareholders of the pool, and also blocks the admin from ever terminating/withdrawing the contract, since `terminate_vesting_contract` calls `distribute` first: [5](#0-4) 

This is a permanent, non-recoverable loss of claim rights for uninvolved shareholders, caused by an unprivileged, single-account action.

### Likelihood Explanation
Likelihood is moderate-to-high in adversarial or simply careless settings: any shareholder can trigger this with two standard, permissionless calls (`set_allow_direct_coin_transfers(false)` and simply never calling `coin::register`/receiving APT before being added as a shareholder). No special role, timing, or race condition is required — it only requires being one of the (up to 30) shareholders in a vesting pool, and multi-shareholder vesting pools are an explicitly supported configuration.

### Recommendation
- In `distribute()` (and `distribute_internal` in `staking_contract.move`), wrap each per-recipient deposit so a failure for one shareholder does not abort the whole batch — e.g., catch failure by pre-checking `aptos_account::can_receive_direct_coin_transfers`/registration state per recipient, and if the recipient cannot receive funds, hold their share in an escrow/dedicated resource (or skip and retry later) instead of aborting the entire distribution.
- Alternatively, require/force registration for `AptosCoin` for shareholders at vesting-contract creation time (as is already done for the contract account itself in `create_vesting_contract_account`), removing the ability to be both an active shareholder and unregistered simultaneously.

### Proof of Concept
1. Admin creates a vesting contract with shareholders `[A, B]` via `create_vesting_schedule`/`vest`-related setup functions (see `setup_vesting_contract` pattern used in existing tests).
2. Shareholder `B`'s address is a fresh account: never calls `coin::register<AptosCoin>` and instead calls `aptos_account::set_allow_direct_coin_transfers(&B, false)`.
3. Time passes; rewards/vested amount accrue and become withdrawable.
4. Anyone calls `vesting::distribute(contract_address)`.
5. Inside the shareholder loop, when the recipient resolves to `B` (or `B`'s default beneficiary), `aptos_account::deposit_coins` hits `!coin::is_account_registered<AptosCoin>(B)` → `can_receive_direct_coin_transfers(B)` is `false` → `abort EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`.
6. The entire `distribute()` transaction reverts, meaning shareholder `A` (and any others) also fail to receive their otherwise-withdrawable, fully vested funds — indefinitely, until `B` changes their own configuration (which `B` has no incentive to do if griefing).

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L730-740)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L770-793)
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

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L187-219)
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
            emit(
                DirectCoinTransferConfigUpdated {
                    account: addr,
                    new_allow_direct_transfers: allow
                }
            );
            move_to(account, direct_transfer_config);
        };
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L888-911)
```text
        // Buy all recipients out of the distribution pool.
        while (distribution_pool.shareholders_count() > 0) {
            let recipients = distribution_pool.shareholders();
            let recipient = recipients[0];
            let current_shares = distribution_pool.shares(recipient);
            let amount_to_distribute =
                distribution_pool.redeem_shares(recipient, current_shares);
            // If the recipient is the operator, send the commission to the beneficiary instead.
            if (recipient == operator) {
                recipient = beneficiary_for_operator(operator);
            };
            aptos_account::deposit_coins(
                recipient, coin::extract(&mut coins, amount_to_distribute)
            );

            emit(
                Distribute {
                    operator,
                    pool_address,
                    recipient,
                    amount: amount_to_distribute
                }
            );
        };
```
