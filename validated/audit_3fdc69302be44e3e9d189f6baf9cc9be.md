No vulnerability found for this question.

**Reasoning:**

The premise that repeated, delegator-triggered calls to `synchronize_delegation_pool` cause commission to be "computed and paid out multiple times" is incorrect. The commission calculation in `calculate_stake_pool_drift` is delta-based, not cumulative-based: it computes commission only on the *newly accrued* stake-pool rewards since the last synchronization, by comparing the stake pool's current `active`/`pending_inactive` amounts against the delegation pool's already-recorded `pool.active_shares.total_coins()` / `pending_inactive_shares_pool(pool).total_coins()`. [1](#0-0) 

Once `synchronize_delegation_pool` runs, it immediately commits that delta by calling `pool.active_shares.update_total_coins(...)` and `pending_inactive_shares_pool_mut(pool).update_total_coins(...)`, then mints commission shares to whichever address `beneficiary_for_operator(...)` resolves to at that exact moment via `buy_in_active_shares`/`buy_in_pending_inactive_shares`. [2](#0-1) 

Because the recorded totals are updated as part of the same synchronization call, a second, third, or Nth call to `synchronize_delegation_pool` within the same lockup/epoch window will see `active == pool_active` (no new stake-pool drift has occurred), so `commission_active`/`commission_pending_inactive` compute to zero — there is nothing left to pay. Commission is only non-zero again once the underlying stake pool actually earns further rewards. So repeatedly calling `add_stake`, `unlock`, or `vote` to trigger extra syncs cannot re-trigger payment of the same reward window twice; each unit of reward is committed to shares (i.e., "paid") exactly once, permanently, to whichever beneficiary was current at that particular sync call.

If an attacker interleaves `set_beneficiary_for_operator` calls with sync-triggering calls, the only effect is that whichever reward-accrual window occurs *before* a given sync goes to the beneficiary that was active *at that sync time* — this is explicitly documented and intended behavior: "Any existing unpaid commission rewards will be paid to the new beneficiary. To ensure payment to the current beneficiary, one should first call `synchronize_delegation_pool` before switching the beneficiary." [3](#0-2) 

This same design and warning is mirrored in `staking_contract::set_beneficiary_for_operator`. [4](#0-3) 

The framework's own test `test_set_beneficiary_for_operator` demonstrates the expected, non-corrupting behavior: commission accrued and synchronized while operator1 is beneficiary is paid to operator1, and commission accrued after `set_beneficiary_for_operator` is switched goes to the new beneficiary, with no double payment or loss. [5](#0-4) 

Since an unprivileged delegator has no ability to mint additional stake-pool rewards out of thin air (rewards only accrue via real epoch/validator activity, not via calling `synchronize_delegation_pool` itself), and the commission delta is zeroed out immediately upon being credited, there is no path for double payment, short payment, or redirection of commission that isn't already the documented, intended consequence of the operator/beneficiary voluntarily switching addresses mid-cycle. The role and accounting invariants (commission computed once per real reward delta, credited to the beneficiary current at that moment) already block the described attack.

### Citations

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

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1891-1912)
```text
        // operator `active` rewards not persisted yet to the active shares pool
        let pool_active = pool.active_shares.total_coins();
        let commission_active = if (active > pool_active) {
            math64::mul_div(active - pool_active, pool.operator_commission_percentage, MAX_FEE)
        } else {
            // handle any slashing applied to `active` stake
            0
        };
        // operator `pending_inactive` rewards not persisted yet to the pending_inactive shares pool
        let pool_pending_inactive = pending_inactive_shares_pool(pool).total_coins();
        let commission_pending_inactive = if (pending_inactive > pool_pending_inactive) {
            math64::mul_div(
                pending_inactive - pool_pending_inactive,
                pool.operator_commission_percentage,
                MAX_FEE
            )
        } else {
            // handle any slashing applied to `pending_inactive` stake
            0
        };

        (lockup_cycle_ended, active, pending_inactive, commission_active, commission_pending_inactive)
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1939-1956)
```text
        // distribute rewards remaining after commission, to delegators (to already existing shares)
        // before buying shares for the operator for its entire commission fee
        // otherwise, operator's new shares would additionally appreciate from rewards it does not own

        // update total coins accumulated by `active` + `pending_active` shares
        // redeemed `add_stake` fees are restored and distributed to the rest of the pool as rewards
        pool.active_shares.update_total_coins(active - commission_active);
        // update total coins accumulated by `pending_inactive` shares at current observed lockup cycle
        pending_inactive_shares_pool_mut(pool).update_total_coins(pending_inactive - commission_pending_inactive);

        // reward operator its commission out of uncommitted active rewards (`add_stake` fees already excluded)
        buy_in_active_shares(pool, beneficiary_for_operator(stake::get_operator(pool_address)), commission_active);
        // reward operator its commission out of uncommitted pending_inactive rewards
        buy_in_pending_inactive_shares(
            pool,
            beneficiary_for_operator(stake::get_operator(pool_address)),
            commission_pending_inactive
        );
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L3757-3780)
```text
        // produce active and pending_inactive rewards
        end_aptos_epoch();
        stake::assert_stake_pool(pool_address, 101000000000000, 0, 0, 101000000000000);
        assert_delegation(operator1_address, pool_address, 126500000000, 0, 126500000000);
        end_aptos_epoch();
        stake::assert_stake_pool(pool_address, 102010000000000, 0, 0, 102010000000000);
        assert_delegation(operator1_address, pool_address, 254265000000, 0, 254265000000);
        timestamp::fast_forward_seconds(LOCKUP_CYCLE_SECONDS);
        end_aptos_epoch();

        withdraw(operator1, pool_address, ONE_APT);
        assert!(coin::balance<AptosCoin>(operator1_address) == ONE_APT - 1, 0);

        set_beneficiary_for_operator(operator1, beneficiary_address);
        assert!(beneficiary_for_operator(operator1_address) == beneficiary_address, 0);
        end_aptos_epoch();

        unlock(beneficiary, pool_address, ONE_APT);
        timestamp::fast_forward_seconds(LOCKUP_CYCLE_SECONDS);
        end_aptos_epoch();

        withdraw(beneficiary, pool_address, ONE_APT);
        assert!(coin::balance<AptosCoin>(beneficiary_address) == ONE_APT - 1, 0);
        assert!(coin::balance<AptosCoin>(operator1_address) == ONE_APT - 1, 0);
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
