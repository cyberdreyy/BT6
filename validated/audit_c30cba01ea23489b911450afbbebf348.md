## Title
`grantRole` for `DEFAULT_ADMIN_ROLE` accepts `address(0)`, allowing an admin to permanently brick B-20 token administration - (File: `crates/common/precompiles/src/b20_asset/logic/v1.rs` / `v2.rs`, `crates/common/precompiles/src/b20_stablecoin/logic/v1.rs` / `v2.rs`)

### Summary
The `grant_role` / `grant_role_unchecked` logic for the B20 asset and B20 stablecoin precompiles never validates that `account != Address::ZERO` before storing a role membership and bumping `role_member_count`. Because the "last admin" protection in `revoke_role`/`renounce_role` only compares `role_member_count(role) == 1`, an admin can grant `DEFAULT_ADMIN_ROLE` to `address(0)` and then safely revoke/renounce their own admin role (count is now 2, so the "last admin" guard doesn't trigger), leaving the token permanently governed by the unreachable zero address.

### Finding Description
`grant_role_unchecked` performs no zero-address validation on `account` before granting a role: [1](#0-0) 

The public entry point `grant_role` only gates on caller authorization, not on the target account: [2](#0-1) 

The "last admin" protections in `revoke_role`/`renounce_role` key exclusively off `role_member_count`, not off whether the remaining holder(s) are reachable addresses: [3](#0-2) 

Unlike transfer-style operations (`transfer`, `seizeWithMemo`), which are wrapped with an `InvalidReceiver`/zero-address check via `NonZeroAddress::new`, role management does not use this same guard: [4](#0-3) 

An existing admin (who already holds `DEFAULT_ADMIN_ROLE` and thus passes `ensure_role_admin_mutations_available`/`B20Guards::ensure_role`) can therefore:
1. Call `grantRole(DEFAULT_ADMIN_ROLE, address(0))` — this succeeds, setting `has_role(DEFAULT_ADMIN_ROLE, address(0)) = true` and incrementing `role_member_count` to 2.
2. Call `renounceRole`/`revokeRole` on themselves — this succeeds because `role_member_count() == 2 != 1`, so the `LastAdminCannotRenounce` guard in `renounce_last_admin`/`revoke_role` does not trigger: [5](#0-4) 

After step 2, the sole remaining `DEFAULT_ADMIN_ROLE` holder is `address(0)`, which can never sign a transaction. All admin-gated operations (role management, `setRoleAdmin`, `updatePolicy`, pause controls, etc.) become permanently inaccessible for the lifetime of the token contract — the exact "loss of ownership" bug class described in the referenced report, where missing `address(0)` validation on an ownership/role-transfer path leads to unrecoverable loss of administrative control.

### Impact Explanation
Once `DEFAULT_ADMIN_ROLE` is left solely with `address(0)`, the token becomes permanently ungovernable: no account can grant new roles, change policies, adjust pausable features, or perform any `DefaultAdmin`-gated recovery action. Any funds or functionality gated behind admin-controlled features (e.g. policy-restricted transfers, pause/unpause, fee-related admin functions) become permanently frozen with no on-chain path to recovery, matching the "loss of ownership leading to stuck funds" impact class of the reference finding. This applies to both B20 asset and stablecoin precompiles (v1 and v2 logic).

### Likelihood Explanation
This requires the acting account to already hold `DEFAULT_ADMIN_ROLE` (a privileged but ordinary transaction sender reachable via the B20 precompile dispatch), and requires two of the admin's own transactions (a mistaken `grantRole` to `address(0)` followed by `renounceRole`/`revokeRole` on themselves). This is a plausible operational/admin-tooling mistake (e.g., mis-encoded calldata, copy-paste error, or a misguided attempt to "burn" the admin role by transferring to zero) rather than requiring any external attacker, matching the "Medium" classification and unprivileged-mistake framing in the original report.

### Recommendation
Add an explicit zero-address check in `grant_role`/`grant_role_unchecked` (and analogous stablecoin logic) that reverts with `IB20::InvalidReceiver`/a dedicated `ZeroAddressError` when `account == Address::ZERO`, consistent with the existing `NonZeroAddress` guard used for transfer/seize paths. This prevents the zero address from ever holding `DEFAULT_ADMIN_ROLE` and closes the "safe-looking" bypass of the last-admin protection.

### Proof of Concept
1. Deploy a B20 asset/stablecoin token via the factory with `initialAdmin = ADMIN`, so `ADMIN` holds `DEFAULT_ADMIN_ROLE` and `role_member_count(DEFAULT_ADMIN_ROLE) == 1`.
2. `ADMIN` calls `grantRole(DEFAULT_ADMIN_ROLE, address(0))` — succeeds per `grant_role_unchecked`, `role_member_count` becomes 2, `has_role(DEFAULT_ADMIN_ROLE, address(0)) == true`.
3. `ADMIN` calls `renounceRole(DEFAULT_ADMIN_ROLE, ADMIN)` (or `revokeRole`) — succeeds because `role_member_count() == 2`, bypassing the `LastAdminCannotRenounce` check.
4. Now `role_member_count(DEFAULT_ADMIN_ROLE) == 1` and the sole holder is `address(0)`. No further admin action (grant, revoke, setRoleAdmin, policy update, pause) is ever possible again.

### Citations

**File:** crates/common/precompiles/src/b20_asset/logic/v1.rs (L461-477)
```rust
    fn grant_role(
        &self,
        token: &mut B20AssetToken<S, A>,
        caller: Address,
        role: B256,
        account: Address,
        privileged: bool,
    ) -> Result<()> {
        if role == B20TokenRole::DefaultAdmin.id() || !privileged {
            self.ensure_role_admin_mutations_available(token, caller)?;
        }
        if !privileged {
            let admin = token.accounting().role_admin(role)?;
            B20Guards::ensure_role(token, caller, admin)?;
        }
        self.grant_role_unchecked(token, role, account, caller)
    }
```

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

**File:** crates/common/precompiles/src/common/ops/non_zero_address.rs (L17-21)
```rust
impl NonZeroAddress {
    /// Returns a non-zero address, or [`ZeroAddressError`] if `address` is zero.
    pub fn new(address: Address) -> Result<Self, ZeroAddressError> {
        if address == Address::ZERO { Err(ZeroAddressError) } else { Ok(Self(address)) }
    }
```

**File:** crates/common/precompiles/src/b20_stablecoin/logic/v2.rs (L592-606)
```rust
    fn renounce_last_admin(
        &self,
        token: &mut B20StablecoinToken<S, A>,
        caller: Address,
    ) -> Result<()> {
        let admin_role = B20TokenRole::DefaultAdmin.id();
        B20Guards::ensure_role(token, caller, admin_role)?;
        if token.accounting().role_member_count(admin_role)? != U256::ONE {
            return Err(BasePrecompileError::revert(IB20::NotSoleAdmin {}));
        }
        self.revoke_role_unchecked(token, admin_role, caller, caller)?;
        token
            .accounting_mut()
            .emit_event(IB20::LastAdminRenounced { previousAdmin: caller }.encode_log_data())
    }
```
