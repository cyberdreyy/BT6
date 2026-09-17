### Title
ActivationRegistry `setAdmin` performs a single-step admin transfer with no acceptance safeguard, unlike the two-step pattern used elsewhere in the codebase - (File: `crates/common/precompiles/src/activation/storage.rs`)

### Summary
`ActivationRegistryStorage::set_admin` directly overwrites the stored activation-registry admin in one transaction, with only a zero-address check as a guard [1](#0-0) . This mirrors the reported bug class: a privileged role-transfer function that looks safe but omits the two-step "stage/accept" pattern that the same codebase already implements elsewhere for exactly this purpose.

### Finding Description
The Policy Registry precompile in this same codebase implements admin rotation as an explicit two-step process — `stage_update_admin` writes a pending admin, and only `finalize_update_admin`, called by that pending admin, actually commits the change [2](#0-1) . This pattern exists specifically to protect against transferring admin control to a mistyped, incompatible, or otherwise inoperable address, since the target must actively accept before the transfer takes effect (analogous to `Owner`'s two-step `transferOwnership` in the original report).

`ActivationRegistryStorage::set_admin`, however, performs the rotation in a single step: it validates the caller is the current admin, checks the target isn't the zero address, and then immediately writes the new admin to storage and emits `AdminChanged` — with no staging/pending step and no acceptance by the new admin [1](#0-0) . This is dispatched directly from calldata via `IActivationRegistry::setAdminCall` in `dispatch`/`inner` [3](#0-2) .

If the current admin sends a `setAdmin` transaction with a wrong-but-nonzero address (typo, wrong checksum, an address whose keys are lost, or a contract that cannot originate the transactions this role requires), the transfer is final and irreversible in the same block — there is no recovery path, no pending-state fallback, and no way for the intended new admin to "reject" or fail to accept an invalid transfer the way the two-step pattern would allow.

### Impact Explanation
`ActivationRegistryStorage` gates activation/deactivation of `PolicyRegistry`, `B20Stablecoin`, and `B20Asset` precompile features [4](#0-3) . Losing control of the admin role permanently locks out the ability to `activate`/`deactivate` these features via `require_admin_caller` checks in `set_activated` [5](#0-4) , with no on-chain remedy short of a hard fork/chain-config change. This can permanently freeze the intended governance path for critical B20 token and policy-registry activation state.

### Likelihood Explanation
This requires the legitimate, currently-authorized admin to submit a `setAdmin` transaction with an incorrect (but non-zero) address — the same operator-error scenario the original report flags. Given that the codebase itself demonstrates the safer two-step pattern for the structurally identical Policy Registry admin rotation, the absence of that safeguard here is a genuine gap rather than an intentional design choice, and the same class of mistake (typo, wrong checksummed address, unreachable multisig) is plausible during routine admin rotation.

### Recommendation
Adopt the same stage/finalize two-step pattern used by `PolicyRegistry` (`stage_update_admin` / `finalize_update_admin`) for `ActivationRegistryStorage::set_admin`, requiring the new admin to explicitly finalize the transfer before it takes effect, rather than committing the change in a single transaction from the current admin alone.

### Proof of Concept
1. Current admin `ADMIN` calls `setAdmin(newAdmin)` with a mistyped/incorrect but non-zero `newAdmin` address.
2. `set_admin` passes the zero-address check, verifies `ADMIN` is the current admin via `require_admin_caller`, and writes `newAdmin` directly to storage, emitting `AdminChanged` [6](#0-5) .
3. No confirmation/acceptance step exists; the mistyped address is now the sole admin.
4. Neither `ADMIN` nor anyone else can call `activate`/`deactivate` for `PolicyRegistry`, `B20Stablecoin`, or `B20Asset` going forward, since `require_admin_caller` will only authorize the (unreachable) `newAdmin` [7](#0-6) , permanently freezing activation governance for those features.

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

**File:** crates/common/precompiles/src/activation/storage.rs (L179-221)
```rust
    /// Sets the feature activation state.
    pub fn set_activated(
        &mut self,
        feature: B256,
        to_activated_state: bool,
        admin_config: ActivationAdminConfig,
    ) -> Result<()> {
        // Keep this guard at the shared mutation boundary so `activate`, `deactivate`, and direct
        // `set_activated` callers all get the same static-call behavior after calldata validation.
        if self.storage.is_static() {
            return Err(BasePrecompileError::revert(IActivationRegistry::StaticCallNotAllowed {}));
        }

        let caller = self.require_admin_caller(admin_config)?;

        let current_activated_state = self.features.at(&feature).read()?;

        let is_activating_and_already_activated = to_activated_state && current_activated_state;
        let is_deactivating_and_already_deactivated =
            !to_activated_state && !current_activated_state;

        if is_activating_and_already_activated {
            return Err(BasePrecompileError::revert(IActivationRegistry::AlreadyActivated {
                feature,
            }));
        }
        if is_deactivating_and_already_deactivated {
            return Err(BasePrecompileError::revert(IActivationRegistry::FeatureNotActivated {
                feature,
            }));
        }

        if to_activated_state {
            self.__initialize()?;
            self.features.at_mut(&feature).write(true)?;
            self.emit_event(IActivationRegistry::FeatureActivated { feature, caller })?;
        } else {
            self.features.at_mut(&feature).delete()?;
            self.emit_event(IActivationRegistry::FeatureDeactivated { feature, caller })?;
        }

        Ok(())
    }
```

**File:** crates/common/precompiles/src/policy/logic/v2.rs (L414-435)
```rust
    fn stage_update_admin(
        &self,
        storage: &mut S,
        policy_id: u64,
        new_admin: Address,
    ) -> Result<()> {
        let (_, caller) = self.require_admin(storage, policy_id)?;
        if new_admin == Address::ZERO {
            storage.delete_pending_admin(policy_id)?;
        } else {
            storage.write_pending_admin(policy_id, new_admin)?;
        }
        storage.emit_event(
            IPolicyRegistry::PolicyAdminStaged {
                policyId: policy_id,
                currentAdmin: caller,
                pendingAdmin: new_admin,
            }
            .encode_log_data(),
        )?;
        Ok(())
    }
```

**File:** crates/common/precompiles/src/activation/dispatch.rs (L65-101)
```rust
    fn inner(
        &mut self,
        calldata: &[u8],
        admin_config: ActivationAdminConfig,
    ) -> base_precompile_storage::Result<Bytes> {
        let set_admin_selector = IActivationRegistry::setAdminCall::SELECTOR;
        if !admin_config.state_enabled && calldata.get(..4) == Some(set_admin_selector.as_slice()) {
            return Err(BasePrecompileError::UnknownFunctionSelector(set_admin_selector));
        }

        match decode_precompile_call!(calldata, IActivationRegistry::IActivationRegistryCalls) {
            C::isActivated(call) => {
                let activated = self.is_activated(call.feature)?;
                Ok(IActivationRegistry::isActivatedCall::abi_encode_returns(&activated).into())
            }
            C::checkActivated(call) => {
                self.ensure_activated(call.feature)?;
                Ok(Bytes::new())
            }
            C::activate(call) => {
                self.activate(call.feature, admin_config)?;
                Ok(Bytes::new())
            }
            C::deactivate(call) => {
                self.deactivate(call.feature, admin_config)?;
                Ok(Bytes::new())
            }
            C::setAdmin(call) => {
                self.set_admin(call.newAdmin, admin_config)?;
                Ok(Bytes::new())
            }
            C::admin(_) => {
                Ok(IActivationRegistry::adminCall::abi_encode_returns(&self.admin(admin_config)?)
                    .into())
            }
        }
    }
```
