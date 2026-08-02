## Analysis

The external report's bug class reduces to: **an unprivileged, per-entry failure inside a loop/batch operation is not isolated, so a single failing entry causes the entire atomic operation to revert, blocking legitimate entries that would have otherwise succeeded** (Solidity per-entry revert / `aggregate3(allowFailure:false)` / uncaught throw).

The Aptos-native analog exists in `vesting::distribute` (and structurally the same pattern in `staking_contract::distribute_internal`), where a shareholder can permissionlessly opt out of un-registered direct coin transfers via `aptos_account::set_allow_direct_coin_transfers`, causing `aptos_account::deposit_coins` to abort for that one entry — and since Move transactions have no try/catch, this aborts the **entire** distribution loop, permanently blocking payout to every other legitimate shareholder.

### Title
Unregistered shareholder with disabled direct-coin-transfers permanently blocks `vesting::distribute` for all co-shareholders - (File: `aptos-move/framework/aptos-framework/sources/vesting.move`)

### Summary
`vesting::distribute` iterates over every shareholder of a `VestingContract` in a single atomic loop and calls `aptos_account::deposit_coins` for each recipient [1](#0-0) . `aptos_account::deposit_coins` aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` if the recipient is not registered for the `CoinType` and has disabled direct coin transfers [2](#0-1) . Any account can permissionlessly disable direct transfers on itself via `set_allow_direct_coin_transfers(false)` [3](#0-2) . Because `create_vesting_contract` only validates that the `withdrawal_address` is registered for APT — never the shareholders — a shareholder address can enter the pool completely unregistered [4](#0-3) . Since Move has no per-item exception handling, the single failing deposit reverts the whole `distribute` transaction, exactly mirroring the "single bad entry halts whole batch" bug class from the external report.

### Finding Description
1. `vesting::create_vesting_contract` requires `assert_account_is_registered_for_apt(withdrawal_address)` but performs no such check on any `shareholders` address [5](#0-4) .
2. Any shareholder (or anyone controlling an address later designated as a shareholder/beneficiary) can call the fully permissionless `aptos_account::set_allow_direct_coin_transfers(&signer, false)` before ever registering a `CoinStore<AptosCoin>` for themselves [3](#0-2) .
3. When `vesting::distribute` runs, it loops `shareholders.for_each_ref` and calls `aptos_account::deposit_coins(recipient_address, share_of_coins)` for every shareholder in one Move transaction, with no isolation between iterations [1](#0-0) .
4. `deposit_coins` will `assert!(can_receive_direct_coin_transfers(to), ...)` and abort if `to` is unregistered and has opted out [6](#0-5) .
5. Because Move aborts unwind the entire transaction, this single failing deposit reverts distribution for every other (unrelated, fully compliant) shareholder in the same vesting contract, indefinitely — until the offending party re-enables transfers or registers, which they have no incentive to do (or may be unable to, e.g., burned key). The `vesting.spec.move` file even flags this exact gap: `// TODO: Can't handle abort in loop.` on `distribute` and `distribute_many` [7](#0-6) .

The identical structural weakness exists in `staking_contract::distribute_internal`'s `while (distribution_pool.shareholders_count() > 0)` loop, which also calls `aptos_account::deposit_coins` per recipient without isolation [8](#0-7) , so the same griefing applies to staker/operator/beneficiary commission distributions.

### Impact Explanation
This traps rightful vested stake and commission payouts belonging to all co-shareholders of the affected vesting contract (or staking contract) — a permanent lock / non-recoverable-until-cooperation loss of claim rights in the vesting/commission distribution flow, matching the required impact category "Permanent lock or non-recoverable loss of claim rights in stake, delegation, commission, beneficiary, or vesting flows." Since `distribute` can be invoked by anyone repeatedly and will keep reverting, the pool's withdrawable stake becomes permanently stuck as long as the griefing shareholder's account state is unchanged.

### Likelihood Explanation
The precondition (an address that never registers `CoinStore<AptosCoin>` and calls `set_allow_direct_coin_transfers(false)`) is fully permissionless and requires no privileged role or already-held authority — any of the vesting contract's designated shareholders (assigned by the admin at contract creation, a normal and expected role) can trigger it unilaterally, and there is no validation at contract-creation time or at distribution time to prevent or route around it.

### Recommendation
- Mirror the report's recommended fix pattern: make the shareholder payout loop resilient per entry instead of all-or-nothing. Wrap each `aptos_account::deposit_coins` call so a failing recipient is skipped (e.g., detect `!coin::is_account_registered<CoinType>(recipient) && !can_receive_direct_coin_transfers(recipient)` up front and route that shareholder's funds to an escrow/claimable balance instead of calling `deposit_coins` directly), emitting an event for the skipped recipient.
- Alternatively, require `assert_account_is_registered_for_apt` for every shareholder (not just `withdrawal_address`) in `create_vesting_contract`, and additionally require registration in `staking_contract` creation flows.
- For `staking_contract::distribute_internal`, apply the same fallback: if `deposit_coins` would fail, retain the recipient's shares/credit in the distribution pool for a separate pull-based `claim` rather than aborting the whole `distribute` transaction.

### Proof of Concept
1. Admin creates a vesting contract with shareholders `[victim_A, griefer_B]`, only `withdrawal_address` is checked for registration; `griefer_B` is never required to be registered for APT.
2. Before any `vest`/`distribute` call, `griefer_B` (owner of their own address, fully permissionless) calls `aptos_account::set_allow_direct_coin_transfers(griefer_B_signer, false)` without ever calling `coin::register<AptosCoin>()`.
3. Time passes, vesting accrues, anyone calls `vesting::vest(contract_address)` then `vesting::distribute(contract_address)`.
4. In `distribute`, the loop reaches `griefer_B`: `coin::is_account_registered<AptosCoin>(griefer_B) == false` and `can_receive_direct_coin_transfers(griefer_B) == false`, so `aptos_account::deposit_coins` hits `assert!(can_receive_direct_coin_transfers(to), ...)` and aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`.
5. The whole `distribute` transaction reverts. `victim_A`'s already-vested share is not paid despite being fully valid; every subsequent call to `distribute` for this contract reverts identically until `griefer_B` re-enables direct transfers or registers — which `griefer_B` has no obligation or incentive to do, permanently freezing `victim_A`'s vested funds in the shared pool. [9](#0-8) [10](#0-9)

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

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L718-748)
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

```

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L109-131)
```text
    /// Convenient function to deposit a custom CoinType into a recipient account that might not exist.
    /// This would create the recipient account first and register it to receive the CoinType, before transferring.
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

**File:** aptos-move/framework/aptos-framework/sources/vesting.spec.move (L307-320)
```text
    spec distribute(contract_address: address) {
        // TODO: Can't handle abort in loop.
        pragma verify = false;
        include ActiveVestingContractAbortsIf;

        let vesting_contract = global<VestingContract>(contract_address);
        include WithdrawStakeAbortsIf { vesting_contract };
    }

    spec distribute_many(contract_addresses: vector<address>) {
        // TODO: Calls `distribute` in loop.
        pragma verify = false;
        aborts_if len(contract_addresses) == 0;
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
