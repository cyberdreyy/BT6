Based on my investigation, I've confirmed the analog vulnerability. The `ActivationRegistry` precompile implements an atomic, single-step admin-change process via `setAdmin`, exactly analogous to the reported `Timelock.changeOwner` bug, whereas the sibling `PolicyRegistry` precompile in the same codebase deliberately implements a two-step `stageUpdateAdmin`/`finalizeUpdateAdmin` process to guard against this exact risk.

### Title
Activation Registry `setAdmin` performs an atomic, irreversible admin rotation with no two-step confirmation - (File: `crates/common/precompiles/src/activation/storage.rs`)

### Summary
`ActivationRegistryStorage::set_admin` changes the state-backed activation-registry admin in a single atomic call, unlike the codebase's own `PolicyRegistry` precompile, which uses a two-step stage/finalize admin-transfer pattern specifically to prevent irrecoverable admin loss.

### Finding Description
`set_admin` validates static-call, feature-enablement, and zero-address conditions, then immediately writes `new_admin` to storage and emits `AdminChanged`, requiring only that the caller pass `require_admin_caller` [1](#0-0) . This is dispatched directly from ABI calldata via the `setAdmin` selector in `ActivationRegistryStorage::inner`, reachable by any signed transaction from the current admin address once state-backed admin storage is enabled at/after the Cobalt upgrade [2](#0-1) . There is no staging/pending-admin mechanism or acceptance step required from the new address — the transfer is final the moment the transaction lands. By contrast, the `PolicyRegistry` precompile in the very same codebase implements `stage_update_admin` and `finalize_update_admin`, where a new admin must call `finalizeUpdateAdmin` itself before the change takes effect, explicitly guarding against a mistyped/incorrect admin address bricking control of a policy [3](#0-2) . The activation registry has no equivalent safety net.

### Impact Explanation
The activation-registry admin exclusively gates `activate`/`deactivate` of chain features such as `PolicyRegistry`, `B20Stablecoin`, and `B20Asset` via `require_admin_caller` in `set_activated` [4](#0-3) . If the current admin submits `setAdmin` with a mistyped or otherwise uncontrolled address, admin authority over feature activation is permanently and irrecoverably lost: no further `activate`/`deactivate`/`setAdmin` calls can ever succeed since `require_admin_caller` will reject every future caller, matching the "node halt"/"permanent freezing of protocol capability" impact bar (no path exists to activate a currently-inactive B20 feature, or deactivate/patch a currently-active one, ever again).

### Likelihood Explanation
This requires the legitimate admin to make an operational error (as in the original report) — no attacker action is needed to trigger the loss, only a single incorrect legitimate transaction, and once activation-admin key management is state-backed (Cobalt+), it is the sole path to update this admin, since no fallback recovery exists once the address rotation is state-backed and non-zero.

### Recommendation
Adopt the same two-step admin-transfer pattern already implemented for `PolicyRegistry` (`stageUpdateAdmin`/`finalizeUpdateAdmin`, with `finalizeUpdateAdmin` requiring `msg.sender == pending_admin`) for `ActivationRegistryStorage::set_admin`, rather than writing `new_admin` directly on a single authenticated call.

### Proof of Concept
1. State-backed activation admin is enabled at Cobalt (`admin_config.state_enabled = true`).
2. Current admin `A` calls `setAdmin(newAdmin)` with an address `X` for which no private key/contract deployer exists (typo or unreachable address) — `set_admin` succeeds unconditionally as long as `X != 0` [5](#0-4) .
3. `admin` storage now permanently holds `X`; `require_admin_caller` will never authorize any account (verified analogously by `dispatch_accepts_set_admin_when_state_backed_admin_is_enabled` and `set_admin_updates_storage_and_authorization` tests showing the prior admin is immediately and permanently unauthorized post-rotation) [6](#0-5) .
4. No transaction can ever call `activate`/`deactivate`/`setAdmin` successfully again — the activation registry's admin-gated functionality is permanently frozen.

### Citations

**File:** crates/common/precompiles/src/activation/storage.rs (L148-177)
```rust
    /// Sets the activation registry admin address.
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

**File:** crates/common/precompiles/src/activation/storage.rs (L179-196)
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
```

**File:** crates/common/precompiles/src/activation/storage.rs (L478-493)
```rust
        let old_admin_err = StorageCtx::enter(&mut storage, |ctx| {
            ActivationRegistryStorage::new(ctx).activate(FEATURE, STATE_ADMIN_CONFIG)
        })
        .unwrap_err();
        assert_eq!(
            old_admin_err,
            BasePrecompileError::revert(IActivationRegistry::Unauthorized { caller: ADMIN })
        );

        storage.set_caller(NEW_ADMIN);
        StorageCtx::enter(&mut storage, |ctx| {
            ActivationRegistryStorage::new(ctx).activate(FEATURE, STATE_ADMIN_CONFIG)
        })
        .unwrap();
        assert_activated(&mut storage, true);
    }
```

**File:** crates/common/precompiles/src/activation/dispatch.rs (L70-95)
```rust
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
```

**File:** crates/common/precompiles/src/policy/logic/v2.rs (L1121-1131)
```rust
    #[test]
    fn admin_transfer_two_step() {
        let mut rt = initialized();
        let id = create_allowlist(&mut rt);
        LOGIC.stage_update_admin(&mut rt, id, NEW_ADMIN).unwrap();
        set_caller(&mut rt, NEW_ADMIN);
        LOGIC.finalize_update_admin(&mut rt, id).unwrap();
        LOGIC.update_allowlist(&mut rt, id, true, vec![ALICE]).unwrap();
        assert!(is_authorized(&rt, id, ALICE));
        assert_eq!(LOGIC.get_policy_admin(&rt, id).unwrap(), NEW_ADMIN);
    }
```
