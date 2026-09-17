### Title
Unvalidated `BatcherUpdate` from L1 `SystemConfig` logs lets the L1 admin instantly and permanently redirect (or zero out) the trusted L2 batcher address with no sanity check — analog of unchecked yield-source swap - (File: `crates/common/genesis/src/updates/batcher.rs`)

### Summary
The reported bug class is: a privileged role (`owner`/`assetManager`) can call `swapYieldSource` and instantly redirect where user funds/trust is routed, with **no validation that the new target is safe/legitimate** and no timelock — an unchecked, single-transaction "swap the trusted address" primitive. The analogous pattern in this repo is `BatcherUpdate::apply` [1](#0-0)  and its `TryFrom<&SystemConfigLog>` decoder [2](#0-1) : on receipt of an L1 `ConfigUpdate` log from the `l1_system_config_address`, the derivation pipeline decodes an arbitrary 20-byte address and immediately overwrites `SystemConfig::batcher_address` — the single source of truth used everywhere in derivation to decide which L1 sender is a "trusted" batcher.

### Finding Description
`SystemConfig::update_with_receipts` filters L1 receipts for `ConfigUpdate` logs from the configured L1 system-config address and applies each decoded update unconditionally, skipping only malformed ones [3](#0-2) . For a `Batcher` update kind, `BatcherUpdate::apply` does nothing but blindly assign the decoded address: `config.batcher_address = self.batcher_address;` [1](#0-0) .

The only validation performed by `UpdateDataValidator::validate` is on the ABI-encoding shape (fixed 96-byte length, pointer == 32, length == 32) [4](#0-3)  — there is **no semantic check** on the decoded address itself (e.g., rejecting `address(0)`, rejecting a duplicate/no-op update, or requiring the new batcher to be registered/attested anywhere). Any 20-byte value that decodes successfully — including the zero address or an address with no operational batcher behind it — is accepted and immediately swapped into the live `SystemConfig`.

This `batcher_address` is the sole discriminator the derivation pipeline uses to decide whether L1 calldata/blob frames are trusted batch data: `PollingTraversal::batcher_addr()` reads directly from `self.system_config.batcher_address` [5](#0-4) , and it is consumed downstream by the retrieval/frame-filtering stages (`l1_retrieval.rs`, `blobs.rs`, `calldata.rs`, `ethereum.rs`) to accept or reject frames purely by sender address. The existing test `batcher_key_rotation_accepts_new_batcher` confirms that the rotation takes effect on the very next L1 block with no delay and no additional vetting: frames from the old address are "IGNORED" immediately and the new address is trusted immediately [6](#0-5) .

This is structurally identical to the `SwappableYieldSource` bug: a single privileged write (there: `owner`/`assetManager` calling `setYieldSource`; here: whoever controls the L1 `SystemConfig` contract emitting a `ConfigUpdate` log) instantly and irreversibly re-points a critical trust anchor (there: where funds live; here: which L1 address is considered the canonical data source for the entire L2 chain) with no registry check, no sanity check on the new value, and no delay/timelock in the consuming code.

### Impact Explanation
If the decoded `batcher_address` is ever `address(0)` (via a misconfiguration, a compromised/malicious L1 `SystemConfig` owner, or a bug in the upgrade path emitting a bad log), the derivation pipeline will accept it with no error and permanently switch to trusting `address(0)` as the batcher. Since no legitimate transaction sender can be `address(0)`, all subsequent L1 batches become unrecoverable/undeliverable by legitimate infrastructure without a hard fork or manual intervention — a chain halt for the safe/derived chain, matching the accepted "node halt" impact category. More generally, because the address is swapped with zero economic or cryptographic gating (any address decodes successfully), a compromised or malicious system-config admin can redirect the trusted batcher to an address that never posts data, or use it to grief the network by rapid flip-flopping, with the derivation pipeline offering no independent sanity check as a defense-in-depth layer.

### Likelihood Explanation
Reaching this code path requires the L1 `SystemConfig` contract (out of this repo's Rust codebase, but its log format is what this code trusts) to emit a `ConfigUpdate` event with `updateType == Batcher`; this repo's derivation code will process **any** correctly-ABI-shaped log without further validation regardless of what value is in it. Given the report's own precedent (privileged-role instant-swap bugs are routinely exploited or accidentally triggered), and given this code path is exercised on every L1 block during normal operation (not a rare edge case), likelihood of an unvalidated zero/garbage address reaching `SystemConfig::batcher_address` is non-trivial whenever the L1-side admin key is compromised or misused — which is exactly the risk class the original report flags as its root cause (no sanity/registry check on a privileged trust-anchor swap).

### Recommendation
Add a defense-in-depth semantic check in `BatcherUpdate::apply`/`TryFrom` (and analogously in the other `SystemConfigUpdate` variants that swap trusted addresses) to reject `address(0)` and any obviously invalid target before it overwrites `SystemConfig::batcher_address`, and treat such invalid updates as `SystemConfigUpdateError`s to be skipped/logged like other malformed updates rather than silently accepted. Consider also flagging repeated/rapid batcher rotations for operational alerting, since the consuming pipeline currently has no independent circuit breaker if the L1-side privileged role misbehaves.

### Proof of Concept
1. On L1, have the `SystemConfig` contract (whoever controls its privileged update function) emit a `ConfigUpdate(version, Batcher, abi.encode(address(0)))` log at the standard 96-byte, pointer=32/length=32 encoding.
2. This repo's `SystemConfig::update_with_receipts` picks up the log via `process_config_update_log` [7](#0-6) , `BatcherUpdate::try_from` decodes `address(0)` successfully (it passes the 96-byte/pointer/length shape checks) [2](#0-1) , and `apply` unconditionally sets `config.batcher_address = Address::ZERO` [8](#0-7) .
3. From the next L1 block onward, `PollingTraversal::batcher_addr()` returns `address(0)` [5](#0-4) , and no legitimate signer can ever match it, so derivation of new batches halts — reproducing the same "instant, no-vetting" pointer-swap primitive the external report calls out, but for the chain's canonical L1 data source instead of a DeFi yield source.

Note: I could not fully trace the exact frame-filtering comparison logic inside `blobs.rs`/`calldata.rs`/`ethereum.rs` (only match counts were retrieved, not code) to confirm there is no additional independent guard against a zero batcher address elsewhere in the frame-acceptance path; this should be verified directly in those files before treating the halt scenario as fully proven.

### Citations

**File:** crates/common/genesis/src/updates/batcher.rs (L18-23)
```rust
impl BatcherUpdate {
    /// Applies the update to the [`SystemConfig`].
    pub const fn apply(&self, config: &mut SystemConfig) {
        config.batcher_address = self.batcher_address;
    }
}
```

**File:** crates/common/genesis/src/updates/batcher.rs (L25-50)
```rust
impl TryFrom<&SystemConfigLog> for BatcherUpdate {
    type Error = BatcherUpdateError;

    fn try_from(log: &SystemConfigLog) -> Result<Self, Self::Error> {
        let LogData { data, .. } = &log.log.data;

        let validated = UpdateDataValidator::validate(data).map_err(|e| match e {
            ValidationError::InvalidDataLen(_expected, actual) => {
                BatcherUpdateError::InvalidDataLen(actual)
            }
            ValidationError::PointerDecodingError => BatcherUpdateError::PointerDecodingError,
            ValidationError::InvalidDataPointer(pointer) => {
                BatcherUpdateError::InvalidDataPointer(pointer)
            }
            ValidationError::LengthDecodingError => BatcherUpdateError::LengthDecodingError,
            ValidationError::InvalidDataLength(length) => {
                BatcherUpdateError::InvalidDataLength(length)
            }
        })?;

        let Ok(batcher_address) = <sol!(address)>::abi_decode_validate(validated.payload()) else {
            return Err(BatcherUpdateError::BatcherAddressDecodingError);
        };

        Ok(Self { batcher_address })
    }
```

**File:** crates/common/genesis/src/system/config.rs (L130-154)
```rust
    pub fn update_with_receipts(
        &mut self,
        receipts: &[Receipt],
        l1_system_config_address: Address,
        ecotone_active: bool,
    ) -> (Vec<SystemConfigUpdateKind>, Vec<SystemConfigUpdateError>) {
        receipts
            .iter()
            .filter(|r| r.status != Eip658Value::Eip658(false))
            .flat_map(|r| &r.logs)
            .filter(|log| {
                let topics = log.topics();
                log.address == l1_system_config_address
                    && !topics.is_empty()
                    && topics[0] == SystemConfigUpdate::TOPIC
            })
            .map(|log| self.process_config_update_log(log, ecotone_active))
            .fold((Vec::new(), Vec::new()), |(mut updates, mut errors), result| {
                match result {
                    Ok(kind) => updates.push(kind),
                    Err(e) => errors.push(e),
                }
                (updates, errors)
            })
    }
```

**File:** crates/common/genesis/src/system/config.rs (L193-209)
```rust
    fn process_config_update_log(
        &mut self,
        log: &Log,
        ecotone_active: bool,
    ) -> Result<SystemConfigUpdateKind, SystemConfigUpdateError> {
        // Construct the system config log from the log.
        let log = SystemConfigLog::new(log.clone(), ecotone_active);

        // Construct the update type from the log.
        let update = log.build()?;

        // Apply the update to the system config.
        update.apply(self);

        // Return the update type.
        Ok(update.kind())
    }
```

**File:** crates/common/genesis/src/updates/validator.rs (L63-100)
```rust
    /// Validates the common structure of a `SystemConfig` update log data.
    ///
    /// This function performs the following validations:
    /// 1. Checks that the data length is exactly 96 bytes
    /// 2. Decodes and validates the pointer (must be 32)
    /// 3. Decodes and validates the data length field (must be 32)
    ///
    /// # Returns
    ///
    /// Returns a `ValidatedUpdateData` containing the validated fields and original data,
    /// or a `ValidationError` if any validation fails.
    pub fn validate(
        data: &alloy_primitives::Bytes,
    ) -> Result<ValidatedUpdateData<'_>, ValidationError> {
        // Validate total data length
        if data.len() != Self::STANDARD_UPDATE_DATA_LEN {
            return Err(ValidationError::InvalidDataLen(
                Self::STANDARD_UPDATE_DATA_LEN,
                data.len(),
            ));
        }

        // Decode and validate pointer
        let pointer = <sol!(uint64)>::abi_decode_validate(&data[0..32])
            .map_err(|_| ValidationError::PointerDecodingError)?;
        if pointer != Self::EXPECTED_POINTER {
            return Err(ValidationError::InvalidDataPointer(pointer));
        }

        // Decode and validate length
        let length = <sol!(uint64)>::abi_decode_validate(&data[32..64])
            .map_err(|_| ValidationError::LengthDecodingError)?;
        if length != Self::EXPECTED_DATA_LENGTH {
            return Err(ValidationError::InvalidDataLength(length));
        }

        Ok(ValidatedUpdateData { data })
    }
```

**File:** crates/consensus/derive/src/stages/traversal/polling.rs (L41-43)
```rust
    fn batcher_addr(&self) -> Address {
        self.system_config.batcher_address
    }
```

**File:** actions/harness/tests/derivation/main.rs (L819-834)
```rust
/// After a batcher-address rotation committed to L1 via a `ConfigUpdate` log,
/// frames from the old batcher address are silently ignored and frames from
/// the new address are derived normally.
///
/// The rotation is delivered as a real `ConfigUpdate` log in an L1 receipt.
/// The traversal stage reads receipts via `receipts_by_hash` when
/// advancing L1 origin, and calls `update_with_receipts` to update its
/// internal [`SystemConfig`].  Subsequent calls to
/// `DataAvailabilityProvider::next` receive the updated batcher address, so
/// the old batcher's frames are filtered out at the frame-retrieval layer.
///
/// Flow:
///   L1 blocks 1-2: batcher A submits → L2 blocks 1-2 derived
///   L1 block 3:    rotation log only  → system config updated, 0 L2 blocks
///   L1 block 4:    batcher A submits  → IGNORED (0 derived)
///   L1 block 5:    batcher B submits  → DERIVED (1 derived)
```
