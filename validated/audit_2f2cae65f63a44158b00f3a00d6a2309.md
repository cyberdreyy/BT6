## Title
Activation registry `setAdmin` performs a single-step, unconfirmed admin transfer with no recovery path - (File: `crates/common/precompiles/src/activation/storage.rs`)

## Summary
The Base activation-registry precompile controls whether the dynamic B-20 asset, B-20 stablecoin, and policy-registry precompiles are enabled or disabled network-wide. Its admin address is rotated via a single, unconfirmed `setAdmin(newAdmin)` call, exactly the pattern the source audit finding (M-07) flags for `Vault.sol`: a one-step ownership/admin change with no acceptance step, so a mistaken or malicious transfer to an unreachable/incorrect address permanently and irrecoverably locks out all privileged control. Notably, the codebase already implements the safer two-step pattern for the sibling policy-registry precompile (`stageUpdateAdmin` / `finalizeUpdateAdmin`), showing the activation registry is the outlier that reproduces the exact bug class the report describes.

## Finding Description
`ActivationRegistryStorage::set_admin` writes the new admin directly to storage in one transaction, after only checking that the caller is the current admin and the new address is non-zero: [1](#0-0) 

There is no pending-admin staging, no second confirmation transaction from the new admin, and no way to recover if `newAdmin` is mistyped, is a contract that cannot call back, or the corresponding key is otherwise unusable. Once written, `admin.write(new_admin)` is the sole source of truth for `require_admin_caller`, and the static chain-config fallback (`admin_config.fallback`) is bypassed entirely as soon as the stored admin is non-zero: [2](#0-1) [3](#0-2) 

This is dispatched directly from user calldata via `ActivationRegistryStorage::dispatch` / `dispatch_with_observer`, which is reachable by any account holding the current admin key through an ordinary transaction to `0x8453000000000000000000000000000000000001`: [4](#0-3) 

By contrast, the policy registry precompile in the same crate implements exactly the two-step pattern the report recommends (`stage_update_admin` then `finalize_update_admin`, requiring the new admin to actively confirm before the transfer is finalized): [5](#0-4) 

This inconsistency demonstrates the activation registry was not brought up to the same standard, reproducing the M-07 bug class inside Base's own precompile layer.

## Impact Explanation
The activation admin is the only account that can activate or deactivate the B20Asset, B20Stablecoin, and PolicyRegistry features. If `setAdmin` is called with an incorrect or unreachable address — whether by operator error, key mismanagement, or a compromised-but-still-authorized signer making a final malicious move — admin control over the registry is permanently and irrecoverably lost, since there is no staging/confirmation step and no fallback once the stored admin is non-zero (`admin()` in `storage.rs:105-113` only falls back to the static admin while the stored slot is zero). This permanently freezes the ability to toggle any dependent B-20/policy features (e.g., to deactivate a feature discovered to be defective or exploited), which is a protocol-level governance freeze with no remediation path other than a hard fork/redeploy.

## Likelihood Explanation
Likelihood is comparable to the original finding: it requires either an operator mistake during a legitimate admin rotation (typo'd address, wrong checksum, sending to an address whose key is lost) or a single malicious/compromised signature from the current admin — no attacker-controlled preconditions beyond holding (or briefly controlling) the current admin key are needed, and the call is a normal transaction to a well-known precompile address.

## Recommendation
Adopt the same two-step pattern already used by the policy registry (`stageUpdateAdmin` / `finalizeUpdateAdmin`) for the activation registry's `setAdmin`, e.g. add `stagedAdmin` storage plus a `pendingAdmin()`/`acceptAdmin()` (or `finalizeSetAdmin()`) flow so a rotation only completes once the new admin proves control of the target key with a follow-up transaction, mirroring `ProposableOwnable`/`Ownable2Step` semantics.

## Proof of Concept
1. Current admin `A` calls `IActivationRegistry.setAdmin(newAdmin)` targeting `0x8453...0001`, per the dispatch path in `activation/dispatch.rs:92-95`.
2. `set_admin` in `activation/storage.rs:149-177` immediately writes `newAdmin` to the `admin` slot and emits `AdminChanged`, with no acceptance step.
3. If `newAdmin` was mistyped or its key is unusable, no further transaction (from `A`, from `newAdmin`, or via the static fallback, since the stored slot is now non-zero) can ever call `activate`/`deactivate`/`setAdmin` again — confirmed by `require_admin_caller` (`storage.rs:237-244`) always comparing against the now-unreachable stored admin. [6](#0-5)

### Citations

**File:** crates/common/precompiles/src/activation/storage.rs (L105-113)
```rust
    pub fn admin(&self, admin_config: ActivationAdminConfig) -> Result<Address> {
        if admin_config.state_enabled {
            let stored_admin = self.stored_admin()?;
            if !stored_admin.is_zero() {
                return Ok(stored_admin);
            }
        }
        Ok(admin_config.fallback.unwrap_or(Address::ZERO))
    }
```

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

**File:** crates/common/precompiles/src/activation/storage.rs (L236-244)
```rust
    /// Reverts unless the current caller is the effective activation admin.
    pub fn require_admin_caller(&self, admin_config: ActivationAdminConfig) -> Result<Address> {
        let caller = self.storage.caller();
        let admin = self.authorized_admin(admin_config)?;
        if caller != admin {
            return Err(BasePrecompileError::revert(IActivationRegistry::Unauthorized { caller }));
        }
        Ok(caller)
    }
```

**File:** crates/common/precompiles/src/activation/storage.rs (L455-493)
```rust
    #[test]
    fn set_admin_updates_storage_and_authorization() {
        let mut storage = HashMapStorageProvider::new(1);
        storage.set_caller(ADMIN);

        StorageCtx::enter(&mut storage, |ctx| {
            ActivationRegistryStorage::new(ctx).set_admin(NEW_ADMIN, STATE_ADMIN_CONFIG).unwrap()
        });

        StorageCtx::enter(&mut storage, |ctx| {
            let registry = ActivationRegistryStorage::new(ctx);
            assert_eq!(registry.stored_admin().unwrap(), NEW_ADMIN);
            assert_eq!(registry.admin(STATE_ADMIN_CONFIG).unwrap(), NEW_ADMIN);
            assert_eq!(registry.admin(STATIC_ADMIN_CONFIG).unwrap(), ADMIN);
        });

        let events = storage.get_events(ActivationRegistryStorage::ADDRESS);
        let event = IActivationRegistry::AdminChanged::decode_log_data(events.last().unwrap())
            .expect("admin change event decodes");
        assert_eq!(event.previousAdmin, ADMIN);
        assert_eq!(event.newAdmin, NEW_ADMIN);
        assert_eq!(event.caller, ADMIN);

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

**File:** crates/common/precompiles/src/policy/logic/v1.rs (L264-309)
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

    fn finalize_update_admin(&self, storage: &mut S, policy_id: u64) -> Result<()> {
        let packed = self.require_custom(storage, policy_id)?;
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
