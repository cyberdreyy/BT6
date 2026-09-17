## Analysis

The reported bug class — an owner/authority transferring a critical role to `address(0)`, permanently bricking privileged operations because nobody can ever sign as the zero address — has a direct analog in Base's B20 precompile role-management logic.

`grant_role_unchecked` in both the B20 asset and B20 stablecoin logic modules (v1 and v2) writes `account` into the `DEFAULT_ADMIN_ROLE` role-membership set and bumps the admin count with **no check that `account != Address::ZERO`**: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

The dispatch layer routes `grantRole`/`renounceRole`/`revokeRole`/`setRoleAdmin` calldata straight into these functions with no additional zero-address gate: [5](#0-4) 

By contrast, elsewhere in the same precompile family, transfer-party addresses are strictly validated against zero via the `NonZeroAddress` wrapper (`InvalidReceiver`/`InvalidSender` reverts): [6](#0-5) [7](#0-6)  — but this same discipline is not applied to role-membership addresses.

**Reachable sequence (using the existing `renounceLastAdmin`/`revokeRole` machinery, which itself is exercised in the golden tests):**
1. Current `DEFAULT_ADMIN_ROLE` holder calls `grantRole(DEFAULT_ADMIN_ROLE, address(0))` — succeeds, `role_member_count` becomes 2. [8](#0-7) 
2. The last-admin protection only fires when `role_member_count == 1` [9](#0-8) , so the original admin can now `renounceRole`/`revokeRole` themselves, since count is 2 — leaving `address(0)` as the sole `DEFAULT_ADMIN_ROLE` holder.
3. `address(0)` can never sign a transaction, so `grantRole`, `revokeRole`, `setRoleAdmin`, `updatePolicy` (which requires `DefaultAdmin` [10](#0-9) ), pause/unpause administration, and any future admin recovery are **permanently and irrecoverably locked** — mirroring the TellerV2 report's "nobody can ever act as the new owner" outcome. For the stablecoin variant, this can permanently freeze policy administration (e.g. seize/freeze policies), i.e. funds already frozen under a compliance policy could never be un-frozen because no admin exists to update the policy.

This is a legitimate cross-cutting analog: same root cause (accepting a governance/administrative address transfer without a zero-address guard) and comparable impact (permanent loss of a privileged capability, here compliance/admin functions on a B20 stablecoin/asset token). One caveat: I could not fully confirm within the tool-call budget whether some other layer (e.g. `B20Guards`, `ensure_role_admin_mutations_available`, or the calldata ABI decoding) rejects a zero `account`/`newAdminRole` argument before reaching `grant_role`/`set_role_admin`; grep across `v1.rs`/`v2.rs` showed many `Address::ZERO` occurrences (used for transfer/sender/receiver checks) but I did not find one specifically guarding the `account` parameter of `grant_role`/`grant_role_unchecked`/`set_role_admin`. This should be verified by inspecting `B20Guards::ensure_role`, `ensure_token_role`, and `ensure_role_admin_mutations_available` implementations directly.

### Title
Missing zero-address check in B20 role grant/admin-transfer functions can permanently brick token administration - (File: crates/common/precompiles/src/b20_asset/logic/v1.rs, v2.rs, b20_stablecoin/logic/v1.rs, v2.rs)

### Summary
`grant_role`/`grant_role_unchecked` and `set_role_admin` in the B20 asset and B20 stablecoin precompile logic accept an arbitrary `account`/`newAdminRole` without rejecting `Address::ZERO`, unlike the strict `NonZeroAddress` validation applied to token transfer parties elsewhere in the same precompile family.

### Finding Description
`grant_role_unchecked` sets `role, account -> true` and increments `role_member_count` for `DEFAULT_ADMIN_ROLE` with no validation that `account` isn't `Address::ZERO`. Combined with the last-admin guard only checking `role_member_count == 1`, an admin can grant `DEFAULT_ADMIN_ROLE` to `address(0)` and then revoke/renounce their own admin role, leaving `address(0)` as sole `DEFAULT_ADMIN_ROLE` holder.

### Impact Explanation
Once `address(0)` is the sole `DEFAULT_ADMIN_ROLE` holder, all admin-gated precompile operations (`grantRole`, `revokeRole`, `setRoleAdmin`, `updatePolicy`, and, transitively, policy-gated compliance actions such as un-freezing/un-seizing accounts on the stablecoin) become permanently unreachable, since no key exists for the zero address. This is a permanent loss of administrative capability and, for the stablecoin's policy-gated flows, can result in permanent freezing of funds already subject to a compliance policy.

### Likelihood Explanation
Requires action by the current `DEFAULT_ADMIN_ROLE` holder (privileged party), either accidental (fat-fingered address, misconfigured deployment script) or intentional to "renounce" administration — directly analogous to the referenced report's `transferMarketOwnership(0)` scenario.

### Recommendation
Reject `Address::ZERO` as the `account` argument in `grant_role`/`grant_role_unchecked` (at minimum for `DEFAULT_ADMIN_ROLE`), mirroring the `NonZeroAddress` pattern already used for transfer parties, or explicitly support "renounce to nobody" only via a dedicated, clearly-named entrypoint if that is an intended admin-abdication feature.

### Proof of Concept
Not independently executed; derived from static analysis of: [11](#0-10) [12](#0-11)

### Citations

**File:** crates/common/precompiles/src/b20_asset/logic/v1.rs (L461-499)
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

**File:** crates/common/precompiles/src/b20_asset/logic/v1.rs (L586-588)
```rust
        if !privileged {
            B20Guards::ensure_token_role(token, caller, B20TokenRole::DefaultAdmin)?;
        }
```

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L246-250)
```rust
        B20Guards::ensure_not_paused(token, IB20::PausableFeature::TRANSFER)?;
        let to = NonZeroAddress::new(to)
            .map_err(|_| BasePrecompileError::revert(IB20::InvalidReceiver { receiver: to }))?;
        let from = NonZeroAddress::new(caller)
            .map_err(|_| BasePrecompileError::revert(IB20::InvalidSender { sender: caller }))?;
```

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L570-590)
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

**File:** crates/common/precompiles/src/b20_stablecoin/logic/v1.rs (L453-473)
```rust
    fn grant_role_unchecked(
        &self,
        token: &mut B20StablecoinToken<S, A>,
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

**File:** crates/common/precompiles/src/b20_stablecoin/logic/v2.rs (L529-549)
```rust
    fn grant_role_unchecked(
        &self,
        token: &mut B20StablecoinToken<S, A>,
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

**File:** crates/common/precompiles/src/b20_asset/dispatch.rs (L304-326)
```rust
            // --- Role mutations ---
            C::grantRole(c) => {
                logic.grant_role(self, caller, c.role, c.account, privileged)?;
                Bytes::new()
            }
            C::revokeRole(c) => {
                logic.revoke_role(self, caller, c.role, c.account, privileged)?;
                Bytes::new()
            }
            // Renounce operations are never factory-privileged: they are only meaningful for the
            // role holder making the call after token creation.
            C::renounceRole(c) => {
                logic.renounce_role(self, caller, c.role, c.callerConfirmation)?;
                Bytes::new()
            }
            C::renounceLastAdmin(_) => {
                logic.renounce_last_admin(self, caller)?;
                Bytes::new()
            }
            C::setRoleAdmin(c) => {
                logic.set_role_admin(self, caller, c.role, c.newAdminRole, privileged)?;
                Bytes::new()
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
