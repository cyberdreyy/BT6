### Title
Missing zero-address check on `BatcherUpdate::apply` allows a compromised/malicious L1 `SystemConfig` batcher-rotation log to permanently halt L2 derivation - ([File: crates/common/genesis/src/updates/batcher.rs])

### Summary
`BatcherUpdate::apply` writes an ABI-decoded L1 `SystemConfigUpdate` batcher address directly into `SystemConfig.batcher_address` with no non-zero validation, mirroring the reported `setYieldTrackers()` pattern where an unchecked address is accepted into a config array/field and later causes functional breakage for legitimate callers.

### Finding Description
`BatcherUpdate::try_from` decodes the L1 log payload and only validates ABI shape (length/pointer/length fields via `UpdateDataValidator`), not the semantic validity of the decoded address: [1](#0-0) 

`BatcherUpdate::apply` then unconditionally overwrites the live `SystemConfig.batcher_address`: [2](#0-1) 

`SystemConfig.batcher_address` is consensus-critical: the derivation pipeline's calldata/blob sources filter L1 DA transactions by comparing the recovered transaction sender against exactly this field, e.g.: [3](#0-2) [4](#0-3) 

If the batcher-rotation event on L1 (attacker-writable system-config data per this program's reachable-paths list) encodes `address(0)` as the new batcher, `apply()` sets `batcher_address = Address::ZERO` with no guard, exactly as the referenced report's `setYieldTrackers()` accepts a zero address into its tracked-array/field without a check.

### Impact Explanation
Once `batcher_address` becomes `Address::ZERO`, every derivation source that filters L1 batcher transactions by `tx.from == batcher_address` (calldata and blob sources) will never match any real transaction, because a valid ECDSA-recovered sender can never be the zero address. All subsequent batcher submissions are silently dropped by every node deriving from L1, which halts safe/derived L2 chain progress network-wide until a further corrective config update is posted (if the same buggy path allows a fix) or a hard intervention occurs. This is a node/chain halt condition — no new blocks can be derived safely — consistent with the required "node halt" impact class.

### Likelihood Explanation
The trigger is a single malformed/malicious `SystemConfigUpdate` log for the batcher type with a zero-address payload, which is valid at the ABI-decoding layer (fixed-length, well-formed pointer/length) and thus passes `UpdateDataValidator::validate` and `BatcherUpdate::try_from` without error, only to be misapplied because there is no downstream zero-address check. This requires the ability to emit such a log from the L1 `SystemConfig` contract (typically gated to its privileged updater), so likelihood is bounded by that L1-side privilege, matching the "Medium" acknowledged severity of the original report for a similarly gated but unchecked write path.

### Recommendation
Add an explicit zero-address check either in `BatcherUpdate::try_from` (reject decode) or in `BatcherUpdate::apply`/`SystemConfig::update_with_receipts` (skip/flag the update and keep the previous batcher address, matching the existing "skip malformed update" pattern already used for other update kinds), and add a golden/unit test analogous to `test_update_with_receipts_continues_past_malformed_log` asserting a zero-address batcher update is rejected rather than applied.

### Proof of Concept
1. Craft an L1 receipt containing a `SystemConfigUpdate` log with `update_type = BATCHER_UPDATE_TYPE` and ABI-encoded payload equal to `address(0)` (well-formed pointer=32, length=32, valid address decode of all-zero bytes).
2. Feed the receipt through `SystemConfig::update_with_receipts` as done in `crates/common/genesis/src/system/config.rs` tests.
3. Observe `system_config.batcher_address == Address::ZERO` after the update (no error raised, unlike the "malformed" case in `test_update_with_receipts_continues_past_malformed_log`).
4. Drive derivation (as in `crates/consensus/derive/src/sources/calldata.rs::test_calldata_source_rejects_old_batcher_after_config_update`) with the new zero `batcher_address`; any real signed batcher transaction is now rejected because its recovered `from` can never equal `Address::ZERO`, causing `source.calldata` to remain empty and derivation to stall.

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

**File:** crates/consensus/derive/src/sources/calldata.rs (L297-322)
```rust
    /// After a `SystemConfig` batcher address update (modeled as changing the
    /// `batcher_address` passed to `load_calldata`), transactions signed by the
    /// OLD batcher are rejected while transactions signed by the NEW batcher
    /// are accepted.
    #[tokio::test]
    async fn test_calldata_source_rejects_old_batcher_after_config_update() {
        let batch_inbox_address = address!("0123456789012345678901234567890123456789");
        let tx = test_legacy_tx(batch_inbox_address);
        let original_batcher = tx.recover_signer().unwrap();

        let mut source = default_test_calldata_source();
        source.batch_inbox_address = batch_inbox_address;
        let block_info = BlockInfo::default();
        source.chain_provider.insert_block_with_transactions(0, block_info, vec![tx.clone()]);

        // With the original batcher address, calldata is accepted.
        assert!(source.load_calldata(&block_info, original_batcher).await.is_ok());
        assert!(!source.calldata.is_empty());

        // Simulate batcher rotation: clear source state and use a new batcher address.
        source.clear();
        let rotated_batcher = address!("00000000000000000000000000000000DeaDBeef");
        assert!(source.load_calldata(&block_info, rotated_batcher).await.is_ok());
        // The same transaction is now rejected because the signer does not match.
        assert!(source.calldata.is_empty());
    }
```

**File:** crates/consensus/derive/src/sources/blobs.rs (L551-570)
```rust
    #[test]
    fn test_extract_blob_data_non_batcher_blobs_excluded() {
        // Case 1: source.batcher_address = Address::ZERO does not match the tx's batch inbox
        // address from `ChainConfig::MAINNET`, so the transaction is skipped and no
        // blobs are captured.
        let source = default_test_blob_source(); // batch_inbox_address = Address::ZERO
        let batcher_address = valid_blob_batcher_address();
        let (data, hashes) = source.extract_blob_data(valid_blob_txs(), batcher_address);
        assert!(data.is_empty(), "non-batcher blobs must not be captured (data)");
        assert!(hashes.is_empty(), "non-batcher blob hashes must not be captured");

        // Case 2: correct batch inbox address → all 5 blobs from the batcher transaction captured.
        let mut source2 = default_test_blob_source();
        let batch_inbox_address = ChainConfig::MAINNET.batch_inbox_address;
        source2.batcher_address = batch_inbox_address;
        let batcher_address = valid_blob_batcher_address();
        let (data, hashes) = source2.extract_blob_data(valid_blob_txs(), batcher_address);
        assert_eq!(data.len(), 5, "all 5 batcher blobs must be captured");
        assert_eq!(hashes.len(), 5, "all 5 batcher blob hashes must be captured");
    }
```
