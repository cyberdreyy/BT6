## Title
Operator commission diverted from configured beneficiary after `switch_operator` due to stale distribution-pool key mismatch - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
When a staker calls `switch_operator`/`switch_operator_with_same_commission`, any commission that was queued (but not yet distributed) for the *old* operator is silently reused under the *new* operator's key. `distribute_internal` later fails to recognize that queued distribution entry as belonging to the old operator, so it skips the beneficiary redirection and pays the funds directly to the old operator's raw address instead of the beneficiary address the old operator had configured with `set_beneficiary_for_operator`.

### Finding Description
`switch_operator` removes the `StakingContract` struct from `old_operator`'s slot, forces a commission request via `request_commission_internal`, then re-inserts the *same* struct (including its `distribution_pool`) under `new_operator`'s key: [1](#0-0) 

`request_commission_internal` records the pending commission as a distribution share keyed by `operator` (i.e. `old_operator` at call time): [2](#0-1) 

Later, anyone can call the permissionless `distribute` entry function on the *new* operator key, which calls `distribute_internal(staker, new_operator, staking_contract)`: [3](#0-2) 

Inside `distribute_internal`, the shareholder-redirect check compares the stored recipient (still `old_operator`'s address, from before the switch) against the `operator` parameter (now `new_operator`):
```
if (recipient == operator) {
    recipient = beneficiary_for_operator(operator);
};
``` [4](#0-3) 

Since `old_operator != new_operator`, this condition is false, so the beneficiary lookup is skipped entirely and the commission is paid straight to `old_operator`'s raw address — even though `old_operator` had explicitly configured a different beneficiary via `set_beneficiary_for_operator`: [5](#0-4) 

### Impact Explanation
This misroutes an operator's already-earned, already-configured beneficiary payout to the operator's own address instead. This is exactly the "beneficiary payout corruption that credits the wrong account" impact class: the operator's beneficiary (who may be a separate custodial/reward-collection account per the design of `set_beneficiary_for_operator`) loses its rightful claim on the commission, while the raw operator address unexpectedly receives funds it should not have received directly. In setups where the beneficiary is used precisely to segregate operator hot-key funds from commission payouts (a common operational security pattern), this silently breaks that segregation guarantee.

### Likelihood Explanation
This is triggerable by fully unprivileged actions with no special role assumptions:
1. Operator calls `set_beneficiary_for_operator` (their own right).
2. Stake pool accrues rewards; staker (or operator/beneficiary) calls `request_commission` to queue a distribution for the operator, without distributing yet.
3. Staker calls `switch_operator` (or `switch_operator_with_same_commission`) to move to a new operator address before the pending commission is distributed.
4. Anyone (the `distribute` entry function is explicitly permissionless: "Allow anyone to distribute already unlocked funds") calls `distribute(staker, new_operator)` once the commission becomes withdrawable, triggering the misrouted payout.

All of steps 1–4 are ordinary, expected usage patterns (commission requests are commonly delayed by lockup, and staker-initiated operator switches are a supported first-class feature), making this reasonably likely to occur in production without any adversarial intent — it can even happen accidentally.

### Recommendation
In `distribute_internal`, capture and use the `operator` address that was actually recorded as the distribution-pool key for each shareholder (or, simpler, redirect **every** recipient through `beneficiary_for_operator` if that recipient corresponds to *any* operator with a `BeneficiaryForOperator` entry, not just the currently-passed `operator` parameter). Alternatively, `switch_operator` should force a full `distribute_internal` (not just `request_commission_internal`) for the old operator's pending commission before reassigning the `StakingContract` to `new_operator`, ensuring no cross-operator distribution-pool entries survive the switch.

### Proof of Concept
1. Staker creates staking contract with `operator_1`, commission 10%.
2. `operator_1` calls `staking_contract::set_beneficiary_for_operator(beneficiary_1)`.
3. Stake pool earns rewards; staker calls `staking_contract::request_commission(staker, operator_1)` — this adds a distribution share keyed to `operator_1` inside the contract's `distribution_pool` (per `request_commission_internal`, lines 637-674) but does not yet pay it out (funds are only unlocking, not yet inactive/withdrawable).
4. Before the lockup expires, staker calls `staking_contract::switch_operator_with_same_commission(staker, operator_1, operator_2)` — the `StakingContract` (with its `distribution_pool` still containing the `operator_1`-keyed share) is moved to the `operator_2` slot (lines 783-805).
5. After lockup expiry, anyone calls `staking_contract::distribute(staker, operator_2)`.
6. Inside `distribute_internal(staker, operator_2, staking_contract)`, the loop finds `recipient == operator_1`, compares against `operator == operator_2`, the check fails, and `aptos_account::deposit_coins` pays the commission directly to `operator_1`'s address instead of `beneficiary_1`.

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L840-853)
```text
    /// Allow anyone to distribute already unlocked funds. This does not affect reward compounding and therefore does
    /// not need to be restricted to just the staker or operator.
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L888-902)
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
