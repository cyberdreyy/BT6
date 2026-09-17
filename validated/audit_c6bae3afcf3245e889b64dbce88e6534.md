### Title
Unbounded L1-derived `gas_limit` in `SystemConfig` accepted without sanity bounds before becoming the L2 block gas limit - (File: `crates/common/genesis/src/updates/gas_limit.rs`)

### Summary
The bug report flags that Kelp's LST price oracles trust externally supplied rate values with no upper/lower bound checks, no fallback, and no protection against unreasonable values from an upgradeable/EOA-controlled source. The Base analog is the derivation-side handling of the `GasLimit` `SystemConfigUpdate`: the value emitted by the L1 `SystemConfig` contract is decoded and applied to `SystemConfig.gas_limit` with only a structural/overflow check, and is then propagated verbatim into L2 payload attributes and ultimately into the produced block's `gas_limit` header field, with no sanity bound (minimum, maximum, or rate-of-change) enforced anywhere in the pipeline.

### Finding Description
`GasLimitUpdate::try_from` in `crates/common/genesis/src/updates/gas_limit.rs` decodes the ABI-encoded `bytes` payload of a `ConfigUpdate(GasLimit)` log emitted by the L1 `SystemConfig` contract. It validates the ABI pointer/length framing and clamps only against `u64::MAX` overflow: [1](#0-0) 

`GasLimitUpdate::apply` then unconditionally overwrites `SystemConfig.gas_limit` with this value, with no lower bound (e.g., rejecting 0 or near-zero limits) and no upper bound relative to the previous value: [2](#0-1) 

This unchecked value is folded into the chain state via `SystemConfig::update_with_receipts` / `process_config_update_log`, which explicitly documents that "malformed or invalid updates are skipped" but performs no semantic validation of the *value* itself — only structural decode success: [3](#0-2) 

The resulting `sys_config.gas_limit` is passed straight through into the L2 `BasePayloadAttributes.gas_limit` field by the stateful attributes builder (`crates/consensus/derive/src/attributes/stateful.rs`) with no re-validation: [4](#0-3) 

On the execution side, `ensure_well_formed_attributes` in the engine validator only checks that `gas_limit` is *present*, not that it is within any sane bound: [5](#0-4) 

and the payload builder / block-env construction takes this value directly as the block's gas limit: [6](#0-5) 

This is structurally identical to the reported bug class: an externally-sourced numeric parameter (LST rate ↔ L1-derived gas limit) is consumed by downstream logic with only structural/format checks, but no bounds, no fallback to a previous good value, and no rate-of-change guard — exactly the gap the Kelp team was asked to close for `getRate()`.

### Impact Explanation
The L1 `SystemConfig` owner (an L1 contract call whose emitted log Base's derivation pipeline treats as authoritative "system-config data") can set an arbitrary `gas_limit`, including 0 or a value far below the chain's practical minimum. Because no floor is enforced anywhere in the derivation → payload-attributes → engine-validation → block-building chain, a sequencer/execution client would build blocks with that degenerate gas limit. A gas limit of 0 (or a value smaller than the intrinsic gas of the mandatory L1-info deposit transaction) would make it impossible to include even the required system deposit transaction in the block, which can stall block production/derivation for the affected L2 chain (denial of service / halted block building) and would also cause divergence between nodes that happen to have any implicit floor versus this codebase, which has none — a chain-split/halt-class impact.

### Likelihood Explanation
This requires control of, or a bug/compromise in, the L1 `SystemConfig` contract's `ConfigUpdate(GasLimit)` emission path (the "attacker-written L1 ... system-config data" derivation category explicitly in scope). Given `SystemConfig` gas-limit changes are a normal, expected on-chain configuration operation (unlike batcher/signer keys which are more tightly held), and the value is fully attacker/administrator-controlled with zero semantic validation downstream, the likelihood of an operational mistake or compromise producing a degenerate value reaching production block-building logic unchecked is realistic, especially since the code path is exercised by ordinary upgrade/derivation tests without any bound assertions.

### Recommendation
Add a semantic validation step to `GasLimitUpdate::try_from` (or to `SystemConfig::update_with_receipts`) that rejects/clamps `gas_limit` values outside a protocol-defined safe range (e.g., a minimum equal to or above the intrinsic gas of the mandatory L1 info/system deposit transactions plus a reasonable buffer, and a sane maximum). On rejection, retain the previous validated `gas_limit` (fallback to last-known-good) rather than applying the malformed/out-of-range value, mirroring the "upper/lower bound + fallback to previous value" mitigation recommended for the LST oracle. Additionally, add a defensive floor check in `ensure_well_formed_attributes` and/or `BaseNextBlockEnvAttributes` construction so that even if an out-of-range value reaches the engine, block building does not silently proceed with an unusable gas limit.

### Proof of Concept
1. As the L1 `SystemConfig` owner (or via a bug allowing arbitrary log injection matching the `ConfigUpdate` topic/version), emit a `ConfigUpdate(GasLimit)` event with `data` ABI-encoding `gas_limit = 0` (or `21_000`, below the size of the mandatory `TxDeposit` for L1 attributes).
2. `SystemConfig::update_with_receipts` decodes this successfully (all structural checks pass) and calls `GasLimitUpdate::apply`, setting `SystemConfig.gas_limit = 0`. See [7](#0-6) .
3. The next `StatefulAttributesBuilder::prepare_payload_attributes` call embeds this value directly as `BasePayloadAttributes.gas_limit`. See [4](#0-3) .
4. `BaseEngineValidator::ensure_well_formed_attributes` only checks `gas_limit.is_none()`, which passes since `Some(0)` is present. See [5](#0-4) .
5. The payload builder uses this as the literal block gas limit (`BaseNextBlockEnvAttributes::build_next_env`), producing a block that cannot include the mandatory L1-info deposit transaction, stalling block/derivation progress for the chain. See [6](#0-5) .

I was not able to execute this end-to-end in a live environment (no test harness run performed); the analysis is based on static code tracing across the cited files, and I did not find any additional bound/floor check elsewhere in the repository during this investigation. If such a check exists in a file not surfaced by search (e.g., a reth-level `gas_limit` validity check in block import), that would materially reduce or negate this finding, so it would be worth explicitly confirming with the codebase owners whether reth's generic header validation enforces a nonzero/minimum gas limit for OP-stack/Base blocks before treating this as a fully confirmed vulnerability.

### Citations

**File:** crates/common/genesis/src/updates/gas_limit.rs (L16-57)
```rust
impl GasLimitUpdate {
    /// Applies the update to the [`SystemConfig`].
    pub const fn apply(&self, config: &mut SystemConfig) {
        config.gas_limit = self.gas_limit;
    }
}

impl TryFrom<&SystemConfigLog> for GasLimitUpdate {
    type Error = GasLimitUpdateError;

    fn try_from(log: &SystemConfigLog) -> Result<Self, Self::Error> {
        let LogData { data, .. } = &log.log.data;
        if data.len() != 96 {
            return Err(GasLimitUpdateError::InvalidDataLen(data.len()));
        }

        let Ok(pointer) = <sol!(uint64)>::abi_decode_validate(&data[0..32]) else {
            return Err(GasLimitUpdateError::PointerDecodingError);
        };
        if pointer != 32 {
            return Err(GasLimitUpdateError::InvalidDataPointer(pointer));
        }

        let Ok(length) = <sol!(uint64)>::abi_decode_validate(&data[32..64]) else {
            return Err(GasLimitUpdateError::LengthDecodingError);
        };
        if length != 32 {
            return Err(GasLimitUpdateError::InvalidDataLength(length));
        }

        let Ok(gas_limit) = <sol!(uint256)>::abi_decode_validate(&data[64..]) else {
            return Err(GasLimitUpdateError::GasLimitDecodingError);
        };

        // Prevent overflows here.
        let max = U256::from(u64::MAX as u128);
        if gas_limit > max {
            return Err(GasLimitUpdateError::GasLimitDecodingError);
        }

        Ok(Self { gas_limit: U64::from(gas_limit).saturating_to::<u64>() })
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

**File:** crates/consensus/derive/src/attributes/stateful.rs (L264-266)
```rust
            gas_limit: Some(u64::from_be_bytes(
                alloy_primitives::U64::from(sys_config.gas_limit).to_be_bytes(),
            )),
```

**File:** crates/execution/node/src/engine.rs (L306-310)
```rust
        if attributes.gas_limit.is_none() {
            return Err(EngineObjectValidationError::InvalidParams(
                "MissingGasLimitInPayloadAttributes".to_string().into(),
            ));
        }
```

**File:** crates/execution/payload/src/payload.rs (L532-540)
```rust
        Ok(Self {
            timestamp: attributes.payload_attributes.timestamp,
            suggested_fee_recipient: attributes.payload_attributes.suggested_fee_recipient,
            prev_randao: attributes.payload_attributes.prev_randao,
            gas_limit: attributes.gas_limit.unwrap_or_else(|| parent.gas_limit()),
            parent_beacon_block_root: attributes.payload_attributes.parent_beacon_block_root,
            extra_data,
        })
    }
```
