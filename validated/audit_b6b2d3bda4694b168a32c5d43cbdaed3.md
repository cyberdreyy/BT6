No vulnerability found for this question.

**Analysis:**

`update_commision` (the typo'd function) is a `public entry fun` that takes `staker: &signer` and operates only on `borrow_global_mut<Store>(staker_address)` where `staker_address = signer::address_of(staker)` [1](#0-0) . This means only the actual staker who created the staking contract can call it against their own `Store` — an unprivileged attacker cannot invoke it against a staker/operator pair they don't own, so this already fails the "attacker already owns the pool" exclusion in the review's Decision Standard.

More importantly, the sequencing in `update_commision` is correct by design, not a bug:
1. `distribute_internal` is called first, which pays out already-unlocked commission/withdrawals using the **currently stored** `commission_percentage` (still the old value) [2](#0-1) .
2. `request_commission_internal` is then called, which computes `commission_amount` from `accumulated_rewards` using `staking_contract.commission_percentage`, again still the **old** value at that point [3](#0-2) [4](#0-3) .
3. Only **after** both of these settle the pre-update rewards does the code assign `staking_contract.commission_percentage = new_commission_percentage` [5](#0-4) .

So pre-update accumulated rewards are always paid out strictly at the pre-update (old) commission percentage — the exact opposite of what the proof idea alleges. The new percentage only takes effect for rewards accrued after this transaction, since `principal` is updated in `request_commission_internal` to `total_active_stake - commission_amount`, resetting the baseline for future reward/commission calculations [6](#0-5) .

Regarding beneficiary redirection: `update_commision` does not touch `BeneficiaryForOperator` at all — that's only mutated by `set_beneficiary_for_operator`, callable exclusively by the operator's own signer [7](#0-6) . `distribute_internal` looks up whatever beneficiary is currently recorded at distribution time via `beneficiary_for_operator(operator)` [8](#0-7) , which is consistent regardless of when `update_commision` runs — there is no cross-field state confusion between commission percentage and beneficiary address.

Since (a) only the staker who owns the contract can trigger this path, and (b) the accounting invariant (old rate applied strictly to pre-update rewards, new rate applied only going forward) is already enforced by the code's ordering, this does not constitute a valid unprivileged vulnerability under the review's Decision Standard.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L566-592)
```text
    public entry fun update_commision(
        staker: &signer, operator: address, new_commission_percentage: u64
    ) acquires Store, BeneficiaryForOperator {
        assert!(
            new_commission_percentage >= 0 && new_commission_percentage <= 100,
            error::invalid_argument(EINVALID_COMMISSION_PERCENTAGE)
        );

        let staker_address = signer::address_of(staker);
        assert!(
            exists<Store>(staker_address),
            error::not_found(ENO_STAKING_CONTRACT_FOUND_FOR_STAKER)
        );

        let store = borrow_global_mut<Store>(staker_address);
        let staking_contract = store.staking_contracts.borrow_mut(&operator);
        distribute_internal(
            staker_address,
            operator,
            staking_contract,
        );
        request_commission_internal(
            operator,
            staking_contract,
        );
        let old_commission_percentage = staking_contract.commission_percentage;
        staking_contract.commission_percentage = new_commission_percentage;
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L637-657)
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L880-886)
```text
        let distribution_pool = &mut staking_contract.distribution_pool;
        update_distribution_pool(
            distribution_pool,
            distribution_amount,
            operator,
            staking_contract.commission_percentage
        );
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L895-898)
```text
            // If the recipient is the operator, send the commission to the beneficiary instead.
            if (recipient == operator) {
                recipient = beneficiary_for_operator(operator);
            };
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L960-975)
```text
    fun get_staking_contract_amounts_internal(
        staking_contract: &StakingContract
    ): (u64, u64, u64) {
        // Pending_inactive is not included in the calculation because pending_inactive can only come from:
        // 1. Outgoing commissions. This means commission has already been extracted.
        // 2. Stake withdrawals from stakers. This also means commission has already been extracted as
        // request_commission_internal is called in unlock_stake
        let (active, _, pending_active, _) =
            stake::get_stake(staking_contract.pool_address);
        let total_active_stake = active + pending_active;
        let accumulated_rewards = total_active_stake - staking_contract.principal;
        let commission_amount =
            accumulated_rewards * staking_contract.commission_percentage / 100;

        (total_active_stake, accumulated_rewards, commission_amount)
    }
```
