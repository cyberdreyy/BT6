No vulnerability found for this question.

**Reasoning:**

The attack requires an unprivileged delegator to submit a transaction where `signer::address_of(delegator) == NULL_SHAREHOLDER` (i.e., `@0x0`). This is not achievable by an ordinary attacker:

1. `NULL_SHAREHOLDER` is defined as the reserved address `@0x0` [1](#0-0) , which is explicitly documented as the "special shareholder temporarily owning the `add_stake` fees" — it is populated programmatically by the framework code itself during `add_stake`, not by any user-supplied signer.

2. In `add_stake`, the delegator's own shares are bought in under `delegator_address = signer::address_of(delegator)` (the caller's real address), while the fee shares are separately bought in under the hardcoded constant `NULL_SHAREHOLDER`, entirely independent of whatever address the caller controls [2](#0-1) . There is no code path where the caller's `delegator_address` argument is used to select `NULL_SHAREHOLDER` — it's a compile-time constant, not attacker-influenced input.

3. To actually mount the described attack, an attacker would need a valid transaction signer whose account address is `0x0`. This address is a reserved/system address with no attacker-controllable private key or authentication key; ordinary account creation and transaction signing flows cannot produce a signer for `0x0` since addresses are derived from public keys (or resource-account derivation schemes), and finding a preimage that hashes to `0x0` is not feasible. No code in `account.move` or the VM grants normal users a signer for this address.

4. The existing test `test_cannot_evict_null_address` demonstrates that `NULL_SHAREHOLDER` accumulates shares purely as a side effect of `add_stake`'s fee mechanism (via `delegator_1`'s ordinary `add_stake` call), not because `delegator_1`'s address equals `0x0` [3](#0-2) . This confirms the premise conflates "fees are attributed to the constant NULL_SHAREHOLDER" with "an attacker can become the NULL_SHAREHOLDER," which are different things.

Since the premise requires the attacker to already control a signer for a reserved system address — something no unprivileged actor can obtain — the described path is infeasible and falls outside the review's unprivileged-entrypoint requirement.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L232-235)
```text
    /// Special shareholder temporarily owning the `add_stake` fees charged during this epoch.
    /// On each `add_stake` operation any resulted fee is used to buy active shares for this shareholder.
    /// First synchronization after this epoch ends will distribute accumulated fees to the rest of the pool as refunds.
    const NULL_SHAREHOLDER: address = @0x0;
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1488-1511)
```text
        let delegator_address = signer::address_of(delegator);
        assert_delegator_allowlisted(pool_address, delegator_address);

        // synchronize delegation and stake pools before any user operation
        synchronize_delegation_pool(pool_address);

        // fee to be charged for adding `amount` stake on this delegation pool at this epoch
        let add_stake_fee = get_add_stake_fee(pool_address, amount);

        let pool = borrow_global_mut<DelegationPool>(pool_address);

        // stake the entire amount to the stake pool
        aptos_account::transfer(delegator, pool_address, amount);
        stake::add_stake(&retrieve_stake_pool_owner(pool), amount);

        // but buy shares for delegator just for the remaining amount after fee
        buy_in_active_shares(pool, delegator_address, amount - add_stake_fee);
        assert_min_active_balance(pool, delegator_address);

        // grant temporary ownership over `add_stake` fees to a separate shareholder in order to:
        // - not mistake them for rewards to pay the operator from
        // - distribute them together with the `active` rewards when this epoch ends
        // in order to appreciate all shares on the active pool atomically
        buy_in_active_shares(pool, NULL_SHAREHOLDER, add_stake_fee);
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L4778-4802)
```text
    #[test(aptos_framework = @aptos_framework, validator = @0x123, delegator_1 = @0x010)]
    #[expected_failure(abort_code = 0x1001b, location = Self)]
    public entry fun test_cannot_evict_null_address(
        aptos_framework: &signer,
        validator: &signer,
        delegator_1: &signer,
    ) acquires DelegationPoolOwnership, DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage, DelegationPoolAllowlisting {
        initialize_for_test(aptos_framework);
        initialize_test_validator(validator, 100 * ONE_APT, true, true);
        enable_delegation_pool_allowlisting_feature(aptos_framework);

        let validator_address = signer::address_of(validator);
        let pool_address = get_owned_pool_address(validator_address);

        let delegator_1_address = signer::address_of(delegator_1);
        account::create_account_for_test(delegator_1_address);

        // add some active shares to NULL_SHAREHOLDER from `add_stake` fee
        stake::mint(delegator_1, 50 * ONE_APT);
        add_stake(delegator_1, pool_address, 50 * ONE_APT);
        assert!(get_delegator_active_shares(borrow_global<DelegationPool>(pool_address), NULL_SHAREHOLDER) != 0, 0);

        enable_delegators_allowlisting(validator);
        evict_delegator(validator, NULL_SHAREHOLDER);
    }
```
