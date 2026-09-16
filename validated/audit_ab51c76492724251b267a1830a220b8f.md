### Title
Missing zero-address check in `grant_role_unchecked` allows granting `DEFAULT_ADMIN_ROLE` (or any role) to `address(0)`, permanently bricking admin-gated token operations - (File: `crates/common/precompiles/src/b20_asset/logic/v1.rs`, `crates/common/precompiles/src/b20_asset/logic/v2.rs`, `crates/common/precompiles/src/b20_stablecoin/logic/v1.rs`, `crates/common/precompiles/src/b20_stablecoin/logic/v2.rs`)

### Summary
The B20 asset/stablecoin precompile role-management logic never validates that `account != Address::ZERO` when granting a role. An admin holding `DEFAULT_ADMIN_ROLE` can call `grantRole` through the precompile dispatcher to grant `DEFAULT_ADMIN_ROLE` to `address(0)`. Because the role-member-count bookkeeping treats this phantom grant as a real admin, the contract's "last admin cannot renounce" safeguard can be bypassed, letting the real admin renounce and leave `address(0)` as the sole role holder — a role no transaction sender can ever satisfy — permanently freezing every admin-gated function of the token (mint, pause, seize, policy updates, metadata updates, etc.).

### Finding Description
`grant_role_unchecked` in both B20 asset and stablecoin logic versions writes the role directly with no zero-address validation: [1](#0-0) 

The same pattern (no `account != Address::ZERO` check) exists in the parallel implementations: [2](#0-1) [3](#0-2) [4](#0-3) 

This is reachable directly from the precompile dispatch table via the `grantRole` selector, callable by any address with `DEFAULT_ADMIN_ROLE` (or role-admin) privileges: [5](#0-4) 

By contrast, other address-accepting entry points such as `transfer` explicitly guard against `Address::ZERO` (`InvalidReceiver`), confirming the codebase's own convention that zero-address recipients require rejection — a convention that was not applied to `grant_role`/`grant_role_unchecked`.

When `role == DefaultAdmin`, granting to `address(0)` increments `role_member_count` for `DefaultAdmin`: [6](#0-5) 

That count is exactly what `revoke_role`/`renounce_role` use to decide whether the *last* admin is being removed: [7](#0-6) [8](#0-7) 

With `address(0)` counted as an admin, the real (only functioning) admin is no longer "sole admin", so `renounce_role`/`revoke_role` will succeed for them, leaving `address(0)` — an address no EVM transaction sender or precompile caller can ever be — as the contract's only role holder.

### Impact Explanation
Once every functioning admin has renounced (permitted only because the zero-address inflated the member count), no account can ever again satisfy `DEFAULT_ADMIN_ROLE` or any role solely held by `address(0)`, since `msg.sender == address(0)` is unreachable in a real transaction. All admin-gated precompile operations on the affected B20 token — granting/revoking further roles, minting, pausing, seizing, updating policies or metadata — become permanently unusable. This is a permanent freezing of the token's administrative control surface, a Medium/High-severity impact under the funds/operations-freezing category.

### Likelihood Explanation
Triggering the bug requires only a single `grantRole` transaction from an existing privileged admin naming `address(0)` as the grantee, followed by a normal `renounceRole`/`revokeRole` call — both are ordinary reachable precompile dispatch paths requiring no special privilege beyond the admin role the caller already legitimately holds. It could occur accidentally (fat-fingered zero address, as in the original report) or be used to intentionally, irrevocably lock a token's admin surface.

### Recommendation
Add an explicit `account != Address::ZERO` check (mirroring the existing `InvalidReceiver`/`NonZeroAddress` pattern used elsewhere in the codebase, e.g. `crates/common/precompiles/src/common/ops/non_zero_address.rs`) inside `grant_role`/`grant_role_unchecked` for all B20 asset and stablecoin logic versions, reverting before the role is written and before `role_member_count` is incremented.

### Proof of Concept
1. Deploy a B20 asset/stablecoin token; `ADMIN` holds `DEFAULT_ADMIN_ROLE` (member count = 1).
2. `ADMIN` calls `grantRole(DEFAULT_ADMIN_ROLE, address(0))` via the precompile dispatch — succeeds because `grant_role_unchecked` performs no zero-address check, bumping `role_member_count(DEFAULT_ADMIN_ROLE)` to 2.
3. `ADMIN` calls `renounceRole(DEFAULT_ADMIN_ROLE, ADMIN)`; the `LastAdminCannotRenounce` guard checks `role_member_count == 1`, which is now false (it's 2), so the renounce succeeds.
4. `DEFAULT_ADMIN_ROLE` is now held solely by `address(0)`. No future transaction can ever supply `caller == address(0)`, so every admin-gated function (`grantRole`, `revokeRole`, `pause`, `mint`, `seize`, policy updates, etc.) reverts with `AccessControlUnauthorizedAccount` forever.

### Citations

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

**File:** crates/common/precompiles/src/b20_asset/logic/v1.rs (L514-519)
```rust
        if role == B20TokenRole::DefaultAdmin.id()
            && token.accounting().has_role(role, account)?
            && token.accounting().role_member_count(role)? == U256::ONE
        {
            return Err(BasePrecompileError::revert(IB20::LastAdminCannotRenounce {}));
        }
```

**File:** crates/common/precompiles/src/b20_asset/logic/v1.rs (L533-539)
```rust
        if role == B20TokenRole::DefaultAdmin.id()
            && token.accounting().has_role(role, caller)?
            && token.accounting().role_member_count(role)? == U256::ONE
        {
            return Err(BasePrecompileError::revert(IB20::LastAdminCannotRenounce {}));
        }
        self.revoke_role_unchecked(token, role, caller, caller)
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

**File:** crates/common/precompiles/src/b20_stablecoin/dispatch.rs (L292-296)
```rust
                C::grantRole(c) => {
                    let caller = ctx.caller();
                    logic.grant_role(self, caller, c.role, c.account, privileged)?;
                    Bytes::new()
                }
```
