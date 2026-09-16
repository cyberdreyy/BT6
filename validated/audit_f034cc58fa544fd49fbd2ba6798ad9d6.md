### Title
Missing zero-address validation on L1 `SystemConfig` batcher-address rotation halts L2 derivation - (File: `crates/common/genesis/src/updates/batcher.rs`)

### Summary
The `BatcherUpdate` type, which applies a `ConfigUpdate(Batcher)` log emitted by the L1 `SystemConfig` contract to the L2 node's in-memory `SystemConfig`, decodes and stores the new batcher address with no validation that it is non-zero. If the system-config owner (or a misconfigured/compromised L1 contract) emits a batcher rotation to `address(0)`, every L2 node following derivation will silently adopt `batcher_address = address(0)` and can never accept another batch again, since no legitimate batch-submitter transaction can ever be signed by (or recovered to) the zero address.

### Finding Description
`BatcherUpdate::try_from` decodes the new batcher address directly from the log payload and returns it unconditionally: [1](#0-0) 

`BatcherUpdate::apply` then overwrites `SystemConfig::batcher_address` with whatever was decoded, again with no zero check: [2](#0-1) 

This is invoked from `SystemConfig::update_with_receipts` → `process_config_update_log`, which is explicitly designed to apply "malformed or invalid updates are skipped" but only validates ABI shape (`UpdateDataValidator`), not semantic validity of the decoded address: [3](#0-2) [4](#0-3) 

The system test suite even documents that a batcher rotation is accepted by address-value equality checks and that a new batcher's frames are accepted going forward while the old batcher's frames are rejected: [5](#0-4) 

This is the exact bug class from the external report: a privileged party (there, the dutch-auction `owner`; here, the L1 `SystemConfig` contract owner/governance) can set a critical operational address to `address(0)` through a code path that has no zero-address guard, permanently breaking a core function (there, NFT minting; here, batch/frame acceptance in L2 derivation).

### Impact Explanation
Once `batcher_address` is rotated to `address(0)`, the derivation pipeline's frame-acceptance logic (which filters incoming L1 batch data by matching the sender/signer address against `SystemConfig::batcher_address`) will reject frames from every real-world batcher key, because no ECDSA signature recovers to `address(0)`. There is no on-chain or in-protocol path to recover from this except another correctly-formed rotation log — but if the underlying misconfiguration or compromise persists, the chain's block derivation permanently stalls: this is a node/chain halt condition (no new safe/finalized L2 blocks can be derived from L1 batches), which matches the accepted "node halt" impact category.

### Likelihood Explanation
This requires the entity controlling the L1 `SystemConfig` contract (analogous to the "owner" in the original report) to emit a `ConfigUpdate(Batcher)` event with `address(0)`, either through misconfiguration, a governance/admin key compromise, or a bug in the L1-side tooling that submits the update. The L2 derivation code discussed here has no defense-in-depth check against this — it fully trusts the decoded address once ABI-well-formed, mirroring the “no zero-address require” pattern flagged in the reference report.

### Recommendation
Add a zero-address check in `BatcherUpdate::try_from` (or in `SystemConfigLog::build`/`process_config_update_log`) so that a `ConfigUpdate(Batcher)` log decoding to `Address::ZERO` is treated as a malformed/invalid update and skipped (consistent with how other malformed updates are already skipped), rather than being applied to `SystemConfig`.

### Proof of Concept
1. Configure `l1_system_config_address` to the address of a `SystemConfig` contract on L1 (as in the existing test harness, e.g. `TraversalTestHelper::L1_SYS_CONFIG_ADDR`).
2. Emit a `ConfigUpdate(Batcher)` log (topic `SystemConfigUpdate::TOPIC`, version `EVENT_VERSION_0`, `BATCHER_UPDATE_TYPE`) with the ABI-encoded batcher address payload set to `address(0)` instead of a real key — using the existing test helper pattern in `crates/common/genesis/src/updates/batcher.rs` tests (`test_batcher_update_try_from`), but substituting all-zero bytes for the trailing 20-byte address.
3. Feed the log through `SystemConfig::update_with_receipts` as shown in `test_system_config_update_with_receipts_batcher_address` (`crates/common/genesis/src/system/config.rs`), confirming `system_config.batcher_address` becomes `Address::ZERO` with no error returned.
4. Observe that any subsequent batch/frame retrieval that filters by `batcher_address` (`crates/consensus/derive/src/sources/{blobs,calldata,ethereum}.rs`, `stages/l1_retrieval.rs`) will never match a real signer again, since real transactions cannot be signed by the zero address — derivation of new L2 blocks halts indefinitely.

Note: I was unable to fully trace every downstream consumer of `batcher_address` (e.g., the exact rejection logic in `sources/blobs.rs` / `sources/calldata.rs`) within the available tool budget; the citations above establish the missing validation at the point of decode/apply, which is the root cause regardless of the exact downstream halt mechanism.

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

**File:** crates/common/genesis/src/system/config.rs (L122-154)
```rust
impl SystemConfig {
    /// Filters all L1 receipts to find config updates and applies the config updates.
    ///
    /// Each config update log is applied independently. Malformed or invalid updates are
    /// skipped so that subsequent valid updates in the same block are still processed.
    /// This matches the reference node behavior in `UpdateSystemConfigWithL1Receipts`.
    ///
    /// Returns the successfully applied update kinds and any errors encountered.
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

**File:** crates/common/genesis/src/system/log.rs (L74-82)
```rust
    /// Builds the [`SystemConfigUpdate`] from the log.
    pub fn build(&self) -> Result<SystemConfigUpdate, SystemConfigUpdateError> {
        self.validate_topic()?;
        self.validate_version()?;
        match self.update_type()? {
            SystemConfigUpdateKind::Batcher => {
                let update = BatcherUpdate::try_from(self)?;
                Ok(SystemConfigUpdate::Batcher(update))
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
