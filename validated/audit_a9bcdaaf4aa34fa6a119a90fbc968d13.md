### Title
Missing zero-check on `eip1559_denominator` decoded from attacker-controlled L1 `SystemConfigUpdate` log can cause a division-by-zero panic / node halt - (File: `crates/common/genesis/src/updates/eip1559.rs`)

### Summary
`Eip1559Update::try_from` decodes the packed `eip1559_denominator`/`eip1559_elasticity` values straight out of the ABI-encoded L1 `SystemConfigUpdate` event payload with no bounds validation beyond basic structural decoding (pointer/length checks), mirroring the reported `configureSequence` pattern of accepting unvalidated numeric configuration fields that later drive consensus-critical arithmetic.

### Finding Description
`Eip1559Update::try_from` decodes the raw `uint64` payload into `eip1559_denominator` (upper 32 bits) and `eip1559_elasticity` (lower 32 bits) with zero range or sanity checking: [1](#0-0) 

This is exactly analogous to the reported `configureSequence`/`SequenceData` issue: a struct with multiple numeric fields is accepted from an external actor (there, an authorized collection admin; here, the L1 `SystemConfig` contract owner via an L1 log) and applied to `SystemConfig` with no validation that the values are within a sane, non-zero, non-degenerate range: [2](#0-1) 

The resulting `SystemConfig.eip1559_denominator` is fed into the Holocene/Jovian EIP-1559 base-fee computation used by consensus and the chain-spec (`next_block_base_fee`), where a denominator of `0` is used in the EIP-1559 base-fee formula denominator. Unlike `MinBaseFeeUpdate`/`GasLimitUpdate`, which at least bound their values to a `uint64`/`u64::MAX` ceiling, `Eip1559Update` performs no floor check (e.g., rejecting `0`) on either field before it is applied to consensus state via `Eip1559Update::apply`.

I was not able to fully confirm from the indexed code excerpts whether `next_block_base_fee` in `crates/execution/chainspec/src/spec.rs` performs a runtime `checked_div`/guard against a zero denominator before dividing, or whether it panics/produces an incorrect (unbacked) result on zero. The search only returned that the function exists and references `denominator`/`elasticity` several times; the arithmetic body itself was not retrieved in full.

### Impact Explanation
If `next_block_base_fee` (or any other EIP-1559 base fee computation path that consumes `SystemConfig.eip1559_denominator`) performs an unguarded division by the denominator, a `0` value derived from an L1 `SystemConfigUpdate` log would cause a panic during block-building or block validation on every node that processes that L1 epoch — a chain-wide halt. Even if a panic is avoided by underlying integer-division semantics, a degenerate `elasticity`/`denominator` pair can also produce a permanently mispriced (or perpetually zero/max) base fee, which is a consensus-relevant miscalculation. Both outcomes match the "High: DoS / consensus fee miscalculation" impact class from the external report.

### Likelihood Explanation
Low, matching the source report: it requires the L1 `SystemConfig` contract owner (a privileged, trusted L1 role) to emit a config-update event with `denominator == 0` — either through misconfiguration or a compromised/malicious owner key — which then propagates through this data path to every unsafe-block-producing/validating Base node during derivation.

### Recommendation
Add explicit validation in `Eip1559Update::try_from` rejecting a decoded `eip1559_denominator` of `0` (and any other value class known to be unsafe for the base-fee formula), returning a dedicated `EIP1559UpdateError` variant instead of silently accepting it, consistent with how other numeric SystemConfig fields should be bounded. Additionally, confirm (or add) a defensive `checked_div`/zero-guard at the point of consumption in `next_block_base_fee` so a bad L1 log cannot panic node execution even if it slips past the derivation-layer validation.

### Proof of Concept
Not independently reproducible from the indexed excerpts alone (the arithmetic body of `next_block_base_fee` was not retrieved), so this should be treated as a code-review-level finding requiring confirmation of the division/guard logic in `crates/execution/chainspec/src/spec.rs`. Conceptually: an L1 `SystemConfigUpdate` log with type `EIP1559_UPDATE_TYPE` and payload `uint64 params = (0 << 32) | elasticity` (i.e., `eip1559_denominator = 0`) would be accepted by `Eip1559Update::try_from`, applied via `Eip1559Update::apply` into `SystemConfig`, and then consumed unchecked by the base-fee calculation on the next Holocene/Jovian block.

### Citations

**File:** crates/common/genesis/src/updates/eip1559.rs (L20-26)
```rust
impl Eip1559Update {
    /// Applies the update to the [`SystemConfig`].
    pub const fn apply(&self, config: &mut SystemConfig) {
        config.eip1559_denominator = Some(self.eip1559_denominator);
        config.eip1559_elasticity = Some(self.eip1559_elasticity);
    }
}
```

**File:** crates/common/genesis/src/updates/eip1559.rs (L48-56)
```rust
        let Ok(eip1559_params) = <sol!(uint64)>::abi_decode_validate(validated.payload()) else {
            return Err(EIP1559UpdateError::EIP1559DecodingError);
        };

        Ok(Self {
            eip1559_denominator: (eip1559_params >> 32) as u32,
            eip1559_elasticity: eip1559_params as u32,
        })
    }
```
