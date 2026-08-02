### Title
Push-payment fan-out in `distribute()` lets one unregistered/opted-out recipient permanently DoS reward, commission, and vesting payouts for all other shareholders - (File: aptos-move/framework/aptos-framework/sources/staking_contract.move, aptos-move/framework/aptos-framework/sources/vesting.move)

### Summary
The external report's core defect is a "push-payment" pattern: a single payout call fans coins out to a recipient that cannot or will not accept them, and the whole redemption reverts, permanently blocking fund release. Aptos has a structurally identical pattern in `staking_contract::distribute_internal` and `vesting::distribute`: both iterate over *all* pending recipients in one atomic loop and call `aptos_account::deposit_coins`, which can `abort` for any single recipient that has opted out of unregistered direct coin transfers. Since the loop is atomic, one griefing/unregistered recipient blocks payment to every other staker, delegator, or vesting shareholder sharing that pool.

### Finding Description
`aptos_account::deposit_coins` will abort with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` whenever the recipient is not yet registered for the `CoinType` (here `AptosCoin`'s legacy `CoinStore`) and has explicitly disabled direct coin transfers via `set_allow_direct_coin_transfers`: [1](#0-0) 

Setting that opt-out flag is a fully permissionless, self-directed action requiring no special role: [2](#0-1) 

Because APT is being migrated to the fungible-asset/primary-fungible-store model (`register_apt` calls `ensure_primary_fungible_store_exists`, and ordinary APT transfers/gas payment go through `fungible_transfer_only`/primary fungible store, not the legacy `coin::CoinStore<AptosCoin>`), a normal, actively transacting account can legitimately never register a legacy `CoinStore<AptosCoin>`: [3](#0-2) 

`staking_contract::distribute_internal` is invoked by the permissionless `distribute` entry function ("Allow anyone to distribute already unlocked funds") and pays out every pending recipient (staker, operator, and beneficiary) in a single atomic `while` loop using `aptos_account::deposit_coins`: [4](#0-3) 

`vesting::distribute` does the same for all shareholders of a vesting contract in one atomic loop: [5](#0-4) 

If any single recipient in that pool (a) has never registered `CoinStore<AptosCoin>` (the normal/default state for many accounts under the FA-migrated framework) and (b) has called `aptos_account::set_allow_direct_coin_transfers(account, false)` on their own account, then every call to `distribute`/`distribute_internal` that would pay that recipient aborts, rolling back the entire transaction — including the `stake::withdraw_with_cap`/`withdraw_stake` and share redemption for every *other* recipient in the same staking contract or vesting contract. This mirrors the bug-report's shortcoming exactly: a push-based payout to a recipient that cannot accept funds renders the whole redemption impossible, except here it also blocks unrelated third parties (all other stakers/shareholders/the operator's commission) rather than just the opted-out account itself.

### Impact Explanation
This breaks the "unlock/withdraw/distribute must not strand value" invariant for `staking_contract` and `vesting` flows. A single unprivileged party (a staker, delegator, or vesting shareholder who simply calls `set_allow_direct_coin_transfers(false)` on their own account) can permanently deny reward/commission/vesting distribution to every other participant sharing that `StakingContract`/`VestingContract`, since `distribute` can never succeed past the poisoned recipient. This traps operator commission and other legitimate stakers' rewards indefinitely (the funds remain unlocked in the stake pool but can never be pushed out via `distribute`), qualifying as "Operator commission, beneficiary payout, or share-accounting corruption that... traps value" and "Permanent lock or non-recoverable loss of claim rights in stake... commission, beneficiary, or vesting flows."

### Likelihood Explanation
The precondition (an account without a legacy `CoinStore<AptosCoin>` that opts out of direct transfers) is easy to reach and requires no privilege — `set_allow_direct_coin_transfers` is a public entry function callable by any account holder, and non-registration of the legacy `CoinStore<AptosCoin>` is increasingly the *default* state as APT moves to the fungible-asset model. Any staker/delegator/vesting-shareholder can trigger this against their co-participants with a single transaction and no further action needed; the DoS persists until governance intervenes (there is no code path to remove or bypass a poisoned recipient from `distribution_pool`/`grant_pool`).

### Recommendation
Do not let one recipient's inability/refusal to receive funds abort payout to all other recipients:
- In `distribute_internal`/`distribute`, check `coin::is_account_registered`/`aptos_account::can_receive_direct_coin_transfers` per-recipient before attempting `deposit_coins`; if a recipient cannot receive, skip them and retain their shares/coins in the pool for later claim instead of aborting the whole loop.
- Alternatively, switch to a pull-based claim model (each recipient calls their own `claim`/`withdraw` similar to `delegation_pool::withdraw`, which only affects the caller) rather than a push-based fan-out affecting unrelated parties.
- Consider forcing legacy `CoinStore<AptosCoin>` registration (or primary-store equivalent) at the time a staker/delegator/shareholder is added to a pool, closing the precondition window.

### Proof of Concept
1. Attacker (or any careless/malicious participant) `A` becomes a shareholder in a `staking_contract` (e.g., as staker/operator) or a `vesting_contract`, without ever registering a legacy `CoinStore<AptosCoin>` (the default post-FA-migration state).
2. `A` calls `aptos_account::set_allow_direct_coin_transfers(A_signer, false)` — a normal, permissionless self-call:
   `aptos-move/framework/aptos-framework/sources/aptos_account.move:188-219`
3. Rewards accrue and become unlockable/withdrawable in the shared stake pool for `A` and other stakers/shareholders `B`, `C`, ...
4. Anyone calls `staking_contract::distribute(staker, operator)` (or `vesting::distribute(contract_address)`).
5. The internal loop reaches `A`'s pending distribution and calls `aptos_account::deposit_coins(A, ...)`, which aborts (`EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`) because `A` is unregistered and opted out.
6. The entire transaction reverts — `B`, `C`, and the operator's commission remain undistributed. Every subsequent call to `distribute` fails the same way as long as `A` remains a shareholder with a nonzero pending distribution, permanently trapping value for all co-participants.

### Citations

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

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L233-259)
```text
    public(friend) fun register_apt(account_signer: &signer) {
        ensure_primary_fungible_store_exists(signer::address_of(account_signer));
    }

    /// APT Primary Fungible Store specific specialized functions,
    /// Utilized internally once migration of APT to FungibleAsset is complete.

    /// Convenient function to transfer APT to a recipient account that might not exist.
    /// This would create the recipient APT PFS first, which also registers it to receive APT, before transferring.
    /// TODO: once migration is complete, rename to just "transfer_only" and make it an entry function (for cheapest way
    /// to transfer APT) - if we want to allow APT PFS without account itself
    public(friend) entry fun fungible_transfer_only(
        source: &signer, to: address, amount: u64
    ) {
        let sender_store =
            ensure_primary_fungible_store_exists(signer::address_of(source));
        let recipient_store = ensure_primary_fungible_store_exists(to);

        // use internal APIs, as they skip:
        // - owner, frozen and dispatchable checks
        // as APT cannot be frozen or have dispatch, and PFS cannot be transfered
        // (PFS could potentially be burned. regular transfer would permanently unburn the store.
        // Ignoring the check here has the equivalent of unburning, transfers, and then burning again)
        fungible_asset::unchecked_deposit(
            recipient_store, fungible_asset::unchecked_withdraw(sender_store, amount)
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L840-920)
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
