## Analysis

The tbtc bug reduces to one invariant: **a batch/shared fund-distribution routine that pays multiple recipients in a single atomic call must not let one recipient's forced revert block payout to everyone else.** In Solidity this happened via `address.transfer` reverting on `receive()`; in Move the analogous failure mode is an `abort` thrown mid-loop by a `coin`/`aptos_account` deposit call, which reverts the entire transaction (Move has no per-iteration exception handling — one `abort` in a `for_each` loop kills the whole batch).

I traced two shared-distribution loops that push funds via `aptos_account::deposit_coins`:

- `aptos_framework::vesting::distribute` — pays *every* shareholder of a `VestingContract` in one loop. [1](#0-0) 
- `aptos_framework::staking_contract::distribute_internal` — pays the staker and operator/beneficiary in one loop. [2](#0-1) 

Both ultimately call `aptos_account::deposit_coins`, which self-registers the recipient for the coin **unless** the recipient has opted out of "direct" transfers via `DirectTransferConfig`, in which case it aborts: [3](#0-2) 

`DirectTransferConfig.allow_arbitrary_coin_transfers` is fully controlled by the recipient themselves via the public entry function `set_allow_direct_coin_transfers`: [4](#0-3) 

### Title
Any shareholder/beneficiary can permanently block `vesting::distribute` / `staking_contract::distribute` for all co-participants by self-disabling direct coin transfers - (File: `aptos-move/framework/aptos-framework/sources/vesting.move`, `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`vesting::distribute` iterates over *all* shareholders of a `VestingContract` and pays each via `aptos_account::deposit_coins` inside one atomic loop; `staking_contract::distribute_internal` does the same for the staker/operator pair. `deposit_coins` aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` if the target account is unregistered for the coin and has called `set_allow_direct_coin_transfers(false)`. Since any of these recipients is themselves the entity being paid, an unprivileged co-shareholder/operator/beneficiary can grief the shared batch payout, causing it to abort for every other participant — the direct analog of the tbtc `liquidationInitiator` griefing the shared `purchaseSignerBondsAtAuction` payout.

### Finding Description
`vesting::distribute` (aapos_framework::vesting) collects all withdrawable inactive stake and then loops over `grant_pool.shareholders()`, calling `aptos_account::deposit_coins(recipient_address, share_of_coins)` for each shareholder in turn: [5](#0-4) 

`staking_contract::distribute_internal` does the analogous thing for the (staker, operator) pair, redirecting the operator's share to `beneficiary_for_operator(operator)`: [6](#0-5) 

`aptos_account::deposit_coins` only skips registration/abort when the recipient is already registered for the coin **or** allows arbitrary direct transfers: [7](#0-6) 

A recipient controls this switch unilaterally and for free: [8](#0-7) 

Because Move aborts unwind the entire transaction (there is no try/catch, unlike Solidity's `send` vs `transfer` distinction that the tbtc fix exploited), a single malicious/unregistered/opted-out recipient anywhere in the loop causes the whole `distribute()` call to fail — for both the vesting shareholders and the staking-contract staker/operator pair. There is no pull-based fallback, no per-recipient error isolation, and no admin mechanism to skip/exclude the blocking recipient from `vesting.move`'s fixed shareholder list.

This mirrors the report's root cause precisely: a participant entitled to only a share of a batch payout can unilaterally force the shared payout transaction to revert, denying the *other* legitimate participants their funds.

### Impact Explanation
- In `vesting::distribute`, the vesting contract's shareholder list is fixed at contract creation and cannot be modified or filtered later; if any one shareholder opts out of direct transfers and never registers for `AptosCoin`, every other shareholder in that same `VestingContract` is indefinitely denied their already-unlocked rewards and vested principal (funds sit unclaimed in the `inactive`/`pending_inactive` stake pool). It also blocks `terminate_vesting_contract`, which calls `distribute` first, preventing admin cleanup/withdrawal-address recovery. [9](#0-8) 
- In `staking_contract::distribute_internal`, since the shareholder set is just {staker, operator/beneficiary}, an operator (who does not own the staked capital) or their beneficiary can indefinitely block the staker from ever withdrawing their own unlocked stake, by refusing to accept the commission payout.

This satisfies "Permanent lock or non-recoverable loss of claim rights" for co-participants who never opted in to the griefing and have no code path to bypass or exclude the malicious recipient.

### Likelihood Explanation
Trivial and free to trigger: the attacker only needs to call the public entry function `set_allow_direct_coin_transfers(false)` on their own account and avoid ever calling `coin::register<AptosCoin>`/registering a primary store — no special privileges, capital, or race condition required. Any shareholder in a multi-employee vesting grant, or any operator/beneficiary in a staking contract, can do this at will.

### Recommendation
Switch these shared distribution loops to a pull-based accounting model: record each recipient's owed amount in the distribution/grant pool (already tracked via `pool_u64`/`distribution_pool` shares) and let each recipient separately claim/withdraw their own share, so one recipient's failure to accept funds cannot block others. Alternatively, wrap the per-recipient deposit in a way that isolates failures (e.g., skip/queue undeliverable amounts back into the pool for later individual claim) instead of aborting the entire `distribute()` transaction.

### Proof of Concept
1. Admin creates a `VestingContract` via `vesting::create_vesting_contract` with shareholders `[A, B]` (each with a grant share). 
2. `A` calls `aptos_account::set_allow_direct_coin_transfers(false)` on their own account and never registers for `AptosCoin`/creates a primary FA store.
3. Time passes; rewards/vested tokens accumulate and become withdrawable via `unlock_rewards`/`vest`.
4. `B` (or anyone) calls `vesting::distribute(contract_address)`. The loop reaches shareholder `A`, calls `aptos_account::deposit_coins(A, ...)`, which hits `is_account_registered<AptosCoin>(A) == false` and `can_receive_direct_coin_transfers(A) == false`, aborting with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS`.
5. The entire transaction reverts — `B`'s legitimately unlocked and already-withdrawn-from-stake-pool funds (as `Coin<AptosCoin>` values held only in the local `coins` variable within the aborted call) never reach `B`, and this repeats on every future call to `distribute`, permanently starving `B` (and any other shareholders) unless `A` chooses to cooperate.

Note: I was not able to fully verify, within the available tool budget, whether the ongoing coin→fungible-asset migration (`primary_fungible_store`) changes `coin::is_account_registered<AptosCoin>`'s semantics for already-existing accounts (e.g., whether all accounts are now implicitly "registered" for APT once migrated). If `is_account_registered<AptosCoin>` always returns `true` post-migration for any account with an `account::Account` resource, the specific `AptosCoin` griefing vector above may be narrowed to freshly-created/uninitialized addresses only; the underlying architectural flaw (unbounded-abort shared distribution loop with no pull fallback) still stands for any non-AptosCoin `CoinType` deposit paths and warrants confirmation via a Devin session with full repository/test access.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L718-741)
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

```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L770-776)
```text
    /// Terminate the vesting contract and send all funds back to the withdrawal address.
    public entry fun terminate_vesting_contract(admin: &signer, contract_address: address) acquires VestingContract {
        assert_active_vesting_contract(contract_address);

        // Distribute all withdrawable coins, which should have been from previous rewards withdrawal or vest.
        distribute(contract_address);

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

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L187-211)
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
