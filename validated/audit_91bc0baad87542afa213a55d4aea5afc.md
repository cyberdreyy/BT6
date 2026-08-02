## Finding

### Title
Unregistered/opted-out beneficiary permanently blocks `distribute_internal`, freezing staker principal and operator commission - ([File: aptos-move/framework/aptos-framework/sources/staking_contract.move])

### Summary
`staking_contract::distribute_internal` iterates over **every** shareholder currently queued in a `StakingContract`'s `distribution_pool` inside a single atomic `while` loop, calling `aptos_account::deposit_coins` for each recipient in turn [1](#0-0) . `deposit_coins` aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` if the recipient is unregistered for the coin and has explicitly opted out of direct transfers via `set_allow_direct_coin_transfers(false)` [2](#0-1) [3](#0-2) . Because Move aborts roll back the *entire* transaction, including the `redeem_shares` calls that already ran earlier in the loop for other, perfectly valid recipients, a single "poisoned" recipient in the distribution pool makes `distribute_internal` un-callable forever for that staker/operator pair.

### Finding Description
`distribute_internal` is not a standalone entry point — it is invoked as the first step of essentially every staking_contract mutation: `request_commission`, `unlock_stake`, `switch_operator`, and the public `distribute` entry function all call it before doing their own work [4](#0-3) [5](#0-4) . This means that once one recipient in the pool cannot receive APT, none of the staker's principal, none of the accrued rewards, and none of the operator's commission for that pool can ever be withdrawn again, because every call path that would flush the `distribution_pool` reaches the same aborting `deposit_coins` call.

The vesting module explicitly recognized and defended against this exact bug class: `vesting::set_beneficiary` calls `assert_account_is_registered_for_apt(new_beneficiary)` specifically "so `distribute()` wouldn't fail and block all other accounts from receiving APT if one beneficiary is not registered" [6](#0-5) . `staking_contract.move`'s equivalents — `set_beneficiary_for_operator` and `switch_operator` (which changes the operator whose beneficiary receives future commissions) — do not perform the analogous registration check before that address is later pushed into `distribution_pool` by `add_distribution`/`request_commission_internal` [7](#0-6) [8](#0-7) .

Any of the following unprivileged actors can create the poisoned state:
- The operator (or an operator-designated beneficiary) can call `aptos_account::set_allow_direct_coin_transfers(false)` on their own beneficiary/operator account *after* becoming an operator on a staking contract but *before* ever registering for `AptosCoin`, and before the next `request_commission`/`distribute` call adds them to `distribution_pool`.
- A staker calling `switch_operator` to a new operator whose beneficiary/account is unregistered and opted out likewise seeds a doomed recipient into the pool.

Once `add_distribution` records that address with a nonzero share and the pool tries to pay it out, `distribute_internal`'s `while` loop reaches that recipient's `deposit_coins` call and aborts — permanently, since the recipient's on-chain opt-out state does not change on its own.

### Impact Explanation
This traps **all** value under that `StakingContract`: the staker's active/principal stake, all accrued rewards, and the operator's pending commission become permanently non-withdrawable, because every entry point that could resolve or bypass the stuck distribution first executes the same reverting `distribute_internal` logic. This is a "permanent lock or non-recoverable loss of claim rights in stake, delegation, commission, beneficiary, or vesting flows," matching the required impact bar, and requires no privileged role — only participation as an operator/beneficiary or a staker's own `switch_operator` call.

### Likelihood Explanation
Likelihood is moderate-to-high: the precondition (calling `set_allow_direct_coin_transfers(false)` while unregistered for `AptosCoin`) is a single, ordinary, permissionless transaction available to any account, and operators/beneficiaries have every incentive to control this setting for other purposes (e.g., avoiding unwanted airdropped coins) without realizing it can retroactively brick their own staking_contract payouts. No governance or admin cooperation is needed to trigger or to recover.

### Recommendation
Mirror the `vesting.move` protection in `staking_contract.move`: require `aptos_account::assert_account_is_registered_for_apt` (or an equivalent "can safely receive APT" check) on the beneficiary/operator address in `set_beneficiary_for_operator` and `switch_operator` before it can become a `distribution_pool` recipient. Additionally, harden `distribute_internal`'s loop so that a single recipient's failed deposit does not abort the whole distribution — e.g., skip/requeue a recipient that cannot currently receive funds (falling back to `coin::deposit` semantics that don't require opt-in, or holding the failed share aside) rather than reverting the entire batch.

### Proof of Concept
Not independently executed in this review (no test harness run); the trace is derived directly from reading the cited code paths:
1. Operator `O` creates/joins a `StakingContract` for staker `S` (any `create_staking_contract` flow).
2. `O` calls `aptos_account::set_allow_direct_coin_transfers(O_or_beneficiary, false)` without ever registering for `AptosCoin` at that address [3](#0-2) .
3. Rewards accrue; anyone calls `request_commission` (or `unlock_stake`/`distribute`), which calls `request_commission_internal` → `add_distribution(operator, ...)`, inserting `O`'s (unregistered, opted-out) address into `distribution_pool` [7](#0-6) .
4. Once the lockup expires and someone calls `distribute`, `distribute_internal`'s loop reaches `O`'s recipient entry and `aptos_account::deposit_coins` aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` [1](#0-0) .
5. Every subsequent call to `unlock_stake`, `request_commission`, `switch_operator`, or `distribute` for that staker/operator pair re-executes the same failing loop and aborts, permanently freezing the staker's principal and all future rewards/commission in that `StakingContract`.

**Caveat / uncertainty**: I could not fully view the body of `staking_contract::set_beneficiary_for_operator` (only its doc comment at lines 807-810) or confirm at runtime whether `AptosCoin`, now migrated to the `FungibleAsset` framework, actually routes through the `coin::is_account_registered`/`can_receive_direct_coin_transfers` check shown at `aptos_account.move:121-130`, or through a different fungible-asset deposit path that might auto-create/register the recipient and bypass the opt-out abort. This distinction is decisive for whether the described DoS is exploitable for `AptosCoin` specifically. I recommend a Devin session with full build/test access to trace `aptos_account::deposit_coins<AptosCoin>`'s exact runtime path and to run a Move unit test reproducing the described `distribute_internal` abort before treating this as confirmed.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L607-635)
```text
    public entry fun request_commission(
        account: &signer, staker: address, operator: address
    ) acquires Store, BeneficiaryForOperator {
        let account_addr = signer::address_of(account);
        assert!(
            account_addr == staker
                || account_addr == operator
                || account_addr == beneficiary_for_operator(operator),
            error::unauthenticated(ENOT_STAKER_OR_OPERATOR_OR_BENEFICIARY)
        );
        assert_staking_contract_exists(staker, operator);

        let store = borrow_global_mut<Store>(staker);
        let staking_contract = store.staking_contracts.borrow_mut(&operator);
        // Short-circuit if zero commission.
        if (staking_contract.commission_percentage == 0) { return };

        // Force distribution of any already inactive stake.
        distribute_internal(
            staker,
            operator,
            staking_contract,
        );

        request_commission_internal(
            operator,
            staking_contract,
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L637-674)
```text
    fun request_commission_internal(
        operator: address,
        staking_contract: &mut StakingContract,
    ): u64 {
        // Unlock just the commission portion from the stake pool.
        let (total_active_stake, accumulated_rewards, commission_amount) =
            get_staking_contract_amounts_internal(staking_contract);
        staking_contract.principal = total_active_stake - commission_amount;

        // Short-circuit if there's no commission to pay.
        if (commission_amount == 0) {
            return 0
        };

        // Add a distribution for the operator.
        add_distribution(
            operator,
            staking_contract,
            operator,
            commission_amount
        );

        // Request to unlock the commission from the stake pool.
        // This won't become fully unlocked until the stake pool's lockup expires.
        stake::unlock_with_cap(commission_amount, &staking_contract.owner_cap);

        let pool_address = staking_contract.pool_address;
        emit(
            RequestCommission {
                operator,
                pool_address,
                accumulated_rewards,
                commission_amount
            }
        );

        commission_amount
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L676-704)
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

        // For simplicity, we request commission to be paid out first. This avoids having to ensure to staker doesn't
        // withdraw into the commission portion.
        let commission_paid =
            request_commission_internal(
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L937-957)
```text
    /// Add a new distribution for `recipient` and `amount` to the staking contract's distributions list.
    fun add_distribution(
        operator: address,
        staking_contract: &mut StakingContract,
        recipient: address,
        coins_amount: u64,
    ) {
        let distribution_pool = &mut staking_contract.distribution_pool;
        let (_, _, _, total_distribution_amount) =
            stake::get_stake(staking_contract.pool_address);
        update_distribution_pool(
            distribution_pool,
            total_distribution_amount,
            operator,
            staking_contract.commission_percentage
        );

        distribution_pool.buy_in(recipient, coins_amount);
        let pool_address = staking_contract.pool_address;
        emit(AddDistribution { operator, pool_address, amount: coins_amount });
    }
```

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L121-130)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L915-935)
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

        let vesting_contract = borrow_global_mut<VestingContract>(contract_address);
        verify_admin(admin, vesting_contract);

        let old_beneficiary = get_beneficiary(vesting_contract, shareholder);
        let beneficiaries = &mut vesting_contract.beneficiaries;
        if (beneficiaries.contains_key(&shareholder)) {
            let beneficiary = beneficiaries.borrow_mut(&shareholder);
            *beneficiary = new_beneficiary;
        } else {
            beneficiaries.add(shareholder, new_beneficiary);
        };
```
