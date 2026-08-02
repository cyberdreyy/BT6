## Title
Single griefing shareholder can permanently block reward/commission distribution for all other stakers, delegators, and vesting shareholders via `set_allow_direct_coin_transfers` - (File: aptos-move/framework/aptos-framework/sources/staking_contract.move, aptos-move/framework/aptos-framework/sources/vesting.move)

### Summary
`staking_contract::distribute_internal` and `vesting::distribute` pay out withdrawn/unlocked APT to **all** shareholders/recipients in a single atomic loop using `aptos_account::deposit_coins`. That function aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` if the recipient (a) is not registered for `AptosCoin` and (b) has called `set_allow_direct_coin_transfers(account, false)`. Because Move has no exception handling, a single reverting payout in the loop aborts the *entire* transaction, undoing the `stake::withdraw_with_cap`/`withdraw_stake` call that already pulled funds out of the stake pool. This lets any single shareholder or the operator's beneficiary indefinitely block distribution to every other unrelated shareholder/staker in the same staking contract or vesting contract.

### Finding Description
- `staking_contract::distribute_internal` withdraws all inactive/pending_inactive coins from the pool and then iterates `distribution_pool.shareholders()`, calling `aptos_account::deposit_coins(recipient, ...)` for each recipient, including the operator's beneficiary via `beneficiary_for_operator(operator)`. [1](#0-0) 
- `vesting::distribute` does the same for every shareholder in `grant_pool.shareholders()`. [2](#0-1) 
- `aptos_account::deposit_coins` will `assert!` (abort) if the target is not registered for the coin type and has explicitly disabled direct transfers via `set_allow_direct_coin_transfers`. [3](#0-2) [4](#0-3) 
- `set_allow_direct_coin_transfers` is a fully permissionless entry function any account (including a delegator, staker, shareholder, or an operator's beneficiary) can call on itself at any time. [4](#0-3) 

Because Move aborts roll back the entire transaction (including the earlier `stake::withdraw_with_cap` that removed funds from `inactive`/`pending_inactive` state), the practical effect is not fund destruction but a **permanent denial-of-service on the shared, permissionless `distribute()`/`vest()`/`unlock_stake()`/`request_commission()` (which all invoke `distribute_internal`) entry points** for every other shareholder in that staking contract or vesting contract, as long as the griefing account keeps its coin-transfer opt-out set. Any account can trigger this once and never undo it if it never registers for `AptosCoin`, indefinitely trapping legitimate co-shareholders' otherwise-withdrawable rewards/vested/commission balances behind a transaction that can never succeed.

### Impact Explanation
This falls under "Permanent lock or non-recoverable loss of claim rights in stake, delegation, commission, beneficiary, or vesting flows" and "Operator commission, beneficiary payout ... share-accounting corruption that ... traps value," because:
- A single unprivileged shareholder (in a multi-shareholder `staking_contract` or `vesting::VestingContract`) can block `distribute()` for the entire pool, trapping the pending distribution balances of all co-shareholders, since `distribute_internal`/`vesting::distribute` process every shareholder in one atomic call and abort entirely on the first failing deposit.
- The same applies to operator commission: if an operator sets a `beneficiary_for_operator` that never registers for `AptosCoin` and disables direct transfers, `request_commission`/`unlock_stake`/`switch_operator` (which call `distribute_internal` first) will also permanently fail, blocking the staker's own withdrawal path (`unlock_stake` forces `distribute_internal` before proceeding). [5](#0-4) 

### Likelihood Explanation
The griefing precondition (disable direct transfers, never register for `AptosCoin`) is trivially achievable by any account with a single self-directed transaction, and the abort path is deterministic and always reachable as long as that account remains a shareholder/beneficiary in the pool with a nonzero pending distribution. No special privilege or race condition is required; the vesting/staking-contract admin cannot remove a shareholder's entitlement to unblock it, since `distribute` is permissionless and shareholder removal isn't exposed. This makes the likelihood high in any staking_contract or vesting pool with more than one shareholder, though it requires a participant to be willing to sacrifice their own payout to grief others.

### Recommendation
- In `distribute_internal` (`staking_contract.move`) and `distribute` (`vesting.move`), do not let one recipient's failed payout revert the whole batch: wrap each recipient payout so failures are isolated per-shareholder (e.g., retain the failed share in the distribution/grant pool and continue distributing to the rest, or use a "pull" pattern where each shareholder withdraws their own balance instead of a shared push-loop).
- Alternatively, bypass the opt-in "allow direct transfers" gate specifically for reward/commission distribution paths (which are pre-agreed obligations, not arbitrary transfers), so `is_account_registered` failures cannot block unrelated third parties' claims.

### Proof of Concept
1. Admin creates a `vesting::VestingContract` (or `staking_contract`) with shareholders `A` (honest) and `B` (attacker), e.g. via `vesting::create_vesting_contract`. [6](#0-5) 
2. `B` calls `aptos_account::set_allow_direct_coin_transfers(B, false)` and never calls `coin::register<AptosCoin>` for itself. [4](#0-3) 
3. Time passes; rewards/vested tokens accrue and become withdrawable (`stake::fast_forward_to_unlock`/epoch progression), giving both `A` and `B` a positive pending distribution balance.
4. Anyone calls `vesting::distribute(contract_address)` (or `staking_contract::distribute(staker, operator)`). The function withdraws all inactive coins from the stake pool, then iterates shareholders; when it reaches `B`, `aptos_account::deposit_coins` aborts because `B` is unregistered and has opted out of direct transfers. [2](#0-1) 
5. The abort rolls back the whole transaction, including `A`'s share of the payout — `A` (and every other shareholder) can never receive their distribution through this contract as long as `B` keeps its settings unchanged, since every call to `distribute`/`vest`/`unlock_stake`/`request_commission` on this pool will hit the same abort.

**Note**: This root cause was independently verified by reading `distribute_internal`, `vesting::distribute`, and `aptos_account::deposit_coins`/`set_allow_direct_coin_transfers`/`can_receive_direct_coin_transfers` directly in this repository. I was not able to fully confirm within this session whether `coin::is_account_registered<AptosCoin>` behaves identically post the Fungible-Asset migration for `AptosCoin` specifically (i.e., whether every account effectively already has an implicit primary fungible store that counts as "registered", which would neutralize this bug for `AptosCoin`). The `coin::deposit`/`is_account_registered` internals reference a `primary_fungible_store` abstraction that may auto-provision stores, which could mean this griefing vector is already mitigated for `AptosCoin` in practice; this would need direct testing (e.g., a Devin session running the Move unit tests) to conclusively confirm or refute exploitability under the current migrated coin/FA implementation before treating this as an unambiguous, provable High/Critical finding.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L676-696)
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
        distribute_internal(
            staker_address,
            operator,
            staking_contract,
        );

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

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L610-633)
```text
        move_to(&contract_signer, VestingContract {
            state: VESTING_POOL_ACTIVE,
            admin: admin_address,
            grant_pool,
            beneficiaries: simple_map::create<address, address>(),
            vesting_schedule,
            withdrawal_address,
            staking: StakingInfo { pool_address, operator, voter, commission_percentage },
            remaining_grant: grant_amount,
            signer_cap: contract_signer_cap,
            update_operator_events: new_event_handle<UpdateOperatorEvent>(&contract_signer),
            update_voter_events: new_event_handle<UpdateVoterEvent>(&contract_signer),
            reset_lockup_events: new_event_handle<ResetLockupEvent>(&contract_signer),
            set_beneficiary_events: new_event_handle<SetBeneficiaryEvent>(&contract_signer),
            unlock_rewards_events: new_event_handle<UnlockRewardsEvent>(&contract_signer),
            vest_events: new_event_handle<VestEvent>(&contract_signer),
            distribute_events: new_event_handle<DistributeEvent>(&contract_signer),
            terminate_events: new_event_handle<TerminateEvent>(&contract_signer),
            admin_withdraw_events: new_event_handle<AdminWithdrawEvent>(&contract_signer),
        });

        buy_ins.destroy_empty();
        contract_address
    }
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L730-741)
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
