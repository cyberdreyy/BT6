### Title
Sequence-dependent, permissionless policy-ID assignment lets a reorg-racing attacker hijack a `PolicyRegistry` policy that a B20 token already trusts — (File: `crates/common/precompiles/src/policy/logic/v1.rs`)

### Summary
`PolicyRegistry.createPolicy` is permissionless and assigns each new policy a deterministic, sequentially-incrementing ID (`type<<56 | counter`) rather than a content/commitment hash. B20 tokens store this bare numeric ID (via `updatePolicy`) to gate transfers, mint, seize, etc. If the block(s) containing a `createPolicy` call are reverted and replayed in a different order (an unsafe-chain reorg), an unprivileged attacker can race to call `createPolicy` first and claim the exact same numeric ID that a token administrator already trusted and referenced, becoming the admin of that policy and silently taking over an access-control gate already wired into a live token.

### Finding Description
`PolicyRegistryV1::create_policy_inner` derives a policy's identity purely from an incrementing counter plus its type byte: [1](#0-0) [2](#0-1) 

`createPolicy` is permissionless — any account can call it — and the resulting `policyId` is entirely a function of call order (global counter state), not of the caller, the policy content, or any commitment established beforehand: [3](#0-2) 

That bare numeric `policyId` is exactly what token admins later persist into B20 token storage via `updatePolicy`, which only checks that *some* policy with that ID exists — not that it is the specific policy the admin intended: [4](#0-3) 

The ID is then trusted for every gated operation (transfer sender/receiver/executor, mint receiver, seize exempt/receiver) via `TokenAccounting::policy_id` / `transfer_policy_ids` and `ensure_authorized_by_id`: [5](#0-4) [6](#0-5) 

This is the same bug class as the RecipeKernel finding: a permissionlessly-creatable, sequentially-indexed resource is referenced elsewhere by its bare index rather than by a content hash/commitment. In RecipeKernel, `targetMarketID` (an incrementing `numMarkets` counter) is baked into IP/AP offers; after a re-org, an attacker can recreate a market at the same `targetMarketID` with malicious recipe logic and hijack any offer that references it. Here, an incrementing `policyId` counter is baked into a token's `TRANSFER_SENDER_POLICY`/`SEIZE_EXEMPT_POLICY`/etc. slots via `updatePolicy`; after a chain reorg of the unsafe/pending L2 chain that discards the original `createPolicy` transaction, an attacker can front-run the resubmission with their own `createPolicy` call using the same `policyType`, deterministically obtaining the identical `policyId` (since the ID depends only on type and counter position, and the counter is a single global sequential value). The attacker becomes `admin` of that policy ID and can then freely add/remove accounts from the allowlist/blocklist that a live B20 token already wires to gate transfers, mint receivers, or seize exemptions.

### Impact Explanation
Whoever controls the `admin` of the hijacked policy ID controls a live authorization gate for one or more already-deployed B20 tokens:
- If wired to `TRANSFER_SENDER_POLICY`/`TRANSFER_RECEIVER_POLICY`/`TRANSFER_EXECUTOR_POLICY`, the attacker can add malicious accounts to an allowlist (or remove intended blocklist members) to allow otherwise-restricted transfers — theft or unauthorized movement of tokenized funds.
- If wired to `SEIZE_EXEMPT_POLICY`/`SEIZE_RECEIVER_POLICY`, the attacker can grant themselves exemption from seizure or redirect seizure proceeds, undermining compliance/seize controls the issuer relies on.
- Because policy semantics (allow vs. block) are baked into the ID's type byte, and the attacker only needs to match `policyType` and the counter slot, this is a straightforward re-derivation, not a brute-force guess.

This satisfies "unauthorized operation / theft of funds" for B20 asset and stablecoin tokens gated by the `PolicyRegistry`.

### Likelihood Explanation
Exploitation requires: (1) an unsafe-chain reorg on the L2 (a normal, non-malicious-sequencer event that can occur before blocks are marked safe/derived from L1, e.g., during sequencer failover or derivation catch-up) that discards the block containing the honest `createPolicy` call and any subsequent `updatePolicy` wiring, and (2) an attacker monitoring the mempool/chain who resubmits a colliding `createPolicy` call before the original is replayed. Both conditions are plausible without any privileged role or malicious sequencer/builder behavior — the attacker is an ordinary unprivileged transaction sender racing a naturally reorg-able chain. The likelihood is lower than a guaranteed-reachable bug because it depends on the timing of a reorg, but the incentive (silently seizing control of a token's compliance gate) makes intentional exploitation attractive whenever a reorg window opens, mirroring the "incentivizes intentional re-orgs" dynamic called out in the original report.

### Recommendation
Do not let cross-contract references to a `PolicyRegistry` policy rely on the bare sequential `policyId` alone. Options:
- Have `updatePolicy` (and any other consumer) additionally record/verify a commitment (e.g., a hash of `(admin, policyType, creation tx/block context)`) established at the time the caller intended to reference the policy, and re-validate that commitment before trusting the ID for gating decisions.
- Alternatively, make policy creation atomic with the intended wiring (e.g., a single transaction that creates the policy and immediately calls `updatePolicy` with the freshly returned ID in the same call), so there is no window in which a stale, reorg-vulnerable ID reference can be replayed against a different underlying policy.
- At minimum, emit and let `updatePolicy` callers assert the expected `admin`/`policyType` of the referenced `policyId` at call time so a hijacked ID with a mismatched admin is rejected rather than silently trusted.

### Proof of Concept
1. Admin calls `PolicyRegistry.createPolicy(admin, ALLOWLIST)`. Assume this lands as counter value `N`, producing `policyId = (ALLOWLIST_TYPE << 56) | N` (per `PolicyRegistryV1::make_id`, `crates/common/precompiles/src/policy/logic/v1.rs:51-53`).
2. In the same or a following block, admin submits `token.updatePolicy(TRANSFER_SENDER_POLICY, policyId)` to wire this specific policy to gate transfers (`crates/common/precompiles/src/b20_asset/logic/v2.rs:669-696`).
3. Before this state is finalized/safe, the L2 unsafe chain reorgs and discards both transactions (e.g., during sequencer failover or a derivation-driven reset — see the reorg-handling machinery in `actions/harness/src/l1/miner.rs` and the node's `act_reset`/pipeline-reset tests demonstrating that pre-finalization state is routinely discarded and replayed).
4. An attacker, observing the discarded transactions, immediately submits their own `createPolicy(attacker, ALLOWLIST)` call so that it lands at the same counter slot `N`, deterministically producing the identical `policyId` via `make_id(ALLOWLIST_TYPE, N)`.
5. The admin's `createPolicy` call, being now redundant/reverted, and their `updatePolicy(TRANSFER_SENDER_POLICY, policyId)` call (if resubmitted or already mined referencing the numeric ID) now wires the token's transfer gate to the attacker-controlled policy.
6. The attacker, as `admin` of `policyId`, calls `updateAllowlist(policyId, true, [attacker_or_accomplice])`, granting themselves (or colluding accounts) authorization to transfer under a token's `TRANSFER_SENDER_POLICY`/`TRANSFER_RECEIVER_POLICY`, bypassing the compliance gate the token issuer intended.

Note: I was not able to find a change in this codebase analogous to the Royco fix (replacing index-based references with hash-based ones) for the `PolicyRegistry`/B20 `updatePolicy` pairing; if such a mitigation exists elsewhere (e.g., a policy-creation/wiring atomicity guarantee not indexed by my searches), it was not found in the reachable code paths reviewed.

### Citations

**File:** crates/common/precompiles/src/policy/logic/v1.rs (L50-53)
```rust
    /// Encodes a policy ID from its type discriminant and counter.
    pub const fn make_id(policy_type: u8, counter: u64) -> u64 {
        (policy_type as u64) << Self::POLICY_ID_TYPE_SHIFT | (counter & Self::COUNTER_MASK)
    }
```

**File:** crates/common/precompiles/src/policy/logic/v1.rs (L146-161)
```rust
    /// Shared creation core after inputs have been validated.
    fn create_policy_inner<S: PolicyAccounting>(
        &self,
        storage: &mut S,
        admin: Address,
        policy_type: PolicyType,
        policy_type_u8: u8,
    ) -> Result<u64> {
        let counter = self.ensure_initialized_and_get_counter(storage)?;
        let is_counter_overflowed = counter >= Self::COUNTER_MASK;
        if is_counter_overflowed {
            return Err(BasePrecompileError::under_overflow());
        }
        storage.write_next_counter(counter + 1)?;
        let policy_id = Self::make_id(policy_type_u8, counter);
        storage.write_policy_word(policy_id, PackedPolicy::new(admin).into_u256())?;
```

**File:** crates/common/precompiles/src/policy/abi/v2.rs (L51-51)
```rust
        function createPolicy(address admin, PolicyType policyType) external returns (uint64);
```

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L669-696)
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

**File:** crates/common/precompiles/src/common/core_storage.rs (L51-68)
```rust
    /// Transfer sender policy ID.
    #[accessor]
    #[mutator]
    pub transfer_sender_policy_id: u64, // slot 9, offset 0
    /// Transfer receiver policy ID.
    #[accessor]
    #[mutator]
    pub transfer_receiver_policy_id: u64, // slot 9, offset 8
    /// Transfer executor policy ID.
    #[accessor]
    #[mutator]
    pub transfer_executor_policy_id: u64, // slot 9, offset 16
    /// Reserved padding to close slot 9.
    pub transfer_reserved_0: u64, // slot 9, offset 24 (filler to close the slot)
    /// Mint receiver policy ID.
    #[accessor]
    #[mutator]
    pub mint_receiver_policy_id: u64, // slot 10, offset 0
```

**File:** crates/common/precompiles/src/common/ops/guards.rs (L80-94)
```rust
    pub fn ensure_authorized_by_id<T: Token + ?Sized>(
        token: &T,
        policy_scope: B256,
        policy_id: u64,
        account: Address,
    ) -> Result<()> {
        if token.policy().is_authorized(token.policy_storage(), policy_id, account)? {
            Ok(())
        } else {
            Err(BasePrecompileError::revert(IB20::PolicyForbids {
                policyScope: policy_scope,
                policyId: policy_id,
            }))
        }
    }
```
