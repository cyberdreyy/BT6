[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L361-368)
```text
    /// Return the beneficiary address of the operator.
    public fun beneficiary_for_operator(operator: address): address acquires BeneficiaryForOperator {
        if (exists<BeneficiaryForOperator>(operator)) {
            return borrow_global<BeneficiaryForOperator>(operator).beneficiary_for_operator
        } else {
            operator
        }
    }
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L697-719)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L1010-1036)
```text
        // Charge all stakeholders (except for the operator themselves) commission on any rewards earnt relatively to the
        // previous value of the distribution pool.
        let shareholders = &distribution_pool.shareholders();
        shareholders.for_each_ref(
            |shareholder| {
                let shareholder: address = *shareholder;
                if (shareholder != operator) {
                    let shares = pool_u64::shares(distribution_pool, shareholder);
                    let previous_worth = pool_u64::balance(distribution_pool, shareholder);
                    let current_worth =
                        pool_u64::shares_to_amount_with_total_coins(
                            distribution_pool, shares, updated_total_coins
                        );
                    let unpaid_commission =
                        (current_worth - previous_worth) * commission_percentage / 100;
                    // Transfer shares from current shareholder to the operator as payment.
                    // The value of the shares should use the updated pool's total value.
                    let shares_to_transfer =
                        pool_u64::amount_to_shares_with_total_coins(
                            distribution_pool, unpaid_commission, updated_total_coins
                        );
                    pool_u64::transfer_shares(
                        distribution_pool, shareholder, operator, shares_to_transfer
                    );
                };
            }
        );
```

**File:** aptos-move/framework/cached-packages/src/aptos_framework_sdk_builder.rs (L5042-5045)
```rust
/// Allows an operator to change its beneficiary. Any existing unpaid commission rewards will be paid to the new
/// beneficiary. To ensures payment to the current beneficiary, one should first call `distribute` before switching
/// the beneficiary. An operator can set one beneficiary for staking contract pools, not a separate one for each pool.
pub fn staking_contract_set_beneficiary_for_operator(
```
