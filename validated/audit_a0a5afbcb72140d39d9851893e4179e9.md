### Title
Single uncooperative shareholder can permanently freeze vested/commission fund distribution for all other shareholders in a vesting or staking contract - ([File: aptos-move/framework/aptos-framework/sources/vesting.move], [File: aptos-move/framework/aptos-framework/sources/staking_contract.move])

### Summary
`vesting::distribute` and `staking_contract::distribute_internal` both iterate over every shareholder of a shared pool in a single atomic loop and pay each one out with `aptos_account::deposit_coins`. That call reverts if the recipient already exists, is not registered for `AptosCoin`, and has opted out of direct coin transfers via `set_allow_direct_coin_transfers(false)`. Because none of the affected entry functions catch or skip a failing recipient, one shareholder who arranges their account into this state can make the whole distribution transaction abort forever, permanently freezing every other shareholder's vested funds or commission — the same class of defect as the external report's `payable.transfer()` hard-gas-limit failure blocking withdrawals for smart-contract recipients.

### Finding Description
`vesting::create_vesting_contract` only checks that the `withdrawal_address` is registered for APT: [1](#0-0) . It never verifies that the `shareholders` themselves are registered for `AptosCoin`, and there is no function anywhere in `vesting.move` that removes a shareholder from `grant_pool` after creation.

`vesting::distribute` is a permissionless `public entry fun` that can be invoked by anyone at any time to flush withdrawable stake to shareholders: [2](#0-1) . It loops over `grant_pool.shareholders()` and calls `aptos_account::deposit_coins(recipient_address, share_of_coins)` for each one in the same transaction, with no per-recipient failure handling.

`aptos_account::deposit_coins` aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` when the target account exists, is not registered for the coin type, and has not opted in to direct transfers: [3](#0-2) . Any account can flip this opt-in flag at will via `set_allow_direct_coin_transfers`: [4](#0-3) .

The identical pattern exists in `staking_contract::distribute_internal`, which pays out every shareholder of `staking_contract.distribution_pool` (staker, operator/beneficiary) via `aptos_account::deposit_coins` inside one atomic `while` loop with no skip-on-failure logic: [5](#0-4) .

Because `set_beneficiary` only requires the *new* beneficiary to be pre-registered (not the shareholder's default self-beneficiary): [6](#0-5) , a shareholder added to a vesting pool without ever calling `set_beneficiary` keeps themselves as the default recipient with no registration guarantee.

### Impact Explanation
Since `distribute`/`distribute_internal` process every shareholder atomically and revert entirely on the first failing deposit, one uncooperative (or malicious) shareholder can permanently strand every other co-shareholder's vested principal, rewards, and operator commission in the underlying stake pool. There is no admin path to remove or skip a bad shareholder from `grant_pool`/`distribution_pool`, so the freeze is non-recoverable through in-scope framework functions — matching "Permanent lock or non-recoverable loss of claim rights in stake, delegation, commission, beneficiary, or vesting flows."

### Likelihood Explanation
The precondition (an account existing, unregistered for `AptosCoin`, and `allow_arbitrary_coin_transfers == false`) is fully attacker-controlled and requires no privileged role — any address can be listed as a `vesting` shareholder or become part of a `staking_contract` payout, and calling `set_allow_direct_coin_transfers(false)` is a normal unprivileged entry function. The remaining uncertainty is how frequently real deployments end up with such shareholders unregistered for legacy `CoinStore<AptosCoin>` under the current coin/FA migration state, which could not be fully confirmed from the indexed code; this affects exploitation likelihood but not the structural absence of per-recipient failure isolation.

### Recommendation
Make distribution to each shareholder failure-isolated: wrap each `aptos_account::deposit_coins` call so a failing recipient's share is retained/queued (e.g., left in the pool or redirected to a claimable holding balance) rather than aborting the whole batch, and/or require every shareholder to be pre-registered for `AptosCoin` at `create_vesting_contract`/`staking_contract` creation time, consistent with the existing `assert_account_is_registered_for_apt` check already used for `withdrawal_address` and `set_beneficiary`.

### Proof of Concept
1. Attacker account `A` (never registered for `CoinStore<AptosCoin>`) calls `aptos_account::set_allow_direct_coin_transfers(A, false)`.
2. Admin creates a `vesting_contract` with shareholders `[A, B, C]` via `create_vesting_contract` — no registration check is performed on `A`, `B`, or `C` (only on `withdrawal_address`).
3. Time passes; `vest()`/`vest_many()` accrue distributable stake in the pool.
4. Anyone calls `distribute(contract_address)`. The loop reaches shareholder `A`, calls `aptos_account::deposit_coins(A, ...)`, which hits `!coin::is_account_registered<AptosCoin>(A)` and `can_receive_direct_coin_transfers(A) == false`, aborting with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`.
5. The entire transaction reverts — `B` and `C` never receive their vested funds, and since `A` cannot be removed from `grant_pool`, every future call to `distribute` fails the same way, permanently freezing `B` and `C`'s claims.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L549-558)
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
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L719-747)
```text
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
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L915-924)
```text
    public entry fun set_beneficiary(
        admin: &signer,
        contract_address: address,
        shareholder: address,
        new_beneficiary: address,
    ) acquires VestingContract {
        // Verify that the beneficiary account is set up to receive APT. This is a requirement so distribute() wouldn't
        // fail and block all other accounts from receiving APT if one beneficiary is not registered.
        assert_account_is_registered_for_apt(new_beneficiary);

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

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L188-212)
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
