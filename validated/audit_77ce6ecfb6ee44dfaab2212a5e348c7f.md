No vulnerability found for this question.

**Rationale:**

The premise of this finding does not match how `beneficiary_for_operator` resolution actually works in `staking_contract.move`. The operator identity used inside `distribute_internal` is not derived from any "current operator" state read that could be raced via `CrossShardStateView` — it is a plain function argument fixed by the entry-function call itself: [1](#0-0) 

`distribute(staker, operator)` takes `operator` as an explicit parameter, looks up the `StakingContract` keyed by that exact address in `Store.staking_contracts`, and passes that same fixed `operator` value into `distribute_internal`, which in turn calls `beneficiary_for_operator(operator)` with that same fixed address: [2](#0-1) 

There is no code path where `distribute_internal` reads "the operator currently recorded" from some other resource that could be stale due to cross-shard timing — it operates purely on the caller-supplied `operator` address and the `StakingContract` value already borrowed from the `Store`.

Separately, `BeneficiaryForOperator` is a resource stored under the operator's own address and is only mutated by that operator calling `set_beneficiary_for_operator`: [3](#0-2) 

`switch_operator` (and `stake::set_operator_with_cap`) never writes to `BeneficiaryForOperator`, so there is no write from an operator-switch transaction that a `distribute` transaction could possibly read "stale." Additionally, `switch_operator` explicitly forces `distribute_internal` and `request_commission_internal` for the **old** operator before moving the `StakingContract` entry to the new operator key, which drains the `distribution_pool` shareholders (the `while` loop in `distribute_internal` always empties the pool): [4](#0-3) 

This exact scenario — commission continuing to accrue correctly to the new operator (and not leaking to the old operator's beneficiary) after a switch — is covered by an existing test: [5](#0-4) 

Regarding the sharded-executor mechanism itself: `CrossShardStateView`'s per-round rebuild and `RemoteStateValue::waiting()`/blocking-`get_value()` design is exactly the synchronization primitive meant to prevent stale cross-shard reads — a dependent transaction blocks on a condition variable until the declared writer transaction has committed and pushed its value: [6](#0-5) [7](#0-6) 

Required cross-shard edges are derived deterministically from static read/write-set analysis during partitioning, not something an unprivileged attacker can manipulate to force an incorrect stale read while bypassing this blocking mechanism.

Because the beneficiary resolution is fixed by the transaction's own `operator` argument (not by any racy shared-state read), and because `switch_operator` already forces full distribution/re-request of commission for the old operator before the switch commits, there is no path by which an unprivileged attacker can redirect commission to a stale beneficiary via cross-shard timing.

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

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L888-898)
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
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L1794-1822)
```text
        // switch operator to operator2. The rewards should go to operator2 not to the beneficiay of operator1.
        let old_beneficiay_balance = beneficiary_balance;
        switch_operator(
            staker,
            operator1_address,
            operator2_address,
            10
        );

        stake::end_epoch();
        let (_, accumulated_rewards, _) =
            staking_contract_amounts(staker_address, operator2_address);

        let expected_commission = accumulated_rewards / 10;

        // Request commission.
        request_commission(operator2, staker_address, operator2_address);
        // Unlocks the commission.
        stake::fast_forward_to_unlock(pool_address);
        expected_commission = with_rewards(expected_commission);

        // Distribute the commission to the operator.
        distribute(staker_address, operator2_address);

        // Assert that the rewards go to operator2, and the balance of the operator1's beneficiay remains the same.
        assert!(coin::balance<AptosCoin>(operator2_address) >= expected_commission, 1);
        assert!(
            coin::balance<AptosCoin>(beneficiary_address) == old_beneficiay_balance, 1
        );
```

**File:** aptos-move/aptos-vm/src/sharded_block_executor/cross_shard_state_view.rs (L26-56)
```rust
    pub fn new(cross_shard_keys: HashSet<StateKey>, base_view: &'a S) -> Self {
        let mut cross_shard_data = HashMap::new();
        trace!(
            "Initializing cross shard state view with {} keys",
            cross_shard_keys.len(),
        );
        for key in cross_shard_keys {
            cross_shard_data.insert(key, RemoteStateValue::waiting());
        }
        Self {
            cross_shard_data,
            base_view,
        }
    }

    #[cfg(test)]
    fn waiting_count(&self) -> usize {
        self.cross_shard_data
            .values()
            .filter(|v| !v.is_ready())
            .count()
    }

    pub fn set_value(&self, state_key: &StateKey, state_value: Option<StateValue>) {
        self.cross_shard_data
            .get(state_key)
            .unwrap()
            .set_value(state_value);
        // uncomment the following line to debug waiting count
        // trace!("waiting count for shard id {} is {}", self.shard_id, self.waiting_count());
    }
```

**File:** aptos-move/aptos-vm/src/sharded_block_executor/remote_state_value.rs (L22-39)
```rust
    pub fn set_value(&self, value: Option<StateValue>) {
        let (lock, cvar) = &*self.value_condition;
        let mut status = lock.lock().unwrap();
        *status = RemoteValueStatus::Ready(value);
        cvar.notify_all();
    }

    pub fn get_value(&self) -> Option<StateValue> {
        let (lock, cvar) = &*self.value_condition;
        let mut status = lock.lock().unwrap();
        while let RemoteValueStatus::Waiting = *status {
            status = cvar.wait(status).unwrap();
        }
        match &*status {
            RemoteValueStatus::Ready(value) => value.clone(),
            RemoteValueStatus::Waiting => unreachable!(),
        }
    }
```
