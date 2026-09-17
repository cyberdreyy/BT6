### Title
Renouncing a `PolicyRegistry` policy's admin while it gates a live B-20 token's transfers permanently freezes all holders' funds - (File: `crates/common/precompiles/src/policy/logic/v1.rs`)

### Summary
The `PolicyRegistry` precompile lets a policy's admin permanently give up control via `renounce_admin`, which zeroes the admin field with no check for whether accounts are still relying on the policy's membership list. A B-20 token can wire its `TRANSFER_SENDER_POLICY`/`TRANSFER_RECEIVER_POLICY` to such a policy via `update_policy`. If that policy's admin renounces while the policy is an `ALLOWLIST` gating transfers, no one can ever add new members again, permanently blocking transfers for any account not already a member — mirroring the reported bug class where releasing a registry's control link over an entity still holding live user state permanently traps those funds.

### Finding Description
`PolicyRegistryV1::renounce_admin` simply requires the caller to be the current admin, then sets the packed admin field to `Address::ZERO` with no check that the policy is still referenced by any active token or that doing so is safe: [1](#0-0) 

Any subsequent attempt to mutate membership goes through `update_membership`, which requires `packed.admin() == caller`; once admin is `Address::ZERO`, no caller (including `Address::ZERO` itself, which cannot originate a transaction) can ever satisfy this check again: [2](#0-1) 

The protocol's own test suite documents this as an irreversible freeze: [3](#0-2) 

A B-20 token's `DefaultAdmin` can wire `TransferSender`/`TransferReceiver` scopes to any policy that merely satisfies `policy_exists`, with no requirement that the policy remain administrable or have any members: [4](#0-3) 

Combining these primitives: a token creator/`DefaultAdmin` can (1) `createPolicy` a fresh `ALLOWLIST`, (2) `updatePolicy` the token's `TransferSender` (or `TransferReceiver`) scope to point at it while it has zero members, and (3) call `renounceAdmin` on that policy. From that point, `is_authorized` for that `ALLOWLIST` policy id always returns `false` for every account (no member was ever added, and none can ever be added), so every transfer routed through that scope reverts forever — for every holder of the token, not just the party who performed the renounce. Unlike the token's own `DefaultAdmin` role, which is explicitly protected from this exact failure mode by `LastAdminCannotRenounce` in `revoke_role`/`renounce_role` (`crates/common/precompiles/src/b20_stablecoin/logic/v1.rs` lines 475-514), the `PolicyRegistry`'s `renounce_admin` has no equivalent safeguard, despite gating the same kind of user-fund-affecting state.

### Impact Explanation
Once the sole admin of a policy renounces while that policy still gates live transfer paths on a deployed B-20 token, membership can never change again. If it is an `ALLOWLIST` with no members (or one that excludes some/most holders), every affected holder is permanently unable to move their balance — a durable freeze of user funds with no on-chain recovery path, since the token's `updatePolicy` merely needs to point at *some* existing policy id, but the frozen policy remains a valid, permanently-empty gate that a malicious or careless deployer can leave wired in place. This matches the reported bug class: releasing administrative control over a registry entry that active user funds still depend on locks those funds without recourse.

### Likelihood Explanation
This requires only ordinary, unprivileged transactions available to any B-20 token creator/`DefaultAdmin`: `createPolicy`, `updatePolicy`, and `renounceAdmin`, all of which are normal-path precompile calls with no special system access. A malicious token creator can trivially engineer this as a "soft rug" (renouncing admin to appear decentralized while trapping holder funds), and even a benign admin can trigger it accidentally by renouncing a policy without realizing it is still wired into a token's transfer gate.

### Recommendation
Add a guard in `renounce_admin` (and the two-step `finalize_update_admin`/`stage_update_admin` paths) analogous to the `LastAdminCannotRenounce` protection used for B20 role admin: either track whether a policy is currently referenced by any token's `policyScope` and block renouncing/leaving it adminless while referenced, or require `updatePolicy` to refuse pointing a scope at a policy whose admin is already `Address::ZERO` and additionally block wiring to policies with no members, and/or provide a governance/recovery path (e.g., a protocol-level override) so that transfers gated by an orphaned policy can be re-pointed even without cooperation from a malicious admin.

### Proof of Concept
1. Attacker (token creator) calls `IPolicyRegistry.createPolicy(admin: attacker, policyType: ALLOWLIST)`, obtaining `policyId`.
2. Attacker deploys a B-20 token (or on an existing token where they hold `DefaultAdmin`) and calls `IB20.updatePolicy(policyScope: TransferSender, newPolicyId: policyId)` — succeeds because `policy_exists(policyId)` is true (per `update_policy` in `b20_asset/logic/v1.rs` lines 578-605), even though `policyId` currently has zero allowlisted members.
3. Users acquire balances of the token (e.g., via `mint`/initial distribution) before or immediately after step 2.
4. Attacker calls `IPolicyRegistry.renounceAdmin(policyId)` — succeeds per `renounce_admin` (`policy/logic/v1.rs` lines 311-324), setting the policy's admin to `Address::ZERO`.
5. Any holder attempts `IB20.transfer(...)`; the `TransferSender` policy check calls `is_authorized(policyId, sender)`, which returns `false` for every address since the allowlist has and will forever have zero members (`update_membership` in `policy/logic/v1.rs` lines 186-212 can never succeed again because no caller can equal `Address::ZERO`). The transfer reverts, permanently, for every current and future holder.

### Citations

**File:** crates/common/precompiles/src/policy/logic/v1.rs (L186-212)
```rust
    fn update_membership<S: PolicyAccounting>(
        &self,
        storage: &mut S,
        policy_id: u64,
        expected_type: u8,
        add: bool,
        accounts: &[Address],
    ) -> Result<Address> {
        // Check order matches Solidity canonical: existence → type → admin → batch size.
        let packed = self.require_custom(storage, policy_id)?;
        if Self::policy_id_type(policy_id) != expected_type {
            return Err(BasePrecompileError::revert(IPolicyRegistry::IncompatiblePolicyType {}));
        }
        let caller = storage.caller();
        if packed.admin() != caller {
            return Err(BasePrecompileError::revert(IPolicyRegistry::Unauthorized {}));
        }
        Self::require_account_batch_size(accounts)?;
        for account in accounts {
            if add {
                storage.set_member(policy_id, *account)?;
            } else {
                storage.delete_member(policy_id, *account)?;
            }
        }
        Ok(caller)
    }
```

**File:** crates/common/precompiles/src/policy/logic/v1.rs (L311-324)
```rust
    fn renounce_admin(&self, storage: &mut S, policy_id: u64) -> Result<()> {
        let (packed, caller) = self.require_admin(storage, policy_id)?;
        storage.write_policy_word(policy_id, packed.with_admin(Address::ZERO).into_u256())?;
        storage.delete_pending_admin(policy_id)?;
        storage.emit_event(
            IPolicyRegistry::PolicyAdminUpdated {
                policyId: policy_id,
                previousAdmin: caller,
                newAdmin: Address::ZERO,
            }
            .encode_log_data(),
        )?;
        Ok(())
    }
```

**File:** crates/common/precompiles/src/policy/logic/v1.rs (L982-989)
```rust
    #[test]
    fn renounce_admin_freezes_policy() {
        let mut rt = initialized();
        let id = create_allowlist(&mut rt);
        LOGIC.renounce_admin(&mut rt, id).unwrap();
        let err = LOGIC.update_allowlist(&mut rt, id, true, vec![ALICE]).unwrap_err();
        assert!(matches!(err, BasePrecompileError::Revert(_)));
    }
```

**File:** crates/common/precompiles/src/b20_asset/logic/v1.rs (L578-605)
```rust
    fn update_policy(
        &self,
        token: &mut B20AssetToken<S, A>,
        caller: Address,
        policy_scope: B256,
        new_policy_id: u64,
        privileged: bool,
    ) -> Result<()> {
        if !privileged {
            B20Guards::ensure_token_role(token, caller, B20TokenRole::DefaultAdmin)?;
        }
        Self::ensure_supported_policy_type(policy_scope)?;
        if !token.policy().policy_exists(token.policy_storage(), new_policy_id)? {
            return Err(BasePrecompileError::revert(IB20::PolicyNotFound {
                policyId: new_policy_id,
            }));
        }
        let old_policy_id = token.accounting().policy_id(policy_scope)?;
        token.accounting_mut().set_policy_id(policy_scope, new_policy_id)?;
        token.accounting_mut().emit_event(
            IB20::PolicyUpdated {
                policyScope: policy_scope,
                oldPolicyId: old_policy_id,
                newPolicyId: new_policy_id,
            }
            .encode_log_data(),
        )
    }
```
