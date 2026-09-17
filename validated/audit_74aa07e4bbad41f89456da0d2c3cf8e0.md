### Title
Single-key `ActivationRegistry` admin can instantly rotate itself in and unilaterally freeze/gate `B20Asset`, `B20Stablecoin`, and `PolicyRegistry` precompiles - (File: crates/common/precompiles/src/activation/storage.rs)

### Summary
The `ActivationRegistry` precompile gates the entire B-20 asset/stablecoin token system and the `PolicyRegistry` behind a single "activation admin" address. From Cobalt onward this admin is stored in mutable state and can be rotated by a single `setAdmin` transaction from the current admin, with the change taking effect immediately — no timelock, multi-sig, or delay of any kind. This mirrors the external report's bug class: a single privileged key that can reassign the address controlling all protected precompile operations, with immediate effect.

### Finding Description
`ActivationRegistryStorage::set_admin` lets the current admin overwrite the stored `admin` address in a single transaction, with the new admin taking effect for the very next call: [1](#0-0) 

`require_admin_caller`/`authorized_admin` resolve the effective admin purely from this mutable slot (falling back to a static genesis-configured address only if unset), and any account matching it can `activate`/`deactivate` the `B20Asset`, `B20Stablecoin`, and `PolicyRegistry` features: [2](#0-1) [3](#0-2) 

The genesis-time "static fallback" admin for production chains (Base Mainnet, Sepolia, Zeronet, Devnet) is a plain externally-owned account address hard-coded in chain config, not a timelock/governor/multisig contract: [4](#0-3) 

Once Cobalt activates state-backed admin storage, that same address (or whoever it rotates itself to) can call `setAdmin` and instantly become the sole authority over activation state — verified end-to-end by the system test showing the old admin loses all activation rights the very next block after rotation: [5](#0-4) 

This directly parallels the report's `AddressRegistry` pattern: a single owner-controlled address (here, the activation admin) governs which addresses hold ultimate authority over critical protocol functionality, and that address can be changed atomically with no time-delay for users to react.

### Impact Explanation
A compromised or malicious activation admin can immediately:
- `deactivate` the `B20Asset` or `B20Stablecoin` feature, which (per `ensure_activated`/`checkActivated` gating referenced in `activation/storage.rs`) would make every live B-20 token on the chain revert all read/write calls, freezing all balances and transfers for as long as the feature stays deactivated — a chain-wide freezing-of-funds condition reachable by one key with no recovery path other than the same admin re-activating it.
- `deactivate` `PolicyRegistry`, breaking allow/blocklist enforcement relied on by every B-20 token that references policies, potentially bypassing compliance-critical policy checks or freezing transfers that depend on policy evaluation.
- Rotate the admin (`setAdmin`) to an address it fully controls, permanently locking out the legitimate operator, since there is no separate recovery mechanism, no delay, and no multi-party check on this transition.

This is comparable in severity to the reported issue: unlike the two-step, delayed `stageUpdateAdmin`/`finalizeUpdateAdmin` flow used elsewhere in the same codebase for `PolicyRegistry` policy admins (which requires a follow-up transaction from the new admin before the change is final), the activation-admin rotation is single-step and immediate.

### Likelihood Explanation
Low-to-medium: requires the activation admin key (a single EOA per chain, hard-coded for Base Mainnet/Sepolia/Zeronet, or configurable in genesis for devnets) to be malicious or compromised — the same "malicious or compromised owner" precondition described in the external report. No unprivileged attacker can reach this path directly.

### Recommendation
Route activation-admin rotations and feature deactivation through a two-step or time-delayed mechanism analogous to the `PolicyRegistry`'s existing `stageUpdateAdmin`/`finalizeUpdateAdmin` pattern (already implemented at [6](#0-5) ), or require the activation admin to be a timelock/multisig contract rather than a bare EOA, so that a compromised or malicious admin cannot instantaneously freeze token functionality or permanently lock out the legitimate operator.

### Proof of Concept
1. On a Cobalt-active chain, the current activation admin (`ADMIN`) calls `IActivationRegistry::setAdminCall { newAdmin: ATTACKER }` — this succeeds in the same block it is submitted, per `set_admin_updates_storage_and_authorization`: [7](#0-6) 
2. `ATTACKER` immediately calls `deactivateCall { feature: ActivationFeature::B20Asset.id() }` (or `B20Stablecoin`/`PolicyRegistry`).
3. Every existing B-20 asset/stablecoin token's `balanceOf`, `transfer`, `mint`, etc. calls now fail their `ensure_activated` check, freezing all token functionality chain-wide, with no user recourse and no delay to react, matching the harness-verified rotation-and-lockout behavior in [8](#0-7) .

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

**File:** crates/common/precompiles/src/activation/storage.rs (L223-244)
```rust
    /// Returns the effective admin if one is configured and the caller address can be authorized.
    pub fn authorized_admin(&self, admin_config: ActivationAdminConfig) -> Result<Address> {
        let caller = self.storage.caller();
        if caller.is_zero() {
            return Err(BasePrecompileError::revert(IActivationRegistry::Unauthorized { caller }));
        }
        let admin = self.admin(admin_config)?;
        if admin.is_zero() {
            return Err(BasePrecompileError::revert(IActivationRegistry::Unauthorized { caller }));
        }
        Ok(admin)
    }

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

**File:** actions/harness/tests/beryl/activation.rs (L115-171)
```rust
#[tokio::test]
async fn cobalt_enables_state_backed_activation_admin_rotation() {
    let mut env = BerylTestEnv::new_with_cobalt();
    let (probe, deploy_probe) = env.deploy_staticcall_probe_tx(ActivationRegistryStorage::ADDRESS);

    let pre_beryl = env.sequencer.build_next_block_with_transactions(vec![deploy_probe]).await;
    assert!(env.user_tx_succeeded(&pre_beryl, 0), "activation-registry probe must deploy");

    let beryl_boundary = env.sequencer.build_empty_block().await;

    let pre_cobalt_set_admin = env.set_activation_admin_tx(BerylTestEnv::bob());
    let pre_cobalt =
        env.sequencer.build_next_block_with_transactions(vec![pre_cobalt_set_admin]).await;
    assert!(
        !env.user_tx_succeeded(&pre_cobalt, 0),
        "setAdmin(newAdmin) must revert after Beryl but before Cobalt"
    );

    let cobalt_boundary = env.sequencer.build_empty_block().await;

    let set_admin = env.set_activation_admin_tx(BerylTestEnv::bob());
    let set_admin_block = env.sequencer.build_next_block_with_transactions(vec![set_admin]).await;
    assert!(
        env.user_tx_succeeded(&set_admin_block, 0),
        "setAdmin(newAdmin) must succeed at Cobalt"
    );
    assert_admin_changed_log(&env, &set_admin_block);

    let admin_call = Bytes::from(IActivationRegistry::adminCall {}.abi_encode());
    let admin_probe =
        env.call_staticcall_probe_tx(probe, admin_call, BerylTestEnv::B20_PROBE_GAS_LIMIT);
    let admin_probe_block =
        env.sequencer.build_next_block_with_transactions(vec![admin_probe]).await;
    assert!(env.probe_call_succeeded(probe), "admin() staticcall must succeed after rotation");
    assert_eq!(
        env.probe_return_word(probe),
        word_from_address(BerylTestEnv::bob()),
        "admin() must return the state-backed activation admin"
    );

    let old_admin_activate = env.activate_feature_tx(FEATURE);
    let old_admin_block =
        env.sequencer.build_next_block_with_transactions(vec![old_admin_activate]).await;
    assert!(
        !env.user_tx_succeeded(&old_admin_block, 0),
        "previous admin must not activate features after rotation"
    );

    let new_admin_activate = env.create_bob_tx(
        TxKind::Call(ActivationRegistryStorage::ADDRESS),
        Bytes::from(IActivationRegistry::activateCall { feature: FEATURE }.abi_encode()),
        GAS_LIMIT,
    );
    let new_admin_block =
        env.sequencer.build_next_block_with_transactions(vec![new_admin_activate]).await;
    assert!(env.user_tx_succeeded(&new_admin_block, 0), "new admin must activate features");
    assert_activation_log_from(&env, &new_admin_block, true, BerylTestEnv::bob());
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
