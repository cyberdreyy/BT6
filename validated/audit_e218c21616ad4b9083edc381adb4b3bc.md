### Title
Operator-controlled beneficiary can permanently block a staker's `unlock_stake`/`switch_operator`/`request_commission` via `deposit_coins` abort in `distribute_internal` - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`staking_contract.move`'s `distribute_internal` pays out **every** pending shareholder in the `distribution_pool` (staker principal/rewards and operator commission) in a single loop, and every payout goes through `aptos_account::deposit_coins`, which can abort. Because the operator's commission recipient is dynamically resolved to `beneficiary_for_operator(operator)` at distribution time, an operator can point their beneficiary at an address that is not registered for `AptosCoin` and has explicitly opted out of direct coin transfers, causing `deposit_coins` to abort for that single recipient — and since it happens inside the same shared loop/transaction, it aborts the *entire* `distribute_internal` call, blocking the staker's own unlock/withdraw/switch operations too.

### Finding Description
`distribute_internal` [1](#0-0)  is invoked from `unlock_stake`, `request_commission`, `update_commision`, `switch_operator`, and `distribute` — i.e. essentially every staker- or operator-initiated flow that touches distribution accounting [2](#0-1) [3](#0-2) .

Inside `distribute_internal`, the function iterates over `distribution_pool.shareholders()` and, for each recipient, redeems their shares and calls `aptos_account::deposit_coins`:
```
if (recipient == operator) {
    recipient = beneficiary_for_operator(operator);
};
aptos_account::deposit_coins(recipient, coin::extract(&mut coins, amount_to_distribute));
``` [4](#0-3) 

`aptos_account::deposit_coins` will `abort` with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` if the target account is not yet registered for the coin type and has explicitly disabled direct transfers via `set_allow_direct_coin_transfers(false)`:
```
if (!coin::is_account_registered<CoinType>(to)) {
    assert!(
        can_receive_direct_coin_transfers(to),
        error::permission_denied(EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS)
    );
    coin::register<CoinType>(&create_signer(to));
};
``` [5](#0-4) , with the opt-out flag toggled by anyone for their own address via `set_allow_direct_coin_transfers` [6](#0-5) .

An operator freely controls their beneficiary via `set_beneficiary_for_operator`, without the staker's consent, and this address is not required to already exist, be registered for `AptosCoin`, or accept direct transfers: [7](#0-6) .

Attack sequence:
1. Operator (or a colluding party) creates/controls address `B`, calls `set_allow_direct_coin_transfers(false)` from `B`, and never registers `B` for `AptosCoin`.
2. Operator calls `set_beneficiary_for_operator(operator, B)`.
3. As soon as the operator has any pending commission distribution recorded in `distribution_pool` (created by `request_commission_internal`/`add_distribution`, which happens automatically inside `unlock_stake`, `switch_operator`, `update_commision`, or explicit `request_commission`), any subsequent call to `distribute_internal` — triggered by the staker calling `unlock_stake`, `switch_operator`, `update_commision`, or anyone calling `distribute` — iterates the loop and hits the operator's commission entry, resolves recipient to `B`, and aborts on `deposit_coins`.
4. Because the loop processes all shareholders (including the staker's own principal/reward distribution) in one atomic call, the abort reverts the whole transaction — the staker cannot withdraw principal or rewards, cannot switch away from the operator, and cannot force a commission distribution to clear the block, since `distribute`, `unlock_stake`, `switch_operator`, and `update_commision` all funnel through the same `distribute_internal`.

This is the direct Aptos analog of the external report's pattern: an external/second-party controllable "require"-like abort condition (here, `deposit_coins`'s opt-out check on an operator-chosen beneficiary) is reachable from the core stake-withdrawal path and can revert the whole transaction, blocking unrelated legitimate stake operations for another unprivileged party (the staker).

### Impact Explanation
This blocks a staker's ability to unlock or withdraw their own principal/rewards, and blocks switching away from a malicious/uncooperative operator, as long as the operator keeps their beneficiary configured to reject the deposit. Because `switch_operator` — the staker's only self-service path to escape a bad operator — also calls `distribute_internal` before performing the switch, the staker is effectively locked into the relationship with that operator until the beneficiary condition is resolved (something entirely outside the staker's control). This matches "permanent lock or non-recoverable loss of claim rights in stake ... flows" from an unprivileged root cause (the operator does not need any elevated permission beyond the beneficiary-setting right they already have over their own commission).

### Likelihood Explanation
Medium-to-high: the preconditions (operator sets an unregistered, opted-out beneficiary; some commission accrues) are simple, require no privileged access beyond what an operator already legitimately has over their own beneficiary setting, and can be triggered deliberately or accidentally (e.g., operator picks a beneficiary address that happens to have opted out of direct transfers for unrelated reasons).

### Recommendation
Do not let a single recipient's payout failure abort the entire distribution loop in `distribute_internal`. Options: wrap each `deposit_coins` call so failures for one recipient (e.g., unregistered/opted-out beneficiary) don't prevent other recipients' payouts and don't block the caller's own unlock/switch operation; fall back to keeping the failed recipient's shares in the pool (or route to a claimable escrow) instead of reverting; alternatively, validate/register the beneficiary's ability to receive `AptosCoin` at the time `set_beneficiary_for_operator` is called, so an operator cannot set an unpayable beneficiary in the first place.

### Proof of Concept
1. Staker creates a staking contract with `operator` and some commission percentage via `staking_contract::create_staking_contract`.
2. Attacker/operator creates account `B`, does not register `B` for `AptosCoin`, and calls `aptos_account::set_allow_direct_coin_transfers(B_signer, false)`.
3. Operator calls `staking_contract::set_beneficiary_for_operator(operator, B)`.
4. Staker calls `staking_contract::unlock_stake(staker, operator, amount)` (or any flow reaching `distribute_internal` after commission has accrued) — this internally calls `request_commission_internal`, adding a distribution entry for `operator`, then subsequent `distribute_internal` calls (from `distribute`, further `unlock_stake`, or `switch_operator`) iterate the pool, resolve the operator's entry to beneficiary `B`, and call `aptos_account::deposit_coins(B, ...)`, which aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` since `B` is unregistered and has opted out.
5. The staker's transaction reverts; the staker cannot withdraw or switch operator until `B`'s configuration changes — something the staker cannot control.

Note: I could not locate the exact `add_distribution`/`update_distribution_pool` helper source in this pass (grep for `fun add_distribution` on `staking_contract.move` returned no match within available tool budget), so the precise share-bookkeeping details of `add_distribution` are inferred from the call sites shown (`request_commission_internal`, `unlock_stake`) rather than directly cited; a full review of `add_distribution`/`update_distribution_pool` is recommended to confirm exact share amounts before finalizing a fix.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L676-729)
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

        // If there's less active stake remaining than the amount requested (potentially due to commission),
        // only withdraw up to the active amount.
        let (active, _, _, _) = stake::get_stake(staking_contract.pool_address);
        if (active < amount) {
            amount = active;
        };
        staking_contract.principal -= amount;

        // Record a distribution for the staker.
        add_distribution(
            operator,
            staking_contract,
            staker_address,
            amount,
        );

        // Request to unlock the distribution amount from the stake pool.
        // This won't become fully unlocked until the stake pool's lockup expires.
        stake::unlock_with_cap(amount, &staking_contract.owner_cap);

        let pool_address = staking_contract.pool_address;
        emit(
            UnlockStake { pool_address, operator, amount, commission_paid }
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L762-805)
```text
    public entry fun switch_operator(
        staker: &signer,
        old_operator: address,
        new_operator: address,
        new_commission_percentage: u64
    ) acquires Store, BeneficiaryForOperator {
        let staker_address = signer::address_of(staker);
        assert_staking_contract_exists(staker_address, old_operator);

        assert!(
            new_commission_percentage <= 100,
            error::invalid_argument(EINVALID_COMMISSION_PERCENTAGE)
        );
        // Merging two existing staking contracts is too complex as we'd need to merge two separate stake pools.
        let store = borrow_global_mut<Store>(staker_address);
        let staking_contracts = &mut store.staking_contracts;
        assert!(
            !staking_contracts.contains_key(&new_operator),
            error::invalid_state(ECANT_MERGE_STAKING_CONTRACTS)
        );

        let (_, staking_contract) = staking_contracts.remove(&old_operator);
        // Force distribution of any already inactive stake.
        distribute_internal(
            staker_address,
            old_operator,
            &mut staking_contract,
        );

        // For simplicity, we request commission to be paid out first. This avoids having to ensure to staker doesn't
        // withdraw into the commission portion.
        request_commission_internal(
            old_operator,
            &mut staking_contract,
        );

        // Update the staking contract's commission rate and stake pool's operator.
        stake::set_operator_with_cap(&staking_contract.owner_cap, new_operator);
        staking_contract.commission_percentage = new_commission_percentage;

        let pool_address = staking_contract.pool_address;
        staking_contracts.add(new_operator, staking_contract);
        emit(SwitchOperator { pool_address, old_operator, new_operator });
    }
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

**File:** aptos-move/framework/aptos-framework/sources/aptos_account.move (L123-129)
```text
        if (!coin::is_account_registered<CoinType>(to)) {
            assert!(
                can_receive_direct_coin_transfers(to),
                error::permission_denied(EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS)
            );
            coin::register<CoinType>(&create_signer(to));
        };
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
