### Title
Activation registry `set_admin` uses a single-step admin rotation with no proposal/claim mechanism, risking permanent loss of admin control - ([File: crates/common/precompiles/src/activation/storage.rs])

### Summary
The `ActivationRegistryStorage::set_admin` function rotates the activation-registry admin in a single transaction: the current admin directly writes `new_admin` to storage after only a zero-address check, with no staging/claim step. This mirrors the exact bug class described in the referenced Sherlock report for `TimeLock::changeOwner` — if the current admin submits an incorrect (but non-zero) address, admin control of the registry is permanently and irrecoverably lost.

### Finding Description
`set_admin` performs the ownership change atomically: [1](#0-0) 

The only input validation is a zero-address check and a caller-is-current-admin check via `require_admin_caller`. There is no proposal/pending-admin slot and no acceptance step by the new address, unlike the codebase's own precompile for the same bug class — the Policy Registry — which implements exactly the two-step pattern recommended by the referenced report (`stage_update_admin` / `finalize_update_admin`, requiring the new admin to actively claim the role in a separate transaction): [2](#0-1) 

This inconsistency shows the codebase already recognizes and mitigates this exact bug class for policy admins, but the activation registry admin — a more privileged and more consequential role — was left with the single-step pattern.

The activation registry admin is not a low-stakes role: it is the sole gate controlling activation/deactivation of `PolicyRegistry`, `B20Stablecoin`, and `B20Asset` precompiles chain-wide: [3](#0-2) 

An admin typo (e.g., transposed hex digits, wrong checksum, copy-paste of a similar-looking but wrong address) in a `setAdmin` call permanently and irreversibly transfers control to an address nobody controls, since `set_admin` also requires the *current* admin to invoke it — once lost, there is no recovery path, and `admin()` will never again match any controllable key.

### Impact Explanation
Losing the activation-registry admin permanently disables the ability to activate or deactivate any Base-native precompile feature going forward. This is a concrete freezing-of-governance-function scenario: if a critical vulnerability is later found in the Policy Registry or a B-20 token variant that needs to be deactivated in an emergency, that deactivation becomes permanently impossible. This matches the Medium severity classification used in the original report for the analogous single-step `changeOwner` pattern.

### Likelihood Explanation
The trigger is a single accidental or fat-fingered transaction by the current admin — the same realistic scenario the original report calls out (typo/wrong address, not malicious action). Given `set_admin` is the only path to rotate this role, and no staged confirmation exists, likelihood of an unrecoverable mistake is non-trivial for any operationally-managed key.

### Recommendation
Adopt the same two-step admin rotation pattern already implemented for the Policy Registry (`stage_update_admin` / `finalize_update_admin`) for the activation registry: add a `pending_admin` storage slot, a `stageUpdateAdmin(newAdmin)` entrypoint restricted to the current admin, and a `finalizeUpdateAdmin()` entrypoint that only the staged `pending_admin` can call to complete the rotation, following the same `require_admin`/`write_pending_admin`/`delete_pending_admin` pattern used in `crates/common/precompiles/src/policy/logic/v1.rs`.

### Proof of Concept
1. Chain is configured with `ActivationAdminConfig::state_backed(fallback)`, and the current admin is `ADMIN`.
2. `ADMIN` calls `setAdmin(newAdmin)` on the activation registry intending to rotate to a new operational key, but mistypes the address as `WRONG_ADDR` (non-zero, so the zero-address guard in `set_admin` does not fire):
```rust
ActivationRegistryStorage::new(ctx).set_admin(WRONG_ADDR, STATE_ADMIN_CONFIG)?;
```
3. `self.admin.write(WRONG_ADDR)` succeeds; per `require_admin_caller`, only the key matching `WRONG_ADDR` (which nobody controls) can now call `activate`/`deactivate`/`set_admin` again.
4. All future `activate`, `deactivate`, and `set_admin` calls revert with `Unauthorized` for every real key, permanently freezing feature-activation governance — see the same failure mode already tested for a *correct* rotation in `set_admin_updates_storage_and_authorization` (crates/common/precompiles/src/activation/storage.rs:456-493), where the old admin is locked out immediately after the write with no recovery path.

### Citations

**File:** crates/common/precompiles/src/activation/storage.rs (L138-147)
```rust
    /// Activates the feature.
    pub fn activate(&mut self, feature: B256, admin_config: ActivationAdminConfig) -> Result<()> {
        self.set_activated(feature, true, admin_config)
    }

    /// Deactivates the feature.
    pub fn deactivate(&mut self, feature: B256, admin_config: ActivationAdminConfig) -> Result<()> {
        self.set_activated(feature, false, admin_config)
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
