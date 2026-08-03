No vulnerability found for this question.

**Analysis:**

`set_beneficiary_for_operator` requires the caller to sign as the operator themselves — the function stores the `BeneficiaryForOperator` resource under `signer::address_of(operator)`, and does not accept or validate any pool address [1](#0-0) . The doc comment's claim that it's "permissionless with respect to any specific pool" means the operator doesn't need to prove ownership of a particular staking pool when designating a beneficiary — not that an arbitrary unprivileged address can invoke it on someone else's behalf [2](#0-1) .

Commission is routed via `beneficiary_for_operator(stake::get_operator(pool_address))`, looked up *from the pool's actual operator address*, not from any caller-supplied address [3](#0-2) [4](#0-3) . For a disallowed address to receive commission shares, the pool's legitimate operator would have to voluntarily designate that address as their beneficiary via their own signer — this is an action taken by a role holder (the operator), not something an unprivileged/disallowed address can trigger unilaterally against someone else's pool.

Additionally, the delegator allowlist (`DelegationPoolAllowlisting`) is specifically documented and enforced as gating delegator-initiated stake operations — `add_stake`, `unlock`/`reactivate` paths call `assert_delegator_allowlisted` [5](#0-4) [6](#0-5) . Commission distribution via `buy_in_active_shares`/`buy_in_pending_inactive_shares` for the operator/beneficiary is not gated by the allowlist because it represents the operator's own earned commission (a percentage the operator was already contractually entitled to), not new delegator participation — no delegator value is redirected or diluted; the commission coins would have gone to the operator (or their chosen beneficiary) regardless of allowlist status [7](#0-6) .

Since the scenario requires either (a) the attacker already possessing the operator role for that pool, or (b) the pool's legitimate operator voluntarily naming the disallowed address as beneficiary — both of which are excluded by the Decision Standard ("Reject anything that assumes the attacker already owns the pool, operator role... or requires privileged cooperation") — this does not constitute a valid unprivileged-input vulnerability.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L349-354)
```text
    /// Tracks a delegation pool's allowlist of delegators.
    /// If allowlisting is enabled, existing delegators are not implicitly allowlisted and they can be individually
    /// evicted later by the pool owner.
    struct DelegationPoolAllowlisting has key {
        allowlist: SmartTable<address, bool>,
    }
```

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

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1488-1489)
```text
        let delegator_address = signer::address_of(delegator);
        assert_delegator_allowlisted(pool_address, delegator_address);
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1862-1913)
```text
    /// Calculate stake deviations between the delegation and stake pools in order to
    /// capture the rewards earned in the meantime, resulted operator commission and
    /// whether the lockup expired on the stake pool.
    fun calculate_stake_pool_drift(pool: &DelegationPool): (bool, u64, u64, u64, u64) {
        let (active, inactive, pending_active, pending_inactive) = stake::get_stake(get_pool_address(pool));
        assert!(
            inactive >= pool.total_coins_inactive,
            error::invalid_state(ESLASHED_INACTIVE_STAKE_ON_PAST_OLC)
        );
        // determine whether a new lockup cycle has been ended on the stake pool and
        // inactivated SOME `pending_inactive` stake which should stop earning rewards now,
        // thus requiring separation of the `pending_inactive` stake on current observed lockup
        // and the future one on the newly started lockup
        let lockup_cycle_ended = inactive > pool.total_coins_inactive;

        // actual coins on stake pool belonging to the active shares pool
        active += pending_active;
        // actual coins on stake pool belonging to the shares pool hosting `pending_inactive` stake
        // at current observed lockup cycle, either pending: `pending_inactive` or already inactivated:
        if (lockup_cycle_ended) {
            // `inactive` on stake pool = any previous `inactive` stake +
            // any previous `pending_inactive` stake and its rewards (both inactivated)
            pending_inactive = inactive - pool.total_coins_inactive
        };

        // on stake-management operations, total coins on the internal shares pools and individual
        // stakes on the stake pool are updated simultaneously, thus the only stakes becoming
        // unsynced are rewards and slashes routed exclusively to/out the stake pool

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
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1951-1974)
```text
        // reward operator its commission out of uncommitted pending_inactive rewards
        buy_in_pending_inactive_shares(
            pool,
            beneficiary_for_operator(stake::get_operator(pool_address)),
            commission_pending_inactive
        );

        event::emit_event(
            &mut pool.distribute_commission_events,
            DistributeCommissionEvent {
                pool_address,
                operator: stake::get_operator(pool_address),
                commission_active,
                commission_pending_inactive,
            },
        );

        emit(DistributeCommission {
            pool_address,
            operator: stake::get_operator(pool_address),
            beneficiary: beneficiary_for_operator(stake::get_operator(pool_address)),
            commission_active,
            commission_pending_inactive,
        });
```
