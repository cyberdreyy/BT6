## Confirmed local root cause

`staking_contract::unlock_stake` -> `request_commission_internal` -> `add_distribution(operator, ..., operator, commission_amount)` records the operator's commission under the operator's own address in the same `distribution_pool` used for the staker's unlocked-stake distribution, and `unlock_stake` then also does `add_distribution(operator, ..., staker_address, amount)` for the staker, into that same pool [1](#0-0) [2](#0-1) .

`distribute_internal` then pays out **every** shareholder in that pool in a single atomic `while` loop, redirecting the operator's shares to its beneficiary via `beneficiary_for_operator(operator)`, and depositing with `aptos_account::deposit_coins` [3](#0-2) .

`set_beneficiary_for_operator` lets the operator set an arbitrary `new_beneficiary` address with **no registration check at all** [4](#0-3)  — unlike `vesting::set_beneficiary`, which explicitly calls `assert_account_is_registered_for_apt(new_beneficiary)` before allowing the change [5](#0-4) .

`aptos_account::deposit_coins` only auto-registers the recipient if it is not yet a coin store, but first checks `can_receive_direct_coin_transfers`, which reverts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` if the account has opted out of unsolicited transfers [6](#0-5) .

### Title
Operator-controlled unregistered/opted-out beneficiary permanently blocks `staking_contract::distribute`, freezing staker's unlocked stake - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`staking_contract::set_beneficiary_for_operator` lets an operator point commission payouts to any address without verifying it can actually receive APT. `distribute_internal` pays the staker and the operator's beneficiary out of the *same* `distribution_pool` in one atomic loop over `shareholders()`. If the operator sets its beneficiary to an account that has disabled direct coin transfers (or otherwise cannot receive the deposit), every subsequent call to `distribute`, `unlock_stake`, `request_commission`, `switch_operator`, or `update_commission_percentage` (all of which invoke `distribute_internal`) reverts, because the loop cannot skip a failing recipient — it processes the whole pool or aborts entirely.

### Finding Description
- `distribute_internal` iterates `distribution_pool.shareholders()` and calls `aptos_account::deposit_coins(recipient, ...)` for each one before returning [3](#0-2) .
- When `recipient == operator`, the payout is redirected to `beneficiary_for_operator(operator)` [7](#0-6) .
- `set_beneficiary_for_operator` performs no registration/eligibility check on `new_beneficiary`, only a feature flag check [4](#0-3) .
- `deposit_coins` aborts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` for a not-yet-registered account that has disabled `allow_arbitrary_coin_transfers` via `aptos_account::set_allow_direct_coin_transfers` (a self-service, unprivileged call) [6](#0-5) .
- Because the entire `while` loop in `distribute_internal` must complete for the function to return (Move transactions are atomic — a single abort reverts the whole call, including the prior `stake::withdraw_with_cap` that pulled coins out of the stake pool), **one poisoned beneficiary blocks distribution to every other shareholder in the same `distribution_pool`**, which in this design is the staker itself plus the operator [8](#0-7) .
- Every entrypoint that unlocks or withdraws staker funds funnels through `distribute_internal` first: `unlock_stake` [9](#0-8) , `request_commission` [10](#0-9) , `distribute` (the plain public withdrawal call) [11](#0-10) , `switch_operator`, and `update_commission_percentage`. This mirrors the GiantLP report's pattern exactly: a hostile/incompatible zero-address-like recipient inside a mandatory hook/loop makes the entire withdrawal path permanently revert.

This is unprivileged and staker-triggered: the staker does not need any special role — they merely need a staking_contract with an operator who (maliciously or accidentally) points its beneficiary at a non-coin-accepting address, something the operator can always do with `set_beneficiary_for_operator` since there's no gate.

### Impact Explanation
The staker's already-unlocked/inactive stake becomes permanently non-withdrawable through the standard `staking_contract` API — `distribute`, `unlock_stake`, `request_commission`, `switch_operator`, and `update_commission_percentage` all revert as long as the poisoned beneficiary exists and neither the operator nor anyone else can silently work around it (nothing in `staking_contract.move` allows removing/replacing a beneficiary except the operator itself calling `set_beneficiary_for_operator` again, and a malicious/compromised operator has no incentive to fix it). This is a "permanent lock / non-recoverable loss of claim rights in stake and commission" scenario, matching the in-scope stake/lockup impact category. It affects the entire pool (staker + operator) since they share one `distribution_pool` and one atomic distribution call.

### Likelihood Explanation
Medium-high. It requires the operator to (a) call `set_beneficiary_for_operator` pointing to an address that (b) has previously called the unprivileged `aptos_account::set_allow_direct_coin_transfers(false)` and (c) has not yet been registered for `AptosCoin`. All three actions are ordinary, permissionless operations reachable by any operator (or an operator colluding/compromised, or simply careless), with no code path currently preventing it — contrasted directly with `vesting::set_beneficiary`, which explicitly guards against this exact failure mode with `assert_account_is_registered_for_apt`.

### Recommendation
Add the same guard used in `vesting::set_beneficiary` to `staking_contract::set_beneficiary_for_operator` (and the analogous function in `delegation_pool.move`): require `assert_account_is_registered_for_apt(new_beneficiary)` (or equivalently pre-register the coin store) before allowing the change. Additionally, consider decoupling per-recipient payout failures in `distribute_internal`'s loop (e.g., skip/queue a failing recipient instead of aborting the whole distribution) so that one bad beneficiary cannot block payouts to unrelated shareholders.

### Proof of Concept
1. Staker calls `staking_contract::create_staking_contract(staker, operator, voter, amount, commission_percentage, seed)`.
2. Attacker-controlled `operator` calls `aptos_account::set_allow_direct_coin_transfers(operator_signer_for_beneficiary_addr, false)` from a fresh, never-coin-registered address `B` (this can be any address the operator controls and has not yet registered for `AptosCoin`).
3. Operator calls `staking_contract::set_beneficiary_for_operator(operator, B)` — succeeds with no check [4](#0-3) .
4. Stake pool accrues rewards; `commission_percentage > 0` so commission accrues to the operator's shares in `distribution_pool`.
5. Stake pool's lockup ends and stake becomes inactive.
6. Staker (or anyone) calls `staking_contract::distribute(staker, operator)` (or `unlock_stake`/`request_commission`). Inside `distribute_internal`, the loop reaches the operator's redeemed shares, resolves `recipient = beneficiary_for_operator(operator) = B`, and calls `aptos_account::deposit_coins(B, ...)`, which reverts with `EACCOUNT_DOES_NOT_ACCEPT_DIRECT_COIN_TRANSFERS` [12](#0-11) .
7. The whole transaction aborts; the staker's portion of the same distribution (which was processed earlier or later in the same loop) is also rolled back, so the staker cannot withdraw funds through any of `distribute`, `unlock_stake`, `request_commission`, `switch_operator`, or `update_commission_percentage` until the beneficiary situation is fixed — something only the operator can do, and has no incentive to.

Note: I could not execute this scenario in a Move test harness within this session (no filesystem/terminal access here) — the trace above is derived purely from static analysis of the cited source; a Devin session with Move/Aptos CLI access would be needed to run an actual `#[test]` reproduction confirming the abort code and full revert semantics end-to-end.

### Citations

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L676-719)
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
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L810-838)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L842-853)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L855-919)
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
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L915-923)
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
