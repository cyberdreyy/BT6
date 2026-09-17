### Title
Single-step `ActivationRegistry.setAdmin` ownership transfer allows irrecoverable admin loss - (File: `crates/common/precompiles/src/activation/storage.rs`)

### Summary
Base's native `ActivationRegistry` precompile (`0x8453…0001`) stores a mutable `admin` address that gates `activate`/`deactivate`/`setAdmin` calls for critical protocol features (`B20Asset`, `B20Stablecoin`, `PolicyRegistry`). `set_admin` performs an immediate, single-step ownership transfer with no pending/accept mechanism, unlike the codebase's own `PolicyRegistry`, which implements a proper two-step `stage_update_admin` / `finalize_update_admin` flow for exactly the same class of admin-transfer risk.

### Finding Description
`ActivationRegistryStorage::set_admin` writes `new_admin` directly to storage after only checking for a non-static call, state-backed admin enablement, and a non-zero address: [1](#0-0) 

There is no staging/pending-admin/acceptance step comparable to the one implemented for policies in the same crate: [2](#0-1) 

The call is dispatched directly from calldata reachable by any signed transaction to the precompile address, gated only by the current admin check inside `set_admin` (`require_admin_caller`): [3](#0-2) 

Because `new_admin` is written unconditionally once the zero-address and admin-caller checks pass, a single malformed `setAdminCall` transaction (typo'd address, wrong checksum, copy-paste error, or a value that cannot produce a valid signing key) permanently transfers control away from the legitimate admin with no recovery path.

### Impact Explanation
The activation registry admin is the sole authority that can `activate`/`deactivate` the `B20Asset`, `B20Stablecoin`, and `PolicyRegistry` native features (per `crates/common/precompiles/README.md` and `ActivationFeature`). Losing control of this admin — even accidentally — permanently freezes the ability to activate, deactivate, or otherwise manage rollout of these native Base precompile feature flags, since there is no owner-recovery mechanism and no unauthorized third party could ever regain it (unless the mistyped address happens to be attacker-controlled, in which case the attacker gains full control over feature activation for these B-20 subsystems). This is a Medium-to-High severity governance/DoS risk consistent with the reported bug class: irrevocable loss of a privileged role due to a one-step transfer.

### Likelihood Explanation
The only precondition is that the current activation admin submits one `setAdminCall` transaction with an incorrect `newAdmin` value — a normal, low-effort, single-transaction operational mistake (fat-fingered address, wrong environment variable, copy from wrong doc) with no possibility of on-chain reversal, exactly the scenario described in the source report. The codebase's own `PolicyRegistry` demonstrates the team is aware of and mitigates this exact class of bug elsewhere, but the mitigation was not applied to `ActivationRegistry`, indicating an oversight rather than an unlikely edge case.

### Recommendation
- Short term: Convert `ActivationRegistryStorage::set_admin` to a two-step process analogous to `PolicyRegistry`'s `stage_update_admin`/`finalize_update_admin`: add a `pending_admin` storage slot, a `stageAdmin(address)` call restricted to the current admin, and a `finalizeAdmin()` call restricted to the pending admin, only then updating `admin` and emitting `AdminChanged`.
- Long term: Audit all other Base-native precompiles/state-backed privileged-role setters (see `crates/common/precompiles/src/**`) for single-step ownership/admin transfers and standardize on the same staged-transfer pattern already implemented for `PolicyRegistry`.

### Proof of Concept
1. Enable state-backed activation admin (`ActivationAdminConfig::state_backed`) and set the current admin to `A`.
2. `A` submits a transaction calling the activation registry (`0x8453000000000000000000000000000000000001`) with `IActivationRegistry::setAdminCall { newAdmin: <mistyped/unowned address X> }`.
3. `ActivationRegistryStorage::set_admin` (crates/common/precompiles/src/activation/storage.rs:149-177) passes the zero-address check (X ≠ 0) and the `require_admin_caller` check (caller is still A), then writes `admin = X` immediately.
4. No transaction from `X` is required to accept the role; `A` can never again call `activate`/`deactivate`/`setAdmin` (confirmed by the existing test `set_admin_updates_storage_and_authorization` in `crates/common/precompiles/src/activation/storage.rs:455-494`, which shows the old admin is rejected with `Unauthorized` immediately after the transfer).
5. If `X` is unowned or mistyped, control of `B20Asset`/`B20Stablecoin`/`PolicyRegistry` activation is permanently lost with no recovery transaction possible.

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

**File:** crates/common/precompiles/src/policy/logic/v2.rs (L414-459)
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

**File:** crates/common/precompiles/src/activation/dispatch.rs (L92-95)
```rust
            C::setAdmin(call) => {
                self.set_admin(call.newAdmin, admin_config)?;
                Ok(Bytes::new())
            }
```
