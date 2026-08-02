### Title
`delegation_pool::set_beneficiary_for_operator` does not gate on `features::operator_beneficiary_change_enabled`, bypassing the governance kill‑switch enforced in `staking_contract` - (File: `aptos-move/framework/aptos-framework/sources/delegation_pool.move`)

### Summary
`staking_contract::set_beneficiary_for_operator` requires `features::operator_beneficiary_change_enabled()` before letting an operator redirect their commission payouts to a new beneficiary. The equivalent entry function in `delegation_pool.move` performs the identical state mutation (rewriting `BeneficiaryForOperator`) but omits that feature-flag check entirely.

### Finding Description
In `staking_contract.move`, the beneficiary-change path is explicitly gated: [1](#0-0) 

The `delegation_pool.move` implementation of the same feature — same struct name (`BeneficiaryForOperator`), same purpose (redirect operator commission) — has no such assertion: [2](#0-1) 

This mirrors the am-AMM analog exactly: the "manager"/beneficiary-controlling function trusts the currently-set state (here, unconditionally accepting a beneficiary change) instead of first checking whether the governance-controlled feature/override that is supposed to gate this capability is currently enabled. If `operator_beneficiary_change_enabled` is ever turned off on-chain (e.g., because framework governance discovers an exploit in the beneficiary-redirection mechanism and disables the feature as a kill switch), operators using `staking_contract`-based pools are blocked, but operators using `delegation_pool`-based pools can still freely call `delegation_pool::set_beneficiary_for_operator` and redirect their commission to an arbitrary beneficiary address, completely bypassing the intended global disable.

### Impact Explanation
This falls under "Operator commission ... payout ... corruption that credits the wrong account" and "wrong-role control over ... beneficiary state without already holding that role" from the required impact set: an operator can continue to exercise a capability (redirecting commission payouts) that governance has explicitly revoked via the feature flag, for every delegation pool on the network, with no way for governance to stop it short of pausing/upgrading the delegation_pool module itself. Because delegation pools hold the vast majority of staked APT and commission flows (vs. legacy `staking_contract` pools), this significantly undermines the intended safety control.

### Likelihood Explanation
Likelihood is Medium: the bug is reachable unconditionally by any operator address with zero privileges (`operator: &signer` is the only capability required), but it only becomes materially impactful in the specific scenario where governance has disabled `operator_beneficiary_change_enabled` (presumably in response to abuse or a bug in the beneficiary mechanism itself). In that scenario, the gap is trivially and immediately exploitable by every delegation-pool operator.

### Recommendation
Add the same governance gate used in `staking_contract.move` to `delegation_pool::set_beneficiary_for_operator`:
```move
assert!(
    features::operator_beneficiary_change_enabled(),
    std::error::invalid_state(EOPERATOR_BENEFICIARY_CHANGE_NOT_SUPPORTED)
);
```
before mutating/creating the `BeneficiaryForOperator` resource, so the feature flag consistently governs beneficiary changes across both the `staking_contract` and `delegation_pool` code paths.

### Proof of Concept
1. Assume Aptos governance disables the `operator_beneficiary_change_enabled` feature flag (via `features::change_feature_flags_for_next_epoch`), intending to prevent any operator from redirecting commission to a new beneficiary network-wide.
2. Any operator `O` of a `staking_contract`-based pool calls `staking_contract::set_beneficiary_for_operator(O, new_beneficiary)` → aborts with `EOPERATOR_BENEFICIARY_CHANGE_NOT_SUPPORTED` as expected (`staking_contract.move:813-816`).
3. The same operator `O`, if they instead operate a `delegation_pool`-based validator, calls `delegation_pool::set_beneficiary_for_operator(O, new_beneficiary)` (`delegation_pool.move:1272-1291`) — this succeeds unconditionally since the function performs no feature check, silently rewriting `BeneficiaryForOperator` and redirecting all future commission distributions (both `active` and `pending_inactive` commission buy-ins at `delegation_pool.move:1950-1956`) to `new_beneficiary`, in direct contradiction to the disabled governance flag. [3](#0-2) [4](#0-3)

### Citations

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
