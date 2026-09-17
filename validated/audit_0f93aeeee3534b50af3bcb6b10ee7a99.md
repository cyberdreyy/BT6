### Title
Single-step, unconfirmed admin transfer in the Activation Registry precompile - (File: `crates/common/precompiles/src/activation/storage.rs`)

### Summary
The Activation Registry precompile's `set_admin` function rewrites the privileged `admin` slot immediately upon a single call from the current admin, with no staging/acceptance step. This is the same bug class as the reported "2 step ownership transfer" issue: a single transaction can permanently redirect (or, via a typo, permanently lose) control of a critical admin role, even though a same-repo precompile (`PolicyRegistry`) already implements the safer two-step pattern for its own admin role.

### Finding Description
`ActivationRegistryStorage::set_admin` validates the caller is the current admin, checks the target isn't `Address::ZERO`, and then writes the new admin directly and emits `AdminChanged` — all in one atomic step, with no pending-admin staging or acceptance by the new address: [1](#0-0) 

This is dispatched from a single signed transaction to the `IActivationRegistry::setAdmin(address)` selector, reachable once the state-backed admin is enabled at the Cobalt fork: [2](#0-1) [3](#0-2) 

Contrast this with `PolicyRegistry`, which implements the exact "stage-then-finalize" pattern that the external report recommends: `stage_update_admin` records a pending admin, and only `finalize_update_admin`, called by the pending address itself, commits the change: [4](#0-3) [5](#0-4) 

The activation admin is a chain-critical, non-multisig-enforced single role: it gates whether the `PolicyRegistry`, `B20Stablecoin`, and `B20Asset` precompile features can ever be turned on: [6](#0-5) 

and creation of B20 tokens via the factory is directly gated on these activation flags: [7](#0-6) 

### Impact Explanation
Because `setAdmin` is a single unconfirmed step, a fat-fingered address, a typo in tooling, or any operational mistake in the admin key-management flow permanently locks out the intended admin and hands control of the activation registry to whatever address (possibly unowned or already-known-bad) was entered. Whoever holds the new admin key can then unilaterally activate/deactivate `PolicyRegistry`, `B20Stablecoin`, and `B20Asset`, or (if the mistake instead sent it to an unrecoverable address) no one can ever activate/deactivate these features again — a node-wide, permanent freeze of a core system capability with no remediation path, since there's no pending-admin state to cancel or contest.

### Likelihood Explanation
This requires only a single transaction from the current legitimate admin invoking `setAdmin`, which is a normal, expected admin operation (rotating keys, migrating to a multisig, etc.). The absence of a confirmation step means the standard operational risk of "wrong address in one field" turns directly into a permanent, unrecoverable state change — the same likelihood/impact profile the original report flags for `Vault.transferOwnership`, and the codebase already demonstrates awareness of the fix pattern via `PolicyRegistry`'s two-step admin transfer.

### Recommendation
Adopt the same stage/finalize pattern already implemented for `PolicyRegistry` (`stage_update_admin` / `finalize_update_admin` in `crates/common/precompiles/src/policy/logic/v2.rs`) for `ActivationRegistryStorage::set_admin`: store the proposed admin in a separate pending-admin slot, require the pending admin to call a `finalizeSetAdmin`-style function from its own address before the admin slot is actually updated, and support clearing a pending transfer (e.g., pass `address(0)`).

### Proof of Concept
1. Current activation admin `A` calls `IActivationRegistry.setAdmin(newAdmin)` with a mistyped/unintended non-zero address `X` (passes the `ZeroAdminAddress` check).
2. `ActivationRegistryStorage::set_admin` immediately writes `X` to the `admin` storage slot and emits `AdminChanged` — see `crates/common/precompiles/src/activation/storage.rs` lines 149-177.
3. `X` never confirms/accepts the role (no such step exists). If `X` is unowned/unreachable, no address can ever again call `activate`/`deactivate`, permanently freezing the ability to enable `PolicyRegistry`, `B20Stablecoin`, or `B20Asset` on that chain — verified by the guard in `require_admin_caller` used throughout `activate`/`deactivate`/`set_admin` at `crates/common/precompiles/src/activation/storage.rs`.

### Citations

**File:** crates/common/precompiles/src/activation/storage.rs (L52-60)
```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ActivationFeature {
    /// `keccak256("base.policy_registry")`
    PolicyRegistry,
    /// `keccak256("base.b20_stablecoin")`
    B20Stablecoin,
    /// `keccak256("base.b20_asset")`
    B20Asset,
}
```

**File:** crates/common/precompiles/src/activation/storage.rs (L149-177)
```rust
    pub fn set_admin(
        &mut self,
        new_admin: Address,
        admin_config: ActivationAdminConfig,
    ) -> Result<()> {
        if self.storage.is_static() {
            return Err(BasePrecompileError::revert(IActivationRegistry::StaticCallNotAllowed {}));
        }
        if !admin_config.state_enabled {
            return Err(BasePrecompileError::revert(
                IActivationRegistry::AdminStorageNotEnabled {},
            ));
        }
        if new_admin.is_zero() {
            return Err(BasePrecompileError::revert(IActivationRegistry::ZeroAdminAddress {}));
        }

        let caller = self.require_admin_caller(admin_config)?;

        self.__initialize()?;
        self.admin.write(new_admin)?;
        self.emit_event(IActivationRegistry::AdminChanged {
            previousAdmin: caller,
            newAdmin: new_admin,
            caller,
        })?;

        Ok(())
    }
```

**File:** crates/common/precompiles/src/activation/abi.rs (L54-55)
```rust
        /// Sets the activation admin.
        function setAdmin(address newAdmin) external;
```

**File:** crates/common/precompiles/src/activation/dispatch.rs (L149-168)
```rust
    #[test]
    fn dispatch_accepts_set_admin_when_state_backed_admin_is_enabled() {
        let mut storage = HashMapStorageProvider::new(1);
        storage.set_caller(ADMIN);

        let calldata =
            Bytes::from(IActivationRegistry::setAdminCall { newAdmin: NEW_ADMIN }.abi_encode());

        let output = StorageCtx::enter(&mut storage, |ctx| {
            ActivationRegistryStorage::new(ctx).dispatch(
                ctx,
                &calldata,
                STATE_ADMIN_CONFIG,
                BaseUpgrade::Cobalt,
            )
        })
        .expect("setAdmin must not fatally error");

        assert!(output.is_success(), "setAdmin must succeed once state-backed admin is enabled");
    }
```

**File:** crates/common/precompiles/src/policy/logic/v2.rs (L437-459)
```rust
    fn finalize_update_admin(&self, storage: &mut S, policy_id: u64) -> Result<()> {
        let packed = self.require_existing_policy(storage, policy_id)?;
        let pending = storage.read_pending_admin(policy_id)?;
        if pending == Address::ZERO {
            return Err(BasePrecompileError::revert(IPolicyRegistry::NoPendingAdmin {}));
        }
        let caller = storage.caller();
        if pending != caller {
            return Err(BasePrecompileError::revert(IPolicyRegistry::Unauthorized {}));
        }
        let previous_admin = packed.admin();
        storage.write_policy_word(policy_id, packed.with_admin(caller).into_u256())?;
        storage.delete_pending_admin(policy_id)?;
        storage.emit_event(
            IPolicyRegistry::PolicyAdminUpdated {
                policyId: policy_id,
                previousAdmin: previous_admin,
                newAdmin: caller,
            }
            .encode_log_data(),
        )?;
        Ok(())
    }
```

**File:** crates/common/precompiles/src/policy/abi/v2.rs (L36-38)
```rust
        error ChildPoliciesOutsideOfRange();
        /// Introduced in V2 (Cobalt). A composite child must be an existing ALLOWLIST or BLOCKLIST
        /// policy — never a built-in sentinel or another composite.
```

**File:** crates/common/precompiles/src/b20_factory/variant.rs (L105-111)
```rust
    /// Returns the activation feature that controls creation of this variant.
    pub const fn activation_feature(self) -> ActivationFeature {
        match self {
            Self::Asset => ActivationFeature::B20Asset,
            Self::Stablecoin => ActivationFeature::B20Stablecoin,
        }
    }
```
