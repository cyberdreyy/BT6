### Title
No two-step confirmation for the Activation Registry admin rotation permits irreversible loss of admin control - (File: `crates/common/precompiles/src/activation/storage.rs`)

### Summary
The `ActivationRegistryStorage::set_admin` function immediately overwrites the sole admin address for the on-chain Activation Registry precompile in a single transaction, with no staged proposal/acceptance step. A mistaken or unreachable `newAdmin` value permanently and irrecoverably strips control of the registry from anyone, since the new address (or a wrong address) becomes the only account able to reverse the mistake.

### Finding Description
`set_admin` is dispatched from `ActivationRegistryStorage::inner` on the `setAdmin` selector [1](#0-0)  and performs the rotation directly:

```rust
pub fn set_admin(
    &mut self,
    new_admin: Address,
    admin_config: ActivationAdminConfig,
) -> Result<()> {
    ...
    let caller = self.require_admin_caller(admin_config)?;
    self.__initialize()?;
    self.admin.write(new_admin)?;
    self.emit_event(IActivationRegistry::AdminChanged { ... })?;
    Ok(())
}
``` [2](#0-1) 

Unlike `PolicyRegistry`, which the same codebase implements with an explicit two-step `stage_update_admin` / `finalize_update_admin` flow requiring the *new* admin to accept the role before the transfer completes [3](#0-2) , the Activation Registry writes the new admin to storage in the very same call that the current admin authorizes, with only a non-zero-address check [4](#0-3) . There is no mechanism for the incoming address to accept the role, and no way to recover if the value is wrong (typo, wrong checksum, an address with no corresponding key, or a contract that cannot call back into the precompile).

Once `admin` storage is non-zero, it permanently overrides the static chain-config fallback admin [5](#0-4) , and `require_admin_caller` gates every subsequent privileged call — `activate`, `deactivate`, and `setAdmin` itself — on matching that stored address [6](#0-5) . The Activation Registry is the gate that turns the `PolicyRegistry`, `B20Stablecoin`, and `B20Asset` native precompiles on or off [7](#0-6) .

### Impact Explanation
If the admin (a chain-governance-controlled key, e.g. a multisig) submits `setAdmin` with an incorrect or unreachable address in a single mistaken transaction, control of the Activation Registry is permanently lost. Because there is no fallback once `admin` storage is non-zero and no recovery path, this permanently freezes the ability to `activate`/`deactivate` the `B20Stablecoin`, `B20Asset`, and `PolicyRegistry` features going forward — including the inability to ever deactivate a compromised or buggy B20 token/policy precompile in an emergency. This is a permanent, protocol-level loss of governance capability over core native-token infrastructure, matching a Medium/High severity misconfiguration-freezing class of bug, directly analogous to the reported `OwnershipFacet.transferOwnership` issue.

### Likelihood Explanation
This requires the current activation admin (a privileged key) to make a single mistaken or maliciously-tricked call; it is not exploitable by an arbitrary unprivileged sender. Likelihood is therefore tied to operational error or key-management failure by the governance signer(s), the same scenario described in the original report's exploit scenario (wrong address supplied during a routine key rotation).

### Recommendation
Adopt the same two-step pattern already used for `PolicyRegistry` admin rotation (`stage_update_admin` / `finalize_update_admin`) for the Activation Registry: have the current admin stage a pending new admin, and require the pending admin to submit a confirming transaction before `admin` storage is overwritten, with an explicit cancel/expire path for staged proposals.

### Proof of Concept
1. Deploy/enable the state-backed Activation Registry admin (`ActivationAdminConfig::state_backed`).
2. Current admin calls `setAdmin(newAdmin)` where `newAdmin` is mistyped or is an address with no known private key, as shown to succeed in `set_admin_updates_storage_and_authorization` [8](#0-7) .
3. `admin` storage is now permanently set to the wrong address; `require_admin_caller` rejects the old admin, and no address can ever call `setAdmin`, `activate`, or `deactivate` again for `PolicyRegistry`, `B20Stablecoin`, or `B20Asset`.

### Citations

**File:** crates/common/precompiles/src/activation/dispatch.rs (L92-95)
```rust
            C::setAdmin(call) => {
                self.set_admin(call.newAdmin, admin_config)?;
                Ok(Bytes::new())
            }
```

**File:** crates/common/precompiles/src/activation/storage.rs (L62-76)
```rust
impl ActivationFeature {
    /// Returns the `keccak256` hash that identifies this feature in storage.
    pub const fn id(self) -> B256 {
        match self {
            Self::PolicyRegistry => {
                b256!("0xb582ebae03f16fee49a6763f78df482fb11ae73f103ed0d330bbe556aa90a43f")
            }
            Self::B20Stablecoin => {
                b256!("0xecfa0def2c10020caaf65e6155aa69c84b24892aaef76eeac52e0e2b3a0b8601")
            }
            Self::B20Asset => {
                b256!("0xcdcc772fe4cbdb1029f822861176d09e646db96723d4c1e82ddfdeb8163ef54c")
            }
        }
    }
```

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

**File:** crates/common/precompiles/src/policy/logic/v2.rs (L1119-1131)
```rust
    // --- two-step admin transfer ---

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
