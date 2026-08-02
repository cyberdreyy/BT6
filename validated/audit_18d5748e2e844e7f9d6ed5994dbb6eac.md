### Title
Front-runnable beneficiary switch via permissionless `distribute()` lets stale/compromised beneficiary drain pending operator commission - (File: `aptos-move/framework/aptos-framework/sources/staking_contract.move`)

### Summary
`staking_contract::distribute()` is explicitly permissionless (any account may call it for any staker/operator pair) and resolves the payout recipient for the operator's share by calling `beneficiary_for_operator(operator)` *at redemption time*, not at the time the commission distribution was queued. Because there is no nonce/ordering guarantee tying a `request_commission`/`distribute` call to a specific beneficiary value, an unprivileged actor can race an operator's own `set_beneficiary_for_operator` transaction: by front-running it with a `distribute()` call, the actor locks in payment to the stale (old/compromised) beneficiary address before the operator's update takes effect — the same reordering hazard described in the Harpie M-9 report.

### Finding Description
In `distribute_internal`, the distribution pool tracks shares keyed by `operator` (not by a snapshotted beneficiary address), and only when shares are redeemed does the code look up the beneficiary: [1](#0-0) 

The function is declared explicitly permissionless in its own doc comment: [2](#0-1) 

`add_distribution`/`update_distribution_pool` also buy shares in under the `operator` key without resolving or freezing a beneficiary at queue time: [3](#0-2) 

The `BeneficiaryForOperator` mapping used for the live lookup can be changed at any time by the operator via `set_beneficiary_for_operator`, and this is documented as needing manual synchronization first to avoid exactly this kind of stale payout in the `delegation_pool` analog of the same pattern: [4](#0-3) 

Because (a) `distribute()` carries no nonce/ordering binding to a particular beneficiary value, and (b) it is callable by anyone, an unprivileged actor observing an operator's pending "fix my compromised beneficiary" transaction in the mempool can submit a `distribute()` transaction first (with higher gas / same-slot reordering) that resolves `beneficiary_for_operator(operator)` while it still points at the old/insecure address, paying out unlocked commission to that address before the operator's correction lands. This mirrors the Harpie M-9 pattern precisely: a legitimate actor tries to redirect future payouts to a safe address, but transaction-ordering lets the stale address take effect first.

### Impact Explanation
Pending unlocked commission (inactive/pending_inactive stake already earned by the operator) can be redirected to a beneficiary the operator is actively trying to move away from, effectively letting whoever controls the stale beneficiary address capture funds that rightfully belong to the operator/its new beneficiary. This is a share-accounting/beneficiary-payout corruption that credits the wrong account, matching the "Operator commission, beneficiary payout... corruption that credits the wrong account" impact category. The amount at risk scales with accumulated, already-unlocked commission, which can be significant for long-lived validator/operator pools.

### Likelihood Explanation
Exploitation requires only observing a pending `set_beneficiary_for_operator` transaction in the mempool and racing it with a `distribute()` call — no special privileges, no compromised keys beyond the pre-existing (attacker-controlled) old beneficiary address are needed by the actor calling `distribute()`, since `distribute()` is permissionless by design. The race window exists on every beneficiary change where unlocked/inactive commission is outstanding, which is a routine operational scenario (operators regularly reset beneficiaries for tax/custody/security reasons).

### Recommendation
Snapshot/resolve the beneficiary at the time commission is queued for distribution (i.e., in `request_commission_internal`/`add_distribution`) rather than resolving it lazily in `distribute_internal`, or require `synchronize`/flush of pending distributions atomically within `set_beneficiary_for_operator` before the beneficiary pointer is updated, so no permissionless caller can force a payout under the stale mapping after a beneficiary-update transaction has been submitted.

### Proof of Concept
1. Operator `O` has `BeneficiaryForOperator` pointing at address `A` (e.g., set previously, now considered insecure).
2. Operator/staker calls `request_commission` for pool `(staker, O)`, which via `unlock_stake`/commission flow queues pending_inactive/inactive funds under distribution shares keyed by `O`.
3. Time passes; funds become `inactive` (withdrawable), per `stake::get_stake`.
4. Operator submits `set_beneficiary_for_operator(O, B)` to move future payouts to secure address `B`.
5. Any unprivileged third party observes this pending tx and submits `distribute(staker, O)` with higher priority/gas so it executes first (or via block-producer reordering).
6. `distribute_internal` resolves `recipient = beneficiary_for_operator(O)`, which still returns `A` because the beneficiary update transaction has not yet been applied, and pays the unlocked commission to `A` instead of `B`.
7. The operator's beneficiary-switch transaction then executes, but the funds are already gone to the stale/insecure address `A`.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L841-853)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L938-957)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1268-1291)
```text
    /// Allows an operator to change its beneficiary. Any existing unpaid commission rewards will be paid to the new
    /// beneficiary. To ensure payment to the current beneficiary, one should first call `synchronize_delegation_pool`
    /// before switching the beneficiary. An operator can set one beneficiary for delegation pools, not a separate
    /// one for each pool.
    public entry fun set_beneficiary_for_operator(
        operator: &signer,
        new_beneficiary: address
    ) acquires BeneficiaryForOperator {
        // The beneficiay address of an operator is stored under the operator's address.
        // So, the operator does not need to be validated with respect to a staking pool.
        let operator_addr = signer::address_of(operator);
        let old_beneficiary = beneficiary_for_operator(operator_addr);
        if (exists<BeneficiaryForOperator>(operator_addr)) {
            borrow_global_mut<BeneficiaryForOperator>(operator_addr).beneficiary_for_operator = new_beneficiary;
        } else {
            move_to(operator, BeneficiaryForOperator { beneficiary_for_operator: new_beneficiary });
        };

        emit(SetBeneficiaryForOperator {
            operator: operator_addr,
            old_beneficiary,
            new_beneficiary,
        });
    }
```
