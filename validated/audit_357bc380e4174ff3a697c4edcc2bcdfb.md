This confirms the analysis. The `add_distribution` call in `request_commission_internal` uses `recipient = operator` (the old operator's address), which is a fixed key stored in the `distribution_pool` shares map — [1](#0-0) . Beneficiary resolution only happens later inside `distribute_internal`'s loop, where it compares the recipient key against the `operator` **parameter passed into that specific call**, not against any stored "this share belongs to old operator X" marker [2](#0-1) .

## Finding

`vesting::update_operator` → `staking_contract::switch_operator` does the following in order [3](#0-2) :
1. `distribute_internal(staker, old_operator, ...)` — flushes any already-*inactive* funds, correctly resolving old operator shares to `beneficiary_for_operator(old_operator)` because `operator == old_operator` at that point.
2. `request_commission_internal(old_operator, ...)` — computes newly accrued commission and calls `add_distribution(old_operator, staking_contract, recipient = old_operator, commission_amount)`, then `stake::unlock_with_cap` moves it into `pending_inactive` (not yet withdrawable) [4](#0-3) .
3. The `StakingContract` struct (including its `distribution_pool`) is then re-keyed in the `Store.staking_contracts` map from `old_operator` to `new_operator`, and `stake::set_operator_with_cap` updates the pool operator [5](#0-4) .

The queued share for `old_operator`'s freshly-requested commission (step 2) sits in the shared `distribution_pool` under recipient key `old_operator`, but it hasn't finished unlocking (still `pending_inactive`), so it is **not** paid out during the switch itself.

Later, once the lockup expires, an unprivileged caller invokes `distribute(staker, new_operator)` (this is exactly the flow `vesting::distribute` uses too, since it looks up the vesting contract's current operator). This calls `distribute_internal(staker, new_operator, staking_contract)`. Inside its redemption loop, the beneficiary substitution is:
```
if (recipient == operator) {
    recipient = beneficiary_for_operator(operator);
};
``` [6](#0-5) 

Here `operator` is bound to **`new_operator`** (the parameter of this later call), while `recipient` for the leftover share is still `old_operator`. Since `old_operator != new_operator`, the beneficiary substitution is skipped, and `aptos_account::deposit_coins(recipient, ...)` pays the coins **directly to `old_operator`'s own address**, not to `beneficiary_for_operator(old_operator)` (B1) [7](#0-6) .

So the money still lands with the correct entity that earned it (old operator), but it silently bypasses the beneficiary redirection that operator1 had configured via `set_beneficiary_for_operator`. This is a real, narrower bug than "new operator's beneficiary steals it" — it's a beneficiary-bypass for pre-switch, not-yet-unlocked commission.

### Why the existing test doesn't catch this
The vesting test at `test_beneficiary...` fully distributes operator1's commission via an explicit `distribute()` call and waits for `stake::fast_forward_to_unlock` *before* calling `update_operator` to switch to operator2 [8](#0-7) . It never exercises the window where `switch_operator`'s own `request_commission_internal` unlocks a fresh commission chunk that is still pending at the time of the switch and only becomes distributable afterward under the new operator's key.

### Assessment against review bounds
- The entrypoint is unprivileged: `staking_contract::distribute` / `vesting::distribute` require no special role — "Allow anyone to distribute already unlocked funds" [9](#0-8) .
- It changes *who* receives commission value (old operator's own address instead of their designated beneficiary), which fits "beneficiary-update paths must not redirect value."
- It does not require the attacker to own the pool, operator, or admin role — the unprivileged caller only triggers `distribute`; the admin's `update_operator` call is a normal, non-malicious action that any vesting admin performs, and the bug manifests without any collusion.

## Output

### Title
Stale commission shares in `distribution_pool` bypass `beneficiary_for_operator` after `switch_operator`/`update_operator` — (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`staking_contract::switch_operator` (invoked by `vesting::update_operator`) queues the old operator's freshly-unlocked commission as a `distribution_pool` share keyed by the **old operator's address**, but leaves it unresolved (still `pending_inactive`) during the switch. Once the staking contract is re-keyed to the new operator, a subsequent unprivileged `distribute` call resolves shares by comparing against the *current* operator only, so the old operator's queued share is paid straight to the old operator's account instead of going through `beneficiary_for_operator(old_operator)`.

### Finding Description
See analysis above: `switch_operator` at [3](#0-2)  calls `request_commission_internal` which records the pending payout under recipient key `old_operator` [4](#0-3) . The beneficiary lookup in `distribute_internal` only fires when `recipient == operator` where `operator` is the function's own parameter (the *current* operator at call time) [6](#0-5) , so a leftover old-operator share never gets remapped to that operator's beneficiary once the contract's active operator has changed.

### Impact Explanation
Commission that should be routed to an operator's designated beneficiary (a payout/custody address they explicitly configured via `set_beneficiary_for_operator`) is instead sent to the operator's own address. If the operator's own key is compromised, offline, or intentionally distinct from the beneficiary for custody/compliance reasons, this silently defeats that separation for any commission that was "in flight" (unlocked but not yet inactive) at the moment of an operator switch.

### Likelihood Explanation
Requires: (1) an operator has set a beneficiary, (2) the admin/staker performs a normal, legitimate `update_operator`/`switch_operator` at a moment when there is unpaid accrued commission (common, since `switch_operator` itself force-requests any pending commission), and (3) anyone (including the old operator, the new operator, or an unrelated party) calls `distribute` after the lockup expires. This is a plausible operational sequence, not a contrived edge case, since operator rotation on vesting/staking contracts with active beneficiaries is an expected admin action.

### Recommendation
Track the true "owner operator" per distribution share rather than relying on the ambient `operator` parameter at distribution time — e.g., store the operator address alongside each commission share (or keep a mapping of recipient→originating operator) so `distribute_internal` can call `beneficiary_for_operator` using the operator that earned the share, regardless of whether the staking contract has since been switched to a different operator.

### Proof of Concept
1. Setup vesting contract, `update_operator(admin, contract, operator1, 10)`.
2. `operator1` calls `set_beneficiary_for_operator(operator1, B1)`.
3. Join validator set, `stake::end_epoch()` to accrue rewards/commission.
4. Admin calls `update_operator(admin, contract, operator2, 10)` — this internally unlocks operator1's newly accrued commission into `pending_inactive`, queued under recipient key `operator1`, then re-keys the staking contract to `operator2`.
5. `stake::fast_forward_to_unlock(pool)` so the queued amount becomes `inactive`.
6. Any unprivileged account calls `vesting::distribute(contract_address)` (or `staking_contract::distribute(staker, operator2)`).
7. Assert: `coin::balance<AptosCoin>(operator1_address)` increased by the commission amount, while `coin::balance<AptosCoin>(B1)` did **not** increase — demonstrating the beneficiary bypass. This differs from the existing `test_operator_can_set_beneficiary_for_operator`-style test in `vesting.move` [10](#0-9) , which always fully distributes before switching operators and thus never exercises the "pending, not-yet-inactive share carried across an operator switch" window.

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L783-804)
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
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L840-843)
```text
    /// Allow anyone to distribute already unlocked funds. This does not affect reward compounding and therefore does
    /// not need to be restricted to just the staker or operator.
    public entry fun distribute(
        staker: address, operator: address
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L888-901)
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
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L1684-1713)
```text
        // Distribute the commission to the operator.
        distribute(contract_address);

        // Assert that the beneficiary receives the expected commission.
        assert!(coin::balance<AptosCoin>(operator_address1) == 0, 1);
        assert!(coin::balance<AptosCoin>(beneficiary_address) == expected_commission, 1);
        let old_beneficiay_balance = coin::balance<AptosCoin>(beneficiary_address);

        // switch operator to operator2. The rewards should go to operator2 not to the beneficiay of operator1.
        update_operator(admin, contract_address, operator_address2, 10);

        stake::end_epoch();
        let (_, accumulated_rewards, _) = staking_contract::staking_contract_amounts(contract_address,
            operator_address2
        );

        let expected_commission = accumulated_rewards / 10;

        // Request commission.
        staking_contract::request_commission(operator2, contract_address, operator_address2);
        // Unlocks the commission.
        stake::fast_forward_to_unlock(stake_pool_address);
        expected_commission = with_rewards(expected_commission);

        // Distribute the commission to the operator.
        distribute(contract_address);

        // Assert that the rewards go to operator2, and the balance of the operator1's beneficiay remains the same.
        assert!(coin::balance<AptosCoin>(operator_address2) >= expected_commission, 1);
        assert!(coin::balance<AptosCoin>(beneficiary_address) == old_beneficiay_balance, 1);
```
