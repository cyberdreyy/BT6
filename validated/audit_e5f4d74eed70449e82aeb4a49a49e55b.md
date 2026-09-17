### Title
Missing zero-address check in `grant_role`/`grant_role_unchecked` allows the `DEFAULT_ADMIN_ROLE` to be granted to `address(0)`, permanently bricking token administration - ([File: crates/common/precompiles/src/b20_asset/logic/v1.rs])

### Summary
The B20 token role-management logic (`grant_role` / `grant_role_unchecked`) does not validate that `account != Address::ZERO` before granting a role. When the granted role is `DefaultAdmin`, this zero address is counted as a legitimate role member in `role_member_count`, which is exactly the counter used by the "last admin can't renounce" safety check. An existing admin can therefore grant `DEFAULT_ADMIN_ROLE` to `address(0)` and then safely renounce their own admin role, since the member count no longer reads `1`. The token is left with `DEFAULT_ADMIN_ROLE` held only by `address(0)`, an address that can never sign a transaction, permanently freezing all admin-gated functionality.

### Finding Description
`grant_role_unchecked` blindly calls `set_role(role, account, true)` and, for `DefaultAdmin`, increments `role_member_count` with no check that `account` is non-zero: [1](#0-0) 

The only guard preventing loss of adminship, `revoke_role`/`renounce_role`, relies exclusively on `role_member_count(role) == U256::ONE`: [2](#0-1) [3](#0-2) 

Because `grant_role_unchecked` never rejects `Address::ZERO`, a legitimate admin can call `grantRole(DEFAULT_ADMIN_ROLE, address(0))`, bumping `role_member_count` to `2` as shown by the analogous test for a real second admin: [4](#0-3) 

With the counter no longer at `1`, the admin can then call `renounceRole`/`revokeRole` on their own address, and the guard at lines 501-520/614-631 passes (member count is `2`, not `1`), even though `address(0)` can never submit a transaction to exercise the role. The same unguarded pattern exists identically in `crates/common/precompiles/src/b20_asset/logic/v2.rs` (lines 570-590), `crates/common/precompiles/src/b20_stablecoin/logic/v1.rs` (lines 453-473), and `crates/common/precompiles/src/b20_stablecoin/logic/v2.rs` (lines 529-549).

This differs from other places in the same codebase that do enforce zero-address checks, e.g. `ActivationRegistryStorage::set_admin` explicitly reverts with `ZeroAdminAddress` for `Address::ZERO`: [5](#0-4) 
and `PolicyRegistry`'s `createPolicy` explicitly reverts on a zero admin: [6](#0-5) 
showing the B20 asset/stablecoin `grant_role` path is inconsistent and missing the equivalent protection.

### Impact Explanation
Once `address(0)` is the sole (or last-effective) holder of `DEFAULT_ADMIN_ROLE`, every admin-gated B20 operation on that token becomes permanently unreachable: `ensure_role_admin_mutations_available` checks, granting/revoking any role (mint, metadata, policy roles), and policy updates gated on `DefaultAdmin` (see `golden_update_policy_unprivileged_requires_role` requiring `DefaultAdmin`) can never again be authorized, since nobody can transact as `address(0)`. This is a permanent freezing of a critical, privileged capability of a live B20 token deployed through the factory precompile — matching the "permanent freezing of funds/functionality" impact bar, not merely a low-severity nuisance.

### Likelihood Explanation
This requires only a normal, authorized transaction from an existing `DefaultAdmin` holder (an unprivileged-relative-to-protocol but legitimately privileged token admin) calling `grantRole(DEFAULT_ADMIN_ROLE, address(0))` followed by `renounceRole`/`revokeRole` on themselves — both are standard, already-exposed entry points with no special preconditions, so likelihood of accidental or malicious triggering is realistic (e.g., a scripting/typo error zeroing out the account, or a malicious insider admin deliberately bricking the token's governance for griefing).

### Recommendation
Add an explicit zero-address check in `grant_role`/`grant_role_unchecked` (and the analogous stablecoin logic) that reverts (e.g., with an `InvalidReceiver`/`ZeroAddress`-style error) when `account == Address::ZERO`, mirroring the pattern already used in `ActivationRegistryStorage::set_admin` and `PolicyRegistry::createPolicy`.

### Proof of Concept
1. Factory creates a B20 asset token with `initialAdmin = ADMIN`, granting `ADMIN` the `DEFAULT_ADMIN_ROLE` (`role_member_count == 1`).
2. `ADMIN` calls `grantRole(DEFAULT_ADMIN_ROLE, address(0))` — succeeds via `grant_role_unchecked` at [1](#0-0) , bumping `role_member_count` to `2` (as validated for a real second admin in the golden test at [4](#0-3) ).
3. `ADMIN` calls `renounceRole(DEFAULT_ADMIN_ROLE, ADMIN)`. The `LastAdminCannotRenounce` guard at [7](#0-6)  compares `role_member_count(role) == U256::ONE`, which is `2`, so the check passes and `ADMIN`'s role is revoked.
4. `DEFAULT_ADMIN_ROLE` is now held solely by `address(0)`; no further admin action (grant/revoke role, policy update, etc.) can ever be authorized on that token again.

### Citations

**File:** crates/common/precompiles/src/b20_asset/logic/v1.rs (L479-499)
```rust
    fn grant_role_unchecked(
        &self,
        token: &mut B20AssetToken<S, A>,
        role: B256,
        account: Address,
        sender: Address,
    ) -> Result<()> {
        if token.accounting().has_role(role, account)? {
            return Ok(());
        }
        token.accounting_mut().set_role(role, account, true)?;
        if role == B20TokenRole::DefaultAdmin.id() {
            let current = token.accounting().role_member_count(role)?;
            let next =
                current.checked_add(U256::ONE).ok_or_else(BasePrecompileError::under_overflow)?;
            token.accounting_mut().set_role_member_count(role, next)?;
        }
        token
            .accounting_mut()
            .emit_event(IB20::RoleGranted { role, account, sender }.encode_log_data())
    }
```

**File:** crates/common/precompiles/src/b20_asset/logic/v1.rs (L501-520)
```rust
    fn revoke_role(
        &self,
        token: &mut B20AssetToken<S, A>,
        caller: Address,
        role: B256,
        account: Address,
        privileged: bool,
    ) -> Result<()> {
        if !privileged {
            self.ensure_role_admin_mutations_available(token, caller)?;
            let admin = token.accounting().role_admin(role)?;
            B20Guards::ensure_role(token, caller, admin)?;
        }
        if role == B20TokenRole::DefaultAdmin.id()
            && token.accounting().has_role(role, account)?
            && token.accounting().role_member_count(role)? == U256::ONE
        {
            return Err(BasePrecompileError::revert(IB20::LastAdminCannotRenounce {}));
        }
        self.revoke_role_unchecked(token, role, account, caller)
```

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L614-631)
```rust
    fn renounce_role(
        &self,
        token: &mut B20AssetToken<S, A>,
        caller: Address,
        role: B256,
        confirmation: Address,
    ) -> Result<()> {
        if confirmation != caller {
            return Err(BasePrecompileError::revert(IB20::AccessControlBadConfirmation {}));
        }
        if role == B20TokenRole::DefaultAdmin.id()
            && token.accounting().has_role(role, caller)?
            && token.accounting().role_member_count(role)? == U256::ONE
        {
            return Err(BasePrecompileError::revert(IB20::LastAdminCannotRenounce {}));
        }
        self.revoke_role_unchecked(token, role, caller, caller)
    }
```

**File:** crates/common/precompiles/tests/b20_stablecoin_v2_golden.rs (L1503-1522)
```rust
#[test]
fn golden_grant_default_admin_bumps_member_count() {
    let mut s = fresh();
    seed(&mut s, |t| give_role(t, B20TokenRole::DefaultAdmin.id(), ADMIN));
    let out = op_privileged(
        &mut s,
        ADMIN,
        FakePolicyAccounting::new(),
        IB20::grantRoleCall { role: B20TokenRole::DefaultAdmin.id(), account: ALICE }.abi_encode(),
    )
    .unwrap();

    assert!(out.is_empty());
    read(&mut s, |t| {
        assert!(t.has_role(B20TokenRole::DefaultAdmin.id(), ALICE).unwrap());
        assert_eq!(t.role_member_count(B20TokenRole::DefaultAdmin.id()).unwrap(), u(2));
    });
    assert_eq!(last_topic0(&s), IB20::RoleGranted::SIGNATURE_HASH);
    assert_root("grant_default_admin", s, ROOT_GRANT_DEFAULT_ADMIN);
}
```

**File:** crates/common/precompiles/src/activation/storage.rs (L511-522)
```rust
    #[test]
    fn set_admin_reverts_for_zero_address() {
        let mut storage = HashMapStorageProvider::new(1);
        storage.set_caller(ADMIN);

        let err = StorageCtx::enter(&mut storage, |ctx| {
            ActivationRegistryStorage::new(ctx).set_admin(Address::ZERO, STATE_ADMIN_CONFIG)
        })
        .unwrap_err();

        assert_eq!(err, BasePrecompileError::revert(IActivationRegistry::ZeroAdminAddress {}));
    }
```

**File:** crates/common/precompiles/tests/b20_policy_v2_golden.rs (L343-357)
```rust
#[test]
fn golden_create_reverts_zero_admin() {
    let mut s = fresh();
    let (rev, bytes) = call_policy(
        &mut s,
        ADMIN,
        IPolicyRegistry::createPolicyCall {
            admin: Address::ZERO,
            policyType: PolicyType::BLOCKLIST,
        }
        .abi_encode(),
    );
    assert!(rev);
    assert_eq!(bytes, Bytes::from(IPolicyRegistry::ZeroAddress {}.abi_encode()));
}
```
