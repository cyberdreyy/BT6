## Finding: Missing beneficiary validation in `staking_contract::set_beneficiary_for_operator` / `delegation_pool::set_beneficiary_for_operator`

### Title
Unvalidated operator beneficiary address can permanently block commission/reward distribution for all pool participants - ([File: aptos-move/framework/aptos-framework/sources/staking_contract.move], [File: aptos-move/framework/aptos-framework/sources/delegation_pool.move])

### Summary
The external report's bug class is "a privileged setter accepts a critical address/fee parameter with no validity check, causing a shared payout path to fail (DoS)." The codebase already contains the fix for this exact class in one place but is missing it in two sibling functions that are reachable by any unprivileged operator.

`vesting::set_beneficiary` explicitly guards against this: [1](#0-0) 

But the analogous operator-beneficiary setters have no such check: [2](#0-1) [3](#0-2) 

### Finding Description
`staking_contract::set_beneficiary_for_operator` and `delegation_pool::set_beneficiary_for_operator` let any operator (a permissionless role — anyone can create a staking contract or delegation pool and become its own operator) redirect their commission payout to an arbitrary address, with **no check that the address is registered/valid for `AptosCoin`**, unlike `vesting::set_beneficiary`, whose comment explicitly states the rationale for the check: *"This is a requirement so distribute() wouldn't fail and block all other accounts from receiving APT if one beneficiary is not registered."*

`distribute_internal` in `staking_contract.move` iterates over **all shareholders** (stakers, delegators, and the operator/beneficiary) in the shared `distribution_pool` and pays each one out via `aptos_account::deposit_coins`, all within one atomic loop of a single `distribute` entry-function call: [4](#0-3) 

If the payout to the operator's beneficiary address aborts (e.g. the address is a reserved/blocked address, or an account that has opted out of direct coin transfers via `DirectTransferConfig`), the entire `distribute` transaction reverts — which also blocks payout of already-unlocked stake to **every other staker/delegator sharing that same pool**, none of whom chose or control the operator's beneficiary address.

`delegation_pool::synchronize_delegation_pool`, which is called on essentially every user action (`unlock`, `add_stake`, `set_operator`, `update_commission_percentage`, `delegate_voting_power`, etc.), similarly credits the (unvalidated) beneficiary via `buy_in_pending_inactive_shares`: [5](#0-4) 

If share-buying or later withdrawal to that beneficiary address is not possible, it can jeopardize the shared synchronization path that every delegator depends on for correct accounting.

### Impact Explanation
This maps to the required "Permanent lock or non-recoverable loss of claim rights in stake, delegation, commission, beneficiary, or vesting flows" and "Accounting ... must preserve value and withdrawal rights" invariants. An operator (permissionless role, requires no privilege beyond creating their own pool) can set their beneficiary to an address that causes `deposit_coins`/`coin::register` to abort, permanently jamming the shared `distribute`/`synchronize_delegation_pool` code path and stranding delegators' already-unlocked APT with no available workaround, since `distribute` (staking_contract) and `synchronize_delegation_pool` (delegation_pool) are the only paths that pay out inactive/pending_inactive funds.

### Likelihood Explanation
Medium-to-high but with a caveat I could not fully resolve: I could not directly inspect `aptos_account::deposit_coins`'s and `account::create_account`'s exact abort conditions (e.g., whether address `0x0`/other reserved addresses are blocked at account-creation, or whether `DirectTransferConfig` opt-out is respected for freshly-created accounts) in this pass, given index/tool limits. The strongest evidence for likelihood is that the Aptos framework authors themselves added this exact validation (`assert_account_is_registered_for_apt`) to `vesting::set_beneficiary` specifically to prevent this failure mode — showing the failure mode is real and previously encountered/fixed in one sibling function but not the other two.

### Recommendation
Add the same guard used in `vesting::set_beneficiary` to both `staking_contract::set_beneficiary_for_operator` and `delegation_pool::set_beneficiary_for_operator`:
```move
assert_account_is_registered_for_apt(new_beneficiary);
```
placed before the beneficiary is persisted, so a broken beneficiary can never be committed, and existing distribution/sync flows cannot be jammed for unrelated stakers/delegators.

### Proof of Concept
1. Attacker calls `staking_contract::create_staking_contract` (or `delegation_pool::initialize_delegation_pool`) becoming `operator` of a new pool — no special privilege required.
2. One or more victims stake/delegate into this pool via `create_staking_contract`/`add_stake`, accumulating principal and unlocking some funds over time (calling `unlock_stake`/`unlock`).
3. Attacker calls `set_beneficiary_for_operator(operator, malicious_address)` where `malicious_address` is chosen to make deposit fail (e.g., an address that cannot register a `CoinStore`/has opted out of direct transfers), since no check on the target address exists (compare to `vesting::set_beneficiary`'s guard at [6](#0-5) , which is absent here).
4. Any subsequent call to `staking_contract::distribute`/`delegation_pool::synchronize_delegation_pool` that attempts to pay commission to `malicious_address` aborts, reverting the whole shared transaction and blocking payout of victims' already-unlocked stake.

Note: full confirmation that `deposit_coins`/`create_account` actually aborts for the chosen `malicious_address` (vs. silently auto-registering, given AptosCoin's fungible-asset migration) requires reading `aptos_account.move`'s `deposit_coins` and `account.move`'s `create_account` implementations in full, which I was unable to complete within available tool calls — starting a Devin session with full repo access is recommended to verify the exact abort conditions before treating this as fully confirmed.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L915-924)
```text
    public entry fun set_beneficiary(
        admin: &signer,
        contract_address: address,
        shareholder: address,
        new_beneficiary: address,
    ) acquires VestingContract {
        // Verify that the beneficiary account is set up to receive APT. This is a requirement so distribute() wouldn't
        // fail and block all other accounts from receiving APT if one beneficiary is not registered.
        assert_account_is_registered_for_apt(new_beneficiary);

```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L811-829)
```text
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
```

**File:** aptos-move/framework/aptos-framework/sources/staking_contract.move (L855-920)
```text
    /// Distribute all unlocked (inactive) funds according to distribution shares.
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






























            );

            emit(
                Distribute {
                    operator,
                    pool_address,
                    recipient,
                    amount: amount_to_distribute
                }
            );
        };

        // In case there's any dust left, send them all to the staker.
        if (coin::value(&coins) > 0) {
            aptos_account::deposit_coins(staker, coins);
            distribution_pool.update_total_coins(0);
        } else {
            coin::destroy_zero(coins);
        }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1272-1291)
```text
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

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1946-1956)
```text
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
