Based on the evidence gathered, I can partially confirm this but with an important caveat about verification limits.

## Title
Missing allowlist enforcement in `add_stake` allows non-allowlisted delegators to buy active shares - (File: `aptos-move/framework/aptos-framework/sources/delegation_pool.move`)

### Summary
`delegation_pool` implements a `DelegationPoolAllowlisting` feature gated by `assert_delegator_allowlisted` [1](#0-0) . This function is intended to prevent non-allowlisted addresses from participating in a restricted pool. However, based on the code available to me, this check is invoked from `reactivate_stake` [2](#0-1)  but a project-wide search for `assert_delegator_allowlisted` only returns two occurrences in the whole file: its own definition and the single call site inside `reactivate_stake`. No occurrence was found inside `add_stake`, `unlock`, or `withdraw`.

### Finding Description
If `add_stake` does not call `assert_delegator_allowlisted` before invoking `buy_in_active_shares`, any address — allowlisted or not — can join a pool that the owner intended to restrict, simply by calling `add_stake` directly. The owner-only functions `allowlist_delegator`, `remove_delegator_from_allowlist`, and `evict_delegator` [3](#0-2)  only give the owner a reactive remedy (forcibly unlocking an already-joined non-allowlisted delegator's stake) rather than preventing the join in the first place. Additionally, `unlock`/`withdraw` do not re-check allowlist status, so a non-allowlisted delegator who has bought active shares can freely unlock and withdraw their contributed principal and any rewards accrued before the owner notices and calls `evict_delegator`.

This breaks the delegator-role boundary invariant: allowlisting is supposed to gate who is allowed to become/remain a delegator of the pool, but with the check missing on the entry point that actually creates share ownership (`add_stake`), the gate only functions once `reactivate_stake` is called, well after the unauthorized share purchase already occurred.

### Impact Explanation
This is an access-control/compliance bypass on the delegator-role boundary rather than direct theft of other users' funds — an unauthorized delegator only gains the ability to deposit and later withdraw its own principal plus pool rewards, not to redirect other delegators' stake. However, it defeats the entire purpose of `DelegationPoolAllowlisting` (a pool-owner-configured restriction meant to keep unauthorized addresses out of active-share accounting), and it means the owner cannot prevent unauthorized inflow into pool accounting proactively — only clean it up reactively via `evict_delegator` after the fact, during which window the attacker has already earned pool rewards and can unlock/withdraw before eviction occurs.

### Likelihood Explanation
High, if confirmed: `add_stake` is a fully unprivileged, public entry function callable by anyone with `pool_address` and `amount` — no special role or prior allowlist status is checked before the call reaches share-buying logic, assuming the check is indeed absent.

### Recommendation
Add `assert_delegator_allowlisted(pool_address, delegator_address)` at the start of `add_stake`, mirroring the check already present in `reactivate_stake`, so that non-allowlisted addresses are rejected before `buy_in_active_shares` mutates any state.

### Proof of Concept
```
#[test(aptos_framework = @aptos_framework, validator = @0x123, attacker = @0x999)]
#[expected_failure(abort_code = ...EDELEGATOR_NOT_ALLOWLISTED...)]
public entry fun test_add_stake_bypasses_allowlist(
    aptos_framework: &signer,
    validator: &signer,
    attacker: &signer,
) acquires ... {
    initialize_for_test(aptos_framework);
    initialize_test_validator(validator, 100 * ONE_APT, true, true);
    enable_delegation_pool_allowlisting_feature(aptos_framework);

    let validator_address = signer::address_of(validator);
    let pool_address = get_owned_pool_address(validator_address);
    enable_delegators_allowlisting(validator);

    let attacker_address = signer::address_of(attacker);
    account::create_account_for_test(attacker_address);
    stake::mint(attacker, 10 * ONE_APT);

    // attacker is NOT allowlisted; this call should abort but currently succeeds
    add_stake(attacker, pool_address, 10 * ONE_APT);
}
```

**Important caveat:** I was unable to directly retrieve the full literal source text of the `add_stake` function body in this repository via the available search tools (the file is very large and indexing coverage may be incomplete for some sections). My conclusion is based on: (1) a whole-file literal grep for `assert_delegator_allowlisted` returning only 2 matches (the definition and the `reactivate_stake` call site, with none attributable to `add_stake`), and (2) the absence of a negative "non-allowlisted delegator cannot add_stake" test case in the visible `test_delegation_pool_allowlisting_e2e` test, unlike the explicit "delegator cannot reactivate stake" assertion present for `reactivate_stake` [4](#0-3) . Given the size-limited indexing of this file, I recommend starting a Devin session with full filesystem access to directly inspect the `add_stake` function body and confirm definitively whether the `assert_delegator_allowlisted` call is present or missing before treating this as confirmed.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1058-1066)
```text
    fun assert_delegator_allowlisted(
        pool_address: address,
        delegator_address: address,
    ) acquires DelegationPoolAllowlisting {
        assert!(
            delegator_allowlisted(pool_address, delegator_address),
            error::permission_denied(EDELEGATOR_NOT_ALLOWLISTED)
        );
    }
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1426-1469)
```text
    /// Allowlist a delegator as the pool owner.
    public entry fun allowlist_delegator(
        owner: &signer,
        delegator_address: address,
    ) acquires DelegationPoolOwnership, DelegationPoolAllowlisting {
        let pool_address = get_owned_pool_address(signer::address_of(owner));
        assert_allowlisting_enabled(pool_address);

        if (delegator_allowlisted(pool_address, delegator_address)) { return };

        borrow_mut_delegators_allowlist(pool_address).add(delegator_address, true);

        event::emit(AllowlistDelegator { pool_address, delegator_address });
    }

    /// Remove a delegator from the allowlist as the pool owner, but do not unlock their stake.
    public entry fun remove_delegator_from_allowlist(
        owner: &signer,
        delegator_address: address,
    ) acquires DelegationPoolOwnership, DelegationPoolAllowlisting {
        let pool_address = get_owned_pool_address(signer::address_of(owner));
        assert_allowlisting_enabled(pool_address);

        if (!delegator_allowlisted(pool_address, delegator_address)) { return };

        borrow_mut_delegators_allowlist(pool_address).remove(delegator_address);

        event::emit(RemoveDelegatorFromAllowlist { pool_address, delegator_address });
    }

    /// Evict a delegator that is not allowlisted by unlocking their entire stake.
    public entry fun evict_delegator(
        owner: &signer,
        delegator_address: address,
    ) acquires DelegationPoolOwnership, DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage, DelegationPoolAllowlisting {
        let pool_address = get_owned_pool_address(signer::address_of(owner));
        assert_allowlisting_enabled(pool_address);
        assert!(
            !delegator_allowlisted(pool_address, delegator_address),
            error::invalid_state(ECANNOT_EVICT_ALLOWLISTED_DELEGATOR)
        );

        // synchronize pool in order to query latest balance of delegator
        synchronize_delegation_pool(pool_address);
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L1579-1584)
```text
    ) acquires DelegationPool, GovernanceRecords, BeneficiaryForOperator, NextCommissionPercentage, DelegationPoolAllowlisting {
        // short-circuit if amount to reactivate is 0 so no event is emitted
        if (amount == 0) { return };

        let delegator_address = signer::address_of(delegator);
        assert_delegator_allowlisted(pool_address, delegator_address);
```

**File:** aptos-move/framework/aptos-framework/sources/delegation_pool.move (L4882-4884)
```text
        // delegator cannot reactivate stake
        reactivate_stake(delegator_1, pool_address, 50 * ONE_APT);
        assert_delegation(delegator_1_address, pool_address, 0, 0, 4999999999);
```
