No vulnerability found for this question.

**Analysis:**

The premise depends on the admin check in `set_management_role` being "bypassed via signer confusion," but no such bypass exists in the code. `set_management_role` calls `verify_admin(admin, vesting_contract)`, which strictly asserts `signer::address_of(admin) == vesting_contract.admin`, aborting with `ENOT_ADMIN` otherwise [1](#0-0) [2](#0-1) . There is no signer-substitution or capability-forwarding path in this function that would let an unprivileged caller pass this check.

`reset_beneficiary` itself independently re-checks the caller: it requires `addr == vesting_contract.admin || addr == get_role_holder(contract_address, ROLE_BENEFICIARY_RESETTER)`, aborting with `EPERMISSION_DENIED` otherwise [3](#0-2) . So even if `set_management_role` were somehow abusable, `reset_beneficiary` does not blindly trust a forged role — it looks up the actual role holder recorded on-chain via `get_role_holder`.

Notably, the exact scenario described in the "Proof idea" — an unauthorized signer calling `reset_beneficiary` and expecting an `EPERMISSION_DENIED` abort with no beneficiary mapping change — already exists as a passing unit test in the codebase: `test_reset_beneficiary_with_unauthorized`, which asserts `#[expected_failure(abort_code = 0x5000F, location = Self)]` [4](#0-3) . This confirms the guarded behavior is already enforced and tested, not a gap.

Additionally, the formal spec for `reset_beneficiary` and `set_management_role` (`SetManagementRoleAbortsIf`, `VerifyAdminAbortsIf`) encode the same admin-only invariant and are checked by the Move Prover [5](#0-4) [6](#0-5) .

Finally, the cited file (`aptos-move/aptos-vm/src/sharded_block_executor/remote_state_value.rs`) is unrelated to the vesting module entirely and has no bearing on the described attack path, further indicating a mismatch between the claimed vulnerable file and the actual code discussed.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L950-967)
```text
    public entry fun reset_beneficiary(
        account: &signer,
        contract_address: address,
        shareholder: address,
    ) acquires VestingAccountManagement, VestingContract {
        let vesting_contract = borrow_global_mut<VestingContract>(contract_address);
        let addr = signer::address_of(account);
        assert!(
            addr == vesting_contract.admin ||
                addr == get_role_holder(contract_address, utf8(ROLE_BENEFICIARY_RESETTER)),
            error::permission_denied(EPERMISSION_DENIED),
        );

        let beneficiaries = &mut vesting_contract.beneficiaries;
        if (beneficiaries.contains_key(&shareholder)) {
            beneficiaries.remove(&shareholder);
        };
    }
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L969-990)
```text
    public entry fun set_management_role(
        admin: &signer,
        contract_address: address,
        role: String,
        role_holder: address,
    ) acquires VestingAccountManagement, VestingContract {
        let vesting_contract = borrow_global<VestingContract>(contract_address);
        verify_admin(admin, vesting_contract);

        if (!exists<VestingAccountManagement>(contract_address)) {
            let contract_signer = &get_vesting_account_signer_internal(vesting_contract);
            move_to(contract_signer, VestingAccountManagement {
                roles: simple_map::create<String, address>(),
            })
        };
        let roles = &mut borrow_global_mut<VestingAccountManagement>(contract_address).roles;
        if (roles.contains_key(&role)) {
            *roles.borrow_mut(&role) = role_holder;
        } else {
            roles.add(role, role_holder);
        };
    }
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L1052-1054)
```text
    fun verify_admin(admin: &signer, vesting_contract: &VestingContract) {
        assert!(signer::address_of(admin) == vesting_contract.admin, error::unauthenticated(ENOT_ADMIN));
    }
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L1993-2009)
```text
    #[test(aptos_framework = @0x1, admin = @0x123, resetter = @0x234, random = @0x345)]
    #[expected_failure(abort_code = 0x5000F, location = Self)]
    public entry fun test_reset_beneficiary_with_unauthorized(
        aptos_framework: &signer,
        admin: &signer,
        resetter: &signer,
        random: &signer,
    ) acquires AdminStore, VestingAccountManagement, VestingContract {
        let admin_address = signer::address_of(admin);
        setup(aptos_framework, &vector[admin_address, @11]);
        let contract_address = setup_vesting_contract(
            admin, &vector[@11], &vector[GRANT_AMOUNT], admin_address, 0);

        // Reset the beneficiary with a random account. This should failed.
        set_beneficiary_resetter(admin, contract_address, signer::address_of(resetter));
        reset_beneficiary(random, contract_address, @11);
    }
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.spec.move (L430-458)
```text
    spec reset_beneficiary(
        account: &signer,
        contract_address: address,
        shareholder: address,
    ) {
        aborts_if !exists<VestingContract>(contract_address);

        let addr = signer::address_of(account);
        let vesting_contract = global<VestingContract>(contract_address);
        aborts_if addr != vesting_contract.admin && !std::string::spec_internal_check_utf8(ROLE_BENEFICIARY_RESETTER);
        aborts_if addr != vesting_contract.admin && !exists<VestingAccountManagement>(contract_address);
        let roles = global<VestingAccountManagement>(contract_address).roles;
        let role = std::string::spec_utf8(ROLE_BENEFICIARY_RESETTER);
        aborts_if addr != vesting_contract.admin && !simple_map::spec_contains_key(roles, role);
        aborts_if addr != vesting_contract.admin && addr != simple_map::spec_get(roles, role);

        let post post_vesting_contract = global<VestingContract>(contract_address);
        ensures !simple_map::spec_contains_key(post_vesting_contract.beneficiaries,shareholder);
    }

    spec set_management_role(
        admin: &signer,
        contract_address: address,
        role: String,
        role_holder: address,
    ) {
        pragma aborts_if_is_partial;
        include SetManagementRoleAbortsIf;
    }
```

**File:** aptos-move/framework/aptos-framework/sources/vesting.spec.move (L617-624)
```text
    spec schema SetManagementRoleAbortsIf {
        contract_address: address;
        admin: signer;
        aborts_if !exists<VestingContract>(contract_address);
        let vesting_contract = global<VestingContract>(contract_address);
        // aborts_if !exists<VestingAccountManagement>(contract_address) ==> exists<VestingAccountManagement>(vesting_contract.signer_cap.account);
        aborts_if signer::address_of(admin) != vesting_contract.admin;
    }
```
