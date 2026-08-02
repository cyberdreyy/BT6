## Finding: `switch_operator` can permanently bypass a former operator's registered beneficiary for commission that is unlocked but not yet inactive at switch time

### Title
Switch-operator commission settlement is incomplete for pending_inactive stake, causing old operator's beneficiary payout to be bypassed after re-key - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`switch_operator` attempts to settle the old operator's outstanding commission before handing the `StakingContract` (and its `distribution_pool`) over to the new operator, but it can only force-settle stake that is already `inactive`, not stake that is merely `pending_inactive` (unlocked but still within lockup). When an outstanding commission share survives the switch because its underlying stake hasn't finished unlocking, the later `distribute()` call pays it to the recorded recipient address directly instead of routing it through `beneficiary_for_operator`, because the beneficiary-redirect check compares the recipient to the *current* operator key rather than to the recipient's own registered beneficiary.

### Finding Description
`switch_operator` does the right thing in principle: it calls `distribute_internal` and `request_commission_internal` for `old_operator` before re-keying the `StakingContract` to `new_operator`: [1](#0-0) 

`distribute_internal`, however, only withdraws `inactive + pending_inactive` and short-circuits with no effect on the pool's shares if the withdrawn amount is zero: [2](#0-1) 

Critically, `stake::withdraw_with_cap`/`withdraw_internal` in `stake.move` only actually extracts `pending_inactive` funds once the pool's lockup has expired; before that, only the already-`inactive` portion can be withdrawn. So if a commission share was added via `request_commission_internal`/`add_distribution` (recipient = `old_operator`) while the lockup has not yet expired, that share remains an un-redeemed entry in `distribution_pool` when `switch_operator` re-keys the contract to `new_operator`.

When `distribute()` is later invoked (by anyone, since it's unprivileged) using the new map key, `distribute_internal` is called with `operator = new_operator`, and the beneficiary-redirect check compares the *stored* recipient against this current `operator`, not against the recipient's own beneficiary: [3](#0-2) 

Since `recipient == old_operator` but `operator == new_operator`, `recipient == operator` is false, so the redirect to `beneficiary_for_operator(old_operator)` never fires. The funds are paid directly to `old_operator`'s own address, bypassing whatever beneficiary `old_operator` had configured via `set_beneficiary_for_operator`.

This contradicts the documented invariant in the module's own spec comment, which states switch must ensure commission "is correctly requested and paid out from the old operator's stake pool before allowing the switch," and that this "guarantees the staker receives the appropriate commission amount and maintains the integrity of the staking process": [4](#0-3) 
Note the spec for `switch_operator` is also unverified (`pragma verify = false`), leaving this gap formally unchecked: [5](#0-4) 

### Impact Explanation
This falls under "Operator commission, beneficiary payout, or share-accounting corruption that credits the wrong account" from the required impacts. An unprivileged staker (acting only within their existing pool-owner role, which is the normal caller of `switch_operator`) can time a `switch_operator` call while a previously-unlocked commission distribution is still `pending_inactive`, causing that commission to later be paid to the old operator's raw account address instead of the operator's registered beneficiary — silently overriding the beneficiary's claim rights without the beneficiary's consent. Note: the misdirected funds go to the *old operator itself*, not to the new operator as originally hypothesized in the submitted question; the exact redirect target differs from the proof idea, but the underlying invariant break (beneficiary bypass across a `switch_operator` boundary) is real.

### Likelihood Explanation
Requires a specific but achievable timing: a commission unlock via `request_commission` (or the implicit one inside `unlock_stake`) must occur while the stake pool's lockup has not yet expired, and `switch_operator` must be called before that lockup completes and before anyone calls `distribute()` to flush the pending share. Both `request_commission` and `switch_operator` are staker/operator-callable at will, so a staker (or a staker colluding with themselves) can engineer this sequence deterministically.

### Recommendation
In `distribute_internal`, resolve the beneficiary based on the recipient's own registered beneficiary rather than comparing to the currently-associated `operator`:
```move
if (exists<BeneficiaryForOperator>(recipient)) {
    recipient = beneficiary_for_operator(recipient);
};
```
Additionally, consider having `switch_operator` refuse to proceed (or force a full commission unlock/wait) while `distribution_pool.shareholders_count() > 0` after the pre-switch `distribute_internal`/`request_commission_internal` calls, to guarantee no residual shares survive the re-key.

### Proof of Concept
1. Set up a staking contract for `staker`/`operator1` with nonzero commission; `operator1` calls `set_beneficiary_for_operator(beneficiary)`.
2. Advance an epoch to accrue rewards, call `request_commission(operator1, staker, operator1)` — this unlocks commission into `pending_inactive` and records an `add_distribution` share keyed at `operator1`, but do **not** advance past `locked_until_secs`.
3. While lockup is still active, call `switch_operator(staker, operator1, operator2, new_commission)`. `distribute_internal` inside computes `total_potential_withdrawable` but `stake::withdraw_with_cap` returns 0 actual coins (pending_inactive not yet unlockable), so the pending share for `operator1` is left untouched in `distribution_pool`, which is then moved to the `operator2` map entry.
4. Advance time past lockup expiry so `pending_inactive` becomes `inactive`.
5. Call `distribute(staker, operator2)`. Assert that the commission amount is deposited to `operator1`'s address (not `beneficiary`), confirming the beneficiary bypass.

### Citations

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L856-879)
```text
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

```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L888-899)
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
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.spec.move (L52-59)
```text
    /// No.: 7
    /// Requirement: The stake pool ensures that the commission is correctly requested and paid out from the old operator's
    /// stake pool before allowing the switch to the new operator.
    /// Criticality: High
    /// Implementation: The switch_operator function initiates the commission payout from the stake pool associated with
    /// the old operator, ensuring a smooth transition. Paying out the commission before the switch guarantees that the
    /// staker receives the appropriate commission amount and maintains the integrity of the staking process.
    /// Enforcement: Audited that the commission is paid to the old operator.
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.spec.move (L311-326)
```text
    /// Staking_contract exists the stacker/operator pair.
    spec switch_operator(
        staker: &signer,
        old_operator: address,
        new_operator: address,
        new_commission_percentage: u64
    ) {
        // TODO: Call `update_distribution_pool` and could not verify `update_distribution_pool`.
        // TODO: Set because of timeout (estimate unknown).
        pragma verify = false;
        let staker_address = signer::address_of(staker);
        include ContractExistsAbortsIf { staker: staker_address, operator: old_operator };
        let store = global<Store>(staker_address);
        let staking_contracts = store.staking_contracts;
        aborts_if simple_map::spec_contains_key(staking_contracts, new_operator);
    }
```
