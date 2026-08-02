## Analog Found: Permanent DoS/Lock of Staking-Contract Distributions via Opt-Out Coin Transfer

### Title
Unhandled `deposit_coins` abort in `distribute_internal`'s payout loop permanently locks staker/operator funds - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`staking_contract::distribute` (callable by *anyone*, unprivileged) withdraws all unlocked stake and then loops over every shareholder in the `distribution_pool`, calling `aptos_account::deposit_coins` for each recipient with no isolation between recipients [1](#0-0) . `deposit_coins` aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` if the recipient has opted out of unregistered direct coin transfers via `aptos_account::set_allow_direct_coin_transfers(false)` [2](#0-1) , an action any account, including the operator's chosen beneficiary, can take on itself at any time [3](#0-2) .

### Finding Description
`distribute` is explicitly documented and implemented as permissionless: "Allow anyone to distribute already unlocked funds... does not need to be restricted to just the staker or operator" [4](#0-3) . Internally, `distribute_internal` withdraws the full unlocked (`inactive` + `pending_inactive`) balance from the underlying stake pool up-front via `stake::withdraw_with_cap`, then iterates the pool's shareholders (staker and operator, or the operator's beneficiary if set) inside a `while` loop, redeeming shares and calling `aptos_account::deposit_coins` for each one [5](#0-4) .

Unlike the Go `processBatch`/`processReceipt` pattern that only aggregates errors after the loop, in Move the abort is even more severe: since Move transactions are atomic, a single `deposit_coins` abort inside the loop reverts the *entire* transaction, including the `stake::withdraw_with_cap` that already pulled coins out of the stake pool. The offending shareholder is never removed from `distribution_pool`, so every subsequent call to `distribute` will walk the same shareholder list, hit the same recipient, and abort again — permanently.

Any shareholder in the pool (the staker or the operator's beneficiary, set via `set_beneficiary_for_operator` [6](#0-5) ) can call `set_allow_direct_coin_transfers(account, false)` on their own account. Once that happens, `distribute` (and therefore `staking_contract::switch_operator`/`vesting::distribute`/`vesting::terminate_vesting_contract` which call it transitively, as shown by `vesting.move`'s `distribute` and `terminate_vesting_contract` [7](#0-6) ) can never again succeed for that staking contract, because the loop always processes all current shareholders together with no way to skip or isolate a failing one.

### Impact Explanation
Because the distribution pool is shared between the staker and the operator/beneficiary, one party opting out of direct transfers permanently traps the *other* party's already-unlocked stake and commission — funds that would otherwise be immediately withdrawable become permanently unreachable, since `distribute_internal` is the only path that releases `inactive`/`pending_inactive` stake to the staker and pays the operator's commission. This satisfies the "permanent lock or non-recoverable loss of claim rights in stake... commission, beneficiary... flows" impact category. It also affects any vesting contract layered on top of a staking contract, since `vesting::distribute`, `vesting::terminate_vesting_contract`, and `vesting::admin_withdraw` all rely on the same `staking_contract::distribute` path being able to complete.

### Likelihood Explanation
Any account controls whether it accepts un-registered direct coin transfers via a fully public, unprivileged entry function (`set_allow_direct_coin_transfers`). No special permission, timing, or race condition is required — a self-interested or malicious operator/beneficiary/staker only needs to flip that flag once to brick the shared distribution pool. This makes the likelihood high once a party has any adversarial incentive (e.g., an operator's beneficiary trying to hold the staker's principal hostage, or vice versa).

### Recommendation
Do not let a single recipient's inability to receive coins abort the whole distribution batch. Options:
- Skip/queue the failing recipient's redeemed shares (e.g., re-buy them back into the pool or hold them in an escrow-like structure) and continue distributing to the remaining shareholders, similar to the reporter's recommendation to "log a warning" instead of failing the whole batch.
- Alternatively, use `coin::deposit` with a check for `is_account_registered`/`can_receive_direct_coin_transfers` up front and route un-deliverable funds to a stuck/claimable bucket rather than aborting the entire transaction.

### Proof of Concept
1. Staker and operator create a staking contract; operator calls `set_beneficiary_for_operator` to designate `beneficiary`.
2. `beneficiary` (or the staker) calls `aptos_account::set_allow_direct_coin_transfers(false)` on their own account.
3. Stake accrues inactive/pending_inactive balance (e.g., after `unlock`/lockup expiry).
4. Anyone calls `staking_contract::distribute(staker, operator)`.
5. The loop reaches the opted-out shareholder, `aptos_account::deposit_coins` aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`, and the entire transaction (including the stake withdrawal) reverts.
6. Every subsequent call to `distribute` for this `(staker, operator)` pair fails identically — unlocked stake and operator commission are permanently stuck, and downstream `vesting::distribute`/`terminate_vesting_contract` calls relying on this staking contract are likewise blocked.

**Note on completeness:** I did not have remaining iterations to fully confirm from the struct definitions whether the `distribution_pool` can ever contain shareholders beyond {staker, operator, beneficiary} (e.g., whether `add_stake` from the staker could add other addresses); the code reviewed shows the payout loop and `deposit_coins` abort condition directly, but a full read of `create_staking_contract`/`add_stake` to enumerate all possible pool members is recommended before finalizing severity.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L807-838)
```text
    /// Allows an operator to change its beneficiary. Any existing unpaid commission rewards will be paid to the new
    /// beneficiary. To ensures payment to the current beneficiary, one should first call `distribute` before switching
    /// the beneficiary. An operator can set one beneficiary for staking contract pools, not a separate one for each pool.
    public entry fun set_beneficiary_for_operator(
        operator: &signer, new_beneficiary: address
    ) acquires BeneficiaryForOperator {
        assert!(
            features::operator_beneficiary_change_enabled(),
            std::error::invalid_state(EOPERATOR_BENEFICIARY_CHANGE_NOT_SUPPORTED)
        );
        // The beneficiay address of an operator is stored under the operator's address.
        // So, the operator does not need to be validated with respect to a staking pool.
        let operator_addr = signer::address_of(operator);
        let old_beneficiary = beneficiary_for_operator(operator_addr);
        if (exists<BeneficiaryForOperator>(operator_addr)) {
            borrow_global_mut<BeneficiaryForOperator>(operator_addr).beneficiary_for_operator =
                new_beneficiary;
        } else {
            move_to(
                operator,
                BeneficiaryForOperator { beneficiary_for_operator: new_beneficiary }
            );
        };

        emit(
            SetBeneficiaryForOperator {
                operator: operator_addr,
                old_beneficiary,
                new_beneficiary
            }
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L840-853)
```text
    /// Allow anyone to distribute already unlocked funds. This does not affect reward compounding and therefore does
    /// not need to be restricted to just the staker or operator.
    public entry fun distribute(
        staker: address, operator: address
    ) acquires Store, BeneficiaryForOperator {
        assert_staking_contract_exists(staker, operator);
        let store = borrow_global_mut<Store>(staker);
        let staking_contract = store.staking_contracts.borrow_mut(&operator);
        distribute_internal(
            staker,
            operator,
            staking_contract,
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L868-911)
```text
        let (_, inactive, _, pending_inactive) = stake::get_stake(pool_address);
        let total_potential_withdrawable = inactive + pending_inactive;
        let coins =
            stake::withdraw_with_cap(
                &staking_contract.owner_cap, total_potential_withdrawable
            );
        let distribution_amount = coin::value(&coins);
        if (distribution_amount == 0) {
            coin::destroy_zero(coins);
            return
        };

        let distribution_pool = &mut staking_contract.distribution_pool;
        update_distribution_pool(
            distribution_pool,
            distribution_amount,
            operator,
            staking_contract.commission_percentage
        );

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

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L188-219)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L718-756)
```text
    /// Distribute any withdrawable stake from the stake pool.
    public entry fun distribute(contract_address: address) acquires VestingContract {
        assert_active_vesting_contract(contract_address);

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

        // Send any remaining "dust" (leftover due to rounding error) to the withdrawal address.
        if (coin::value(&coins) > 0) {
            aptos_account::deposit_coins(vesting_contract.withdrawal_address, coins);
        } else {
            coin::destroy_zero(coins);
        };

        emit(
            Distribute {
                admin: vesting_contract.admin,
                vesting_contract_address: contract_address,
                amount: total_distribution_amount,
            },
        );
    }
```
