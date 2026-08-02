Based on the investigation, the strongest local analog to the "malicious payee blocks push-payment to all others" bug from the external `send()` report is the interaction between `aptos_account::deposit_coins`'s direct-transfer opt-out check and the shared distribution loops in `staking_contract.move` (and similarly `vesting.move`).

### Title
Operator can permanently freeze a staker's already-unlocked stake by configuring a non-receiving beneficiary - (File: aptos-move/framework/aptos-framework/sources/staking_contract.move)

### Summary
`staking_contract::distribute_internal` withdraws *all* inactive + pending_inactive coins from the stake pool in one shot and then pays every recipient (staker and operator/beneficiary) out of that single `coins` object inside one loop. If any single recipient's deposit aborts, the whole transaction — including the earlier withdrawal — reverts, so nobody gets paid. Because the operator fully controls the beneficiary address via `set_beneficiary_for_operator`, and any Aptos account can permissionlessly opt out of unregistered coin deposits via `aptos_account::set_allow_direct_coin_transfers(false)`, an operator can turn their own commission recipient into a permanently-reverting sink, which blocks the staker's own unrelated principal withdrawal in the same call.

### Finding Description
`distribute_internal` withdraws the pool's total withdrawable balance up front and distributes it to every shareholder in the `distribution_pool` (staker principal and operator commission/beneficiary) inside one `while` loop, using `aptos_account::deposit_coins`: [1](#0-0) 

`aptos_account::deposit_coins` aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` if the target account is unregistered for the coin type and has opted out of arbitrary transfers: [2](#0-1) 

Any account can independently, permissionlessly, set this opt-out flag: [3](#0-2) 

The operator fully controls which address receives their commission by calling `set_beneficiary_for_operator`, without needing the staker's consent: [4](#0-3) 

Because Move transaction aborts are atomic, an abort while paying the operator/beneficiary entry in the loop reverts the entire `distribute_internal` call — including the `stake::withdraw_with_cap` that had already pulled the staker's own unlocked principal out of the stake pool for payout in the same batch. `distribute_internal` is reachable from several unprivileged/staker-triggered entry points (`distribute`, `unlock_stake`, `request_commission`, `update_commision`, `switch_operator`), all of which share this same all-or-nothing payout batch: [5](#0-4) [6](#0-5) 

The same push-payment pattern also exists in `vesting::distribute`, which loops over every shareholder's beneficiary in a single call and pays "dust" to the withdrawal address only after the loop completes: [7](#0-6) 

The Move spec for vesting explicitly acknowledges this class of issue is unverified/unhandled: "Can't handle abort in loop": [8](#0-7) 

### Impact Explanation
This traps stake value and breaks the withdrawal-rights invariant: a staker's already-unlocked, already-earned principal and rewards become permanently non-withdrawable as long as the operator (or whoever controls the beneficiary account) keeps the beneficiary's `DirectTransferConfig.allow_arbitrary_coin_transfers` set to `false` and unregistered for `AptosCoin`. The staker has no unilateral recovery path — only the operator (by fixing the beneficiary, or the beneficiary account itself by re-enabling transfers/registering) can unblock the funds. This matches the "Permanent lock or non-recoverable loss of claim rights in stake ... commission, beneficiary ... flows" impact category, and gives the operator leverage to hold a staker's unlocked funds hostage.

### Likelihood Explanation
Medium-high. No collusion or privileged access is required beyond the operator role, which is not a trust boundary the staker controls — `create_staking_contract` lets the staker pick an operator, but nothing stops that operator later choosing a hostile beneficiary and toggling `set_allow_direct_coin_transfers(false)` on it (a completely permissionless, standard account operation). The bug triggers automatically the next time any party calls `distribute`, `unlock_stake`, `request_commission`, `update_commision`, or `switch_operator`.

### Recommendation
- In `distribute_internal` (and `vesting::distribute`), do not let one recipient's failed deposit revert payouts to all other recipients: wrap each `aptos_account::deposit_coins` call so that on failure, the amount is retained in the distribution pool (re-buy-in) for that recipient to withdraw later (a pull-payment fallback), rather than aborting the whole batch.
- Alternatively, withdraw and pay out each recipient in its own atomic sub-step so that a failure for one recipient does not prevent stake already withdrawn for others (especially the staker's own principal) from being paid.
- Consider disallowing `set_beneficiary_for_operator` targets that are not registered for `AptosCoin` and have opted out of direct transfers, or require the beneficiary to explicitly register/accept before being set.

### Proof of Concept
1. Staker creates a staking contract with `operator` via `create_staking_contract` (staking_contract.move).
2. `operator` calls `set_beneficiary_for_operator(operator, evil_beneficiary)` (staking_contract.move:810-838), pointing commission at `evil_beneficiary`, an address they control.
3. From `evil_beneficiary`, call `aptos_account::set_allow_direct_coin_transfers(evil_beneficiary_signer, false)` and never call `coin::register<AptosCoin>` for it.
4. Staker calls `unlock_stake(staker, operator, amount)` to unlock part or all of their stake; after lockup expiry, staker (or anyone) calls `distribute(staker, operator)`.
5. `distribute_internal` withdraws the full inactive+pending_inactive balance, then in the payout loop reaches the operator/beneficiary entry and calls `aptos_account::deposit_coins(evil_beneficiary, ...)`, which aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`.
6. The entire transaction reverts — the staker's own already-unlocked principal is not paid out, and remains stuck for as long as `evil_beneficiary`'s configuration is unchanged.

Note: I was unable, within the remaining tool budget, to confirm the exact function names/permissions for setting a per-shareholder beneficiary in `vesting.move` (searches for `set_beneficiary`/`get_beneficiary` in that file returned matches but I could not read their bodies before running out of iterations), so the vesting-contract variant of this issue (many shareholders, one poisoned beneficiary blocking all vested payouts) is reported here only as a secondary, unverified analog — the `staking_contract.move` path above is the one with fully verified local code support.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L676-690)
```text
    /// Staker can call this to request withdrawal of part or all of their staking_contract.
    /// This also triggers paying commission to the operator for accounting simplicity.
    public entry fun unlock_stake(
        staker: &signer, operator: address, amount: u64
    ) acquires Store, BeneficiaryForOperator {
        // Short-circuit if amount is 0.
        if (amount == 0) return;

        let staker_address = signer::address_of(staker);
        assert_staking_contract_exists(staker_address, operator);

        let store = borrow_global_mut<Store>(staker_address);
        let staking_contract = store.staking_contracts.borrow_mut(&operator);

        // Force distribution of any already inactive stake.
```

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L855-920)
```text
    /// Distribute all unlocked (inactive) funds according to distribution shares.
    fun distribute_internal(
        staker: address,
        operator: address,
        staking_contract: &mut StakingContract,
    ) acquires BeneficiaryForOperator {
        let pool_address = staking_contract.pool_address;
        // Create the Staker resource if it doesn't exist to backfill the Staker resource for each pool.
        if (!exists<Staker>(pool_address)) {
            let pool_signer =
                &account::create_signer_with_capability(&staking_contract.signer_cap);
            move_to(pool_signer, Staker { staker });
        };
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

        // In case there's any dust left, send them all to the staker.
        if (coin::value(&coins) > 0) {
            aptos_account::deposit_coins(staker, coins);
            distribution_pool.update_total_coins(0);
        } else {
            coin::destroy_zero(coins);
        }
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

**File:** aptos-move/framework/aptos-framework/sources/vesting.spec.move (L307-314)
```text
    spec distribute(contract_address: address) {
        // TODO: Can't handle abort in loop.
        pragma verify = false;
        include ActiveVestingContractAbortsIf;

        let vesting_contract = global<VestingContract>(contract_address);
        include WithdrawStakeAbortsIf { vesting_contract };
    }
```
