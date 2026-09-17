### Title
Single-step `setAdmin` on the Activation Registry precompile risks permanent loss of feature-activation control - (File: `crates/common/precompiles/src/activation/storage.rs`)

### Summary
`ActivationRegistryStorage::set_admin` rotates the activation-registry admin in a single transaction, unlike the `PolicyRegistry` precompile in the same codebase, which implements a two-step `stageUpdateAdmin` / `finalizeUpdateAdmin` pending-admin pattern for exactly this class of risk.

### Finding Description
`set_admin` validates that the new admin is non-zero and that the caller is the current admin, then writes `new_admin` directly to storage in the same call: [1](#0-0) . There is no `pendingAdmin`/`claimAdmin` intermediate step: the admin address is authoritative for `activate`/`deactivate` calls immediately after this single transaction is included, as demonstrated in both the unit test and the system test that rotate `admin -> new_admin` in one call and observe the old admin immediately losing authorization: [2](#0-1) [3](#0-2) .

This contrasts with the `PolicyRegistry` precompile in the same repository, which the codebase's own authors hardened against exactly this class of bug (the `SwappableYieldSource` "one-step ownership transfer" bug class cited in the report) by requiring the new admin to explicitly accept via a second transaction: `stage_update_admin` writes a pending admin and only `finalize_update_admin`, called by the pending admin itself, commits the change: [4](#0-3) . The activation-registry admin path has no equivalent safeguard.

### Impact Explanation
The activation-registry admin is a privileged, feature-gating role: `activate`/`deactivate` calls that flip B20Asset/B20Stablecoin (and other) feature flags are authorized solely by this admin, as shown by the `require_admin_caller` gate reused across `set_admin`/`activate`/`deactivate`. If the current admin submits `setAdmin(newAdmin)` with an incorrect address (typo, wrong checksum, unreachable/unknown-key address, or a address for a not-yet-deployed multisig), all future admin-gated feature-activation operations become permanently unreachable — there is no way to recover, since the new admin can never submit a corrective transaction and there is no fallback path (unlike the Policy Registry's two-step design, which lets the current admin re-stage a corrected address before the transfer commits). This can permanently freeze the ability to activate/deactivate registered features on the token precompiles that gate on this registry, a permanent-freezing-of-functionality condition analogous to the referenced report.

### Likelihood Explanation
The change requires only a single signed transaction from the current, already-privileged activation admin — no special privilege escalation, front-running, or additional actor collusion is needed to trigger the mistake, and human/operational error entering the wrong address is a well-documented, plausible occurrence (as the original report's sponsor discussion also concedes, "human error" is not eliminated by using a multisig). Given this pattern was already identified and mitigated for `PolicyRegistry` in the same codebase, its absence on the Activation Registry indicates an inconsistent application of the mitigation rather than a deliberate accepted risk.

### Recommendation
Apply the same two-step admin-rotation pattern already implemented for `PolicyRegistry` (`stage_update_admin` / `finalize_update_admin`) to `ActivationRegistryStorage::set_admin`: have `setAdmin` write a `pendingAdmin` slot and require a subsequent `claimAdmin`/`finalizeUpdateAdmin`-style call, signed by the pending admin, to commit the change. This lets an operator correct a mis-set address before the transfer takes effect, consistent with the design already adopted elsewhere in this codebase.

### Proof of Concept
1. Current activation admin `A` calls `setAdmin(newAdmin = B)` where `B`'s private key is unknown/mistyped, matching the state-transition validated by `set_admin_updates_storage_and_authorization`: [5](#0-4) .
2. `admin` storage slot is immediately overwritten to `B`; `A` immediately loses authorization, as asserted in the same test (`old_admin_err` = `Unauthorized`): [6](#0-5) .
3. Since `B` cannot sign a transaction, no further `setAdmin`/`activate`/`deactivate` call can succeed — the exact single-step irrecoverable-transfer scenario the external report describes.

### Citations

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

**File:** crates/common/precompiles/src/activation/storage.rs (L455-491)
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
```

**File:** etc/systems/tests/activation_registry.rs (L90-141)
```rust
/// At Cobalt, `setAdmin` updates the stored admin and future activation authority.
#[tokio::test]
async fn test_activation_registry_cobalt_admin_rotation() -> Result<()> {
    let (_system, provider) = cobalt::start_cobalt_system().await?;
    let admin = PrivateKeySigner::from_bytes(&ANVIL_ACCOUNT_5.private_key)
        .wrap_err("Failed to parse system test admin private key")?;
    let new_admin = PrivateKeySigner::from_bytes(&ANVIL_ACCOUNT_6.private_key)
        .wrap_err("Failed to parse system test new admin private key")?;
    beryl::wait_for_balance(&provider, admin.address()).await?;
    beryl::wait_for_balance(&provider, new_admin.address()).await?;

    let admin_client = B20PrecompileClient::new(&provider, &admin, common::L2_CHAIN_ID)
        .with_receipt_timeout(beryl::TX_RECEIPT_TIMEOUT);
    let new_admin_client = B20PrecompileClient::new(&provider, &new_admin, common::L2_CHAIN_ID)
        .with_receipt_timeout(beryl::TX_RECEIPT_TIMEOUT);

    assert_eq!(admin_address(&admin_client).await?, admin.address());

    let set_admin_receipt = admin_client
        .send_call_receipt(
            ActivationRegistryStorage::ADDRESS,
            IActivationRegistry::setAdminCall { newAdmin: new_admin.address() },
            "set activation admin",
        )
        .await?;
    assert_admin_changed_log(
        &set_admin_receipt,
        admin.address(),
        new_admin.address(),
        admin.address(),
    );
    assert_eq!(admin_address(&admin_client).await?, new_admin.address());

    let feature = ActivationFeature::B20Stablecoin.id();
    let old_admin_activate = admin_client
        .try_send_call(
            ActivationRegistryStorage::ADDRESS,
            IActivationRegistry::activateCall { feature },
            "activate feature with old admin",
        )
        .await?;
    assert!(!old_admin_activate, "previous admin should not activate after rotation");

    let new_admin_activate = new_admin_client
        .send_call_receipt(
            ActivationRegistryStorage::ADDRESS,
            IActivationRegistry::activateCall { feature },
            "activate feature with new admin",
        )
        .await?;
    assert_activation_log(&new_admin_activate, feature, new_admin.address(), true);
    assert!(is_activated(&new_admin_client, feature).await?, "new admin activation should persist");
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
