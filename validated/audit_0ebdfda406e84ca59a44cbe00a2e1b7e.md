## Finding

The bug is real, but the actual misrouting target differs from what the proof idea guesses: the stranded commission tranche is **not** redirected to `beneficiary_for_operator(new_operator)`. Instead it is paid directly to the raw `old_operator` address, completely bypassing *any* beneficiary lookup (old or new). This still qualifies as a beneficiary/commission-routing corruption reachable by an unprivileged caller.

### Title
Commission requested under `old_operator` before an operator switch bypasses beneficiary routing and pays the raw old-operator address instead of `beneficiary_for_operator(old_operator)` - ([File: aptos-move/framework/aptos-framework/sources/staking_contract.move])

### Summary
`staking_contract::request_commission_internal` records a pending payout share keyed by the **literal operator address at request time** via `add_distribution(operator, staking_contract, operator, commission_amount)` [1](#0-0) . This share lives inside `staking_contract.distribution_pool`, a field of the `StakingContract` struct that is moved (not recreated) into the new map slot when `switch_operator`/`vesting::update_operator` runs. `distribute_internal` later redirects a payout to `beneficiary_for_operator` only when `recipient == operator`, where `operator` is the function's **current** parameter [2](#0-1) . If a commission tranche was requested and unlocked under `old_operator` but had not yet finished its lockup (still `pending_inactive`) when `switch_operator` ran, `distribute_internal`'s early-return on `distribution_amount == 0` [3](#0-2)  means that tranche's share entry (`recipient == old_operator`) survives the switch untouched inside the very struct that gets re-keyed to `new_operator`.

### Finding Description
1. Admin (staker of the vesting contract) requests commission for `old_operator` (directly via `staking_contract::request_commission`, or indirectly via `unlock_stake`/`unlock_rewards`). This calls `stake::unlock_with_cap`, moving the commission amount to `pending_inactive`, and records a share for `recipient = old_operator` in `staking_contract.distribution_pool` [4](#0-3) .
2. Before the stake pool's lockup expires, admin calls `vesting::update_operator(admin, contract_address, new_operator, new_commission_percentage)`, which invokes `staking_contract::switch_operator(contract_signer, old_operator, new_operator, new_commission_percentage)` [5](#0-4) . Inside `switch_operator`, the forced `distribute_internal(staker, old_operator, ...)` call finds `distribution_amount == 0` (because the earlier commission is still `pending_inactive`, not yet `inactive`) and returns without touching the `distribution_pool`, so the `old_operator` share entry survives [6](#0-5) . The whole `StakingContract` struct — including that stale `distribution_pool` — is then moved from the `old_operator` key to the `new_operator` key in the `Store` map.
3. Once the lockup expires, any unprivileged caller invokes `vesting::distribute(contract_address)` — a fully permissionless `public entry fun` with no signer/role check at all [7](#0-6)  — which calls `staking_contract::distribute(contract_address, vesting_contract.staking.operator)`, using the already-updated `new_operator` [8](#0-7) .
4. `distribute_internal` now finds the previously stranded stake inactive and redeems all shares in the pool, including the `old_operator` entry. Because the redirection check compares the stored `recipient` (`old_operator`) against the current function parameter `operator` (`new_operator`), the condition `recipient == operator` is false, so the payout is deposited **directly to the `old_operator` address**, never consulting `beneficiary_for_operator(old_operator)` or `beneficiary_for_operator(new_operator)` [9](#0-8) .

### Impact Explanation
If `old_operator` had configured `set_beneficiary_for_operator` to redirect commission payouts to a custodian/partner/tax address, any commission tranche that was requested-but-still-locked at the moment of an operator switch silently bypasses that beneficiary and is paid straight into `old_operator`'s own wallet instead. This is a real corruption of commission routing invariants (beneficiary boundaries should hold regardless of subsequent operator changes) and is triggerable purely through permissionless entrypoints (`vesting::update_operator` by the admin — a normal, expected operation — followed by `vesting::distribute` by anyone).

### Likelihood Explanation
Moderate. It requires a commission request to be in-flight (unlocked but not yet past lockup) at the exact moment an operator switch occurs — a realistic but non-trivial timing window that can occur naturally whenever an admin rotates operators shortly after a commission request, or can be intentionally engineered by the admin/operator to strip beneficiary protection from a specific payout.

### Recommendation
In `distribute_internal`, do not compare `recipient == operator` (the function's current-operator parameter). Instead, resolve the beneficiary redirection based on the recorded distribution's original operator context, or force full settlement/redemption of any operator-owed distribution shares for the *old* operator at the time of `switch_operator` (i.e., make `distribute_internal` inside `switch_operator` unconditionally flush/settle pending shares for `old_operator`, or track distributions with an explicit "is this the operator's commission share" flag independent of address equality) so that beneficiary rerouting is preserved across operator changes.

### Proof of Concept
1. Set up a vesting contract with `operator1`, commission 10%, and call `staking_contract::set_beneficiary_for_operator(operator1, beneficiary1)`.
2. Advance an epoch so rewards accrue; call `staking_contract::request_commission(operator1, contract_address, operator1)` — this unlocks the commission into `pending_inactive` and records a distribution share for `operator1` in `distribution_pool`.
3. **Before** the lockup expires, admin calls `vesting::update_operator(admin, contract_address, operator2, new_commission_percentage)`. Internally `switch_operator`'s `distribute_internal` no-ops (amount still pending_inactive, `distribution_amount == 0`), so the `operator1` share persists in the struct now keyed under `operator2`.
4. Fast-forward past lockup expiry (`stake::fast_forward_to_unlock`) so the pending_inactive stake becomes inactive.
5. An unrelated, unprivileged signer calls `vesting::distribute(contract_address)`.
6. Assert `coin::balance<AptosCoin>(operator1)` increased by the accrued commission and `coin::balance<AptosCoin>(beneficiary1)` did **not** — demonstrating the tranche bypassed `beneficiary_for_operator(operator1)` and was paid to `operator1` directly instead, even though `operator1` had a beneficiary configured.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L637-661)
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
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L783-805)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L868-878)
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

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L718-728)
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
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L823-835)
```text
    public entry fun update_operator(
        admin: &signer,
        contract_address: address,
        new_operator: address,
        commission_percentage: u64,
    ) acquires VestingContract {
        let vesting_contract = borrow_global_mut<VestingContract>(contract_address);
        verify_admin(admin, vesting_contract);
        let contract_signer = &get_vesting_account_signer_internal(vesting_contract);
        let old_operator = vesting_contract.staking.operator;
        staking_contract::switch_operator(contract_signer, old_operator, new_operator, commission_percentage);
        vesting_contract.staking.operator = new_operator;
        vesting_contract.staking.commission_percentage = commission_percentage;
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L1071-1078)
```text
    fun withdraw_stake(vesting_contract: &VestingContract, contract_address: address): Coin<AptosCoin> {
        // Claim any withdrawable distribution from the staking contract. The withdrawn coins will be sent directly to
        // the vesting contract's account.
        staking_contract::distribute(contract_address, vesting_contract.staking.operator);
        let withdrawn_coins = coin::balance<AptosCoin>(contract_address);
        let contract_signer = &get_vesting_account_signer_internal(vesting_contract);
        coin::withdraw<AptosCoin>(contract_signer, withdrawn_coins)
    }
```
