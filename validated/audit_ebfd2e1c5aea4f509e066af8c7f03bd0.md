No vulnerability found for this question.

**Analysis:**

The reported behavior is exactly how `synchronize_delegation_pool` is documented and intended to work, not a bug. The function reads `beneficiary_for_operator(stake::get_operator(pool_address))` at the moment it is called and buys shares for whatever address is *currently* registered as the beneficiary at that time via `buy_in_active_shares`/`buy_in_pending_inactive_shares`. [1](#0-0) 

Since `synchronize_delegation_pool` is explicitly permissionless (any caller may invoke it to settle accrued rewards before any user operation), an unprivileged caller invoking it simply causes uncommitted commission to be settled to *the beneficiary registered at that instant* — which is precisely the invariant the question itself states should hold ("commission always credits the operator's currently registered beneficiary"). There is no "stale beneficiary" being used; the lookup is always fresh at call time. [2](#0-1) 

The module's own doc comment on `set_beneficiary_for_operator` explicitly documents the ordering dependency: "To ensure payment to the current beneficiary, one should first call `synchronize_delegation_pool` before switching the beneficiary." This means the code acknowledges that unsynchronized commission accrued *before* a beneficiary switch is paid to whoever holds the beneficiary role at the time of the next sync — this is a known, intentional design decision, not an exploitable misrouting bug, since only the operator itself (a privileged role) can change its own beneficiary via `set_beneficiary_for_operator`. [3](#0-2) 

The existing Move unit test `test_set_beneficiary_for_operator` demonstrates and validates exactly this behavior: commission is paid to whichever beneficiary is registered at each `synchronize_delegation_pool`/settlement point, and switching operator or beneficiary correctly redirects *future* accrued rewards to the new party while past-settled rewards remain with the prior recipient — confirming the accounting invariant holds, not that it's broken. [4](#0-3) 

No unprivileged actor gains the ability to redirect commission to an address of their choosing — only the operator (already a privileged role assumption explicitly excluded by the review's decision standard) can call `set_beneficiary_for_operator`, and the timing of calling permissionless `synchronize_delegation_pool` only affects which of the operator's own already-authorized beneficiary addresses (past vs. new, both operator-controlled) receives already-accrued vs. future commission — it does not let an attacker steal or misdirect funds to an account they don't already control.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L899-907)
```text
    #[view]
    /// Return the beneficiary address of the operator.
    public fun beneficiary_for_operator(operator: address): address acquires BeneficiaryForOperator {
        if (exists<BeneficiaryForOperator>(operator)) {
            return borrow_global<BeneficiaryForOperator>(operator).beneficiary_for_operator
        } else {
            operator
        }
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

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1949-1956)
```text
        // reward operator its commission out of uncommitted active rewards (`add_stake` fees already excluded)
        buy_in_active_shares(pool, beneficiary_for_operator(stake::get_operator(pool_address)), commission_active);
        // reward operator its commission out of uncommitted pending_inactive rewards
        buy_in_pending_inactive_shares(
            pool,
            beneficiary_for_operator(stake::get_operator(pool_address)),
            commission_pending_inactive
        );
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L3770-3792)
```text
        set_beneficiary_for_operator(operator1, beneficiary_address);
        assert!(beneficiary_for_operator(operator1_address) == beneficiary_address, 0);
        end_aptos_epoch();

        unlock(beneficiary, pool_address, ONE_APT);
        timestamp::fast_forward_seconds(LOCKUP_CYCLE_SECONDS);
        end_aptos_epoch();

        withdraw(beneficiary, pool_address, ONE_APT);
        assert!(coin::balance<AptosCoin>(beneficiary_address) == ONE_APT - 1, 0);
        assert!(coin::balance<AptosCoin>(operator1_address) == ONE_APT - 1, 0);

        // switch operator to operator2. The rewards should go to operator2 not to the beneficiay of operator1.
        set_operator(operator1, operator2_address);
        end_aptos_epoch();
        unlock(operator2, pool_address, ONE_APT);
        timestamp::fast_forward_seconds(LOCKUP_CYCLE_SECONDS);
        end_aptos_epoch();

        withdraw(operator2, pool_address, ONE_APT);
        assert!(coin::balance<AptosCoin>(beneficiary_address) == ONE_APT - 1, 0);
        assert!(coin::balance<AptosCoin>(operator2_address) == ONE_APT - 1, 0);
    }
```
