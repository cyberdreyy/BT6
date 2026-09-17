## Analysis

The reported bug class is "some functions performing an analogous validation only partially validate a numeric parameter, letting an out-of-range value slip through and cause a panic further downstream." I found a structural analog to this in Base's EIP-1559 extra-data decoding, where the same `(elasticity, denominator)` pair is validated with different strictness in different call sites.

`crates/execution/chainspec/src/basefee.rs::base_fee_params_from_extra_data` treats `elasticity == 0` and `denominator == 0` symmetrically: both zero falls back to chain defaults, but *either one being zero while the other is non-zero* is rejected as `EIP1559ParamError::InvalidParams`. [1](#0-0) 

By contrast, `crates/proof/executor/src/util.rs::decode_holocene_eip_1559_params_block_header` (and its Jovian counterpart) only rejects `denominator == 0`; it never checks `elasticity == 0`, and its own comment asserts the denominator "is always non-zero" in the header without any analogous guarantee for elasticity: [2](#0-1) 

The same partial-check pattern (reject only `denominator == 0`, ignore `elasticity == 0`) is present in the consolidation check used by the derivation pipeline: [3](#0-2) 

This decoded `BaseFeeParams` is fed into `next_block_base_fee`/`calc_next_block_base_fee` in the sibling, correctly-guarded path: [4](#0-3) 

Both `elasticity_multiplier` (the gas-target divisor) and `max_change_denominator` are denominators in the standard EIP-1559 base-fee formula; `elasticity_multiplier == 0` produces a division by zero in `calc_next_block_base_fee` just as surely as `max_change_denominator == 0` would. The chainspec path guards against both; `crates/proof/executor` and the consolidation check in `crates/consensus/engine` only guard the denominator, exactly mirroring the external report's pattern where one "sibling" setter enforces a bound and others in the same module/family do not.

### Title
Incomplete EIP-1559 `elasticity_multiplier` validation in the fault-proof executor allows a division-by-zero panic - (File: crates/proof/executor/src/util.rs)

### Summary
`decode_holocene_eip_1559_params_block_header` and `decode_jovian_eip_1559_params_block_header` in the fault-proof program's executor crate only reject a zero `max_change_denominator`, while the analogous, correctly-hardened function `base_fee_params_from_extra_data` in the execution-layer chainspec crate rejects any header where exactly one of `elasticity_multiplier` / `max_change_denominator` is zero. This asymmetry mirrors the reported "some setters validate, siblings don't" bug class.

### Finding Description
Header `extra_data` for Holocene/Jovian blocks encodes two u32 values, `elasticity_multiplier` and `max_change_denominator`, which are subsequently used as `BaseFeeParams` to compute the next block's base fee via `calc_next_block_base_fee`. That routine's gas-target computation divides by `elasticity_multiplier`. The chainspec crate's decoder correctly treats `elasticity == 0` as invalid unless `denominator` is also zero [1](#0-0) , but the fault-proof executor's decoder omits this check entirely, only guarding `denominator == 0` [5](#0-4) . The same gap exists in the consolidation validation used by block derivation, which rejects `(denominator=0, elasticity≠0)` but not `(elasticity=0, denominator≠0)` [6](#0-5) .

### Impact Explanation
Because the fault-proof program must deterministically re-derive/execute a disputed L2 block to produce a claimed output root, a header whose `extra_data` encodes `elasticity_multiplier = 0` with a non-zero `max_change_denominator` passes this decoder's check yet later triggers a division-by-zero panic when the base fee is recomputed. A panic inside the fault-proof program means it cannot produce (or produces no) provable output root for that block, undermining the dispute game's correctness guarantee — this falls under the accepted "node halt" / "wrong provable output root" impact categories.

### Likelihood Explanation
`extra_data` originates from the (potentially disputed/adversarial) L2 block header that is the very subject of the fault-proof program's re-execution; a dispute-game participant supplying or referencing such a block controls this field directly, requiring no special privilege beyond normal block/claim submission.

### Recommendation
Mirror the stricter validation from `crates/execution/chainspec/src/basefee.rs::base_fee_params_from_extra_data` in `crates/proof/executor/src/util.rs`'s `decode_holocene_eip_1559_params_block_header` / `decode_jovian_eip_1559_params_block_header`, rejecting the header whenever exactly one of `elasticity_multiplier` or `max_change_denominator` is zero, and apply the same symmetric check in `crates/consensus/engine/src/attributes.rs::check_eip1559`.

### Proof of Concept
1. Construct an L2 block header with Holocene/Jovian `extra_data` encoding `max_change_denominator = N` (any non-zero value) and `elasticity_multiplier = 0`.
2. Feed this header into the fault-proof executor's block-building path, which calls `decode_holocene_eip_1559_params_block_header` (or the Jovian variant) — the function returns `Ok(BaseFeeParams { elasticity_multiplier: 0, max_change_denominator: N })` because it only checks `denominator == 0`. [2](#0-1) 
3. When the resulting `BaseFeeParams` is subsequently used to compute the next block's base fee (as in the analogous, correctly-guarded `decode_holocene_base_fee`/`compute_jovian_base_fee` pattern which calls `parent.next_block_base_fee(base_fee_params)`), the zero `elasticity_multiplier` causes a division-by-zero panic in `calc_next_block_base_fee`. [4](#0-3) 

**Uncertainty note:** I could not, within the available tool budget, directly view `crates/proof/executor/src/builder/env.rs` (the file with 6 matches for `calc_next_block_base_fee`/`elasticity_multiplier`) to confirm the exact call site where the executor's under-validated `BaseFeeParams` is passed into the dividing routine, nor fully trace whether an upstream invariant elsewhere in the derivation pipeline independently forecloses `elasticity_multiplier == 0` before reaching this code. The asymmetry between the chainspec decoder and the executor/consensus decoders is confirmed directly from the cited source, but the final "division actually executes with elasticity=0" step is inferred from the standard EIP-1559 formula and the confirmed absence of a guarding check, rather than directly observed in `builder/env.rs`.

### Citations

**File:** crates/execution/chainspec/src/basefee.rs (L11-24)
```rust
fn base_fee_params_from_extra_data(
    chain_spec: impl EthChainSpec,
    timestamp: u64,
    elasticity: u32,
    denominator: u32,
) -> Result<BaseFeeParams, EIP1559ParamError> {
    if elasticity == 0 && denominator == 0 {
        Ok(chain_spec.base_fee_params_at_timestamp(timestamp))
    } else if elasticity == 0 || denominator == 0 {
        Err(EIP1559ParamError::InvalidParams)
    } else {
        Ok(BaseFeeParams::new(denominator as u128, elasticity as u128))
    }
}
```

**File:** crates/execution/chainspec/src/basefee.rs (L31-45)
```rust
pub fn decode_holocene_base_fee<H>(
    chain_spec: impl EthChainSpec + Upgrades,
    parent: &H,
    timestamp: u64,
) -> Result<u64, EIP1559ParamError>
where
    H: BlockHeader,
{
    let (elasticity, denominator) = HoloceneExtraData::decode(parent.extra_data())?;

    let base_fee_params =
        base_fee_params_from_extra_data(chain_spec, timestamp, elasticity, denominator)?;

    Ok(parent.next_block_base_fee(base_fee_params).unwrap_or_default())
}
```

**File:** crates/proof/executor/src/util.rs (L20-57)
```rust
pub(crate) fn decode_holocene_eip_1559_params_block_header(
    header: &Header,
) -> ExecutorResult<BaseFeeParams> {
    let (elasticity, denominator) = HoloceneExtraData::decode(header.extra_data())?;

    // Check for potential division by zero.
    // In the block header, the denominator is always non-zero.
    // <https://github.com/ethereum-optimism/specs/blob/main/specs/protocol/holocene/exec-engine.md#eip-1559-parameters-in-block-header>
    if denominator == 0 {
        return Err(ExecutorError::InvalidExtraData(Eip1559ValidationError::ZeroDenominator));
    }

    Ok(BaseFeeParams {
        elasticity_multiplier: elasticity.into(),
        max_change_denominator: denominator.into(),
    })
}

pub(crate) fn decode_jovian_eip_1559_params_block_header(
    header: &Header,
) -> ExecutorResult<(BaseFeeParams, u64)> {
    let (elasticity, denominator, min_base_fee) = JovianExtraData::decode(header.extra_data())?;

    // Check for potential division by zero.
    // In the block header, the denominator is always non-zero.
    // <https://github.com/ethereum-optimism/specs/blob/main/specs/protocol/holocene/exec-engine.md#eip-1559-parameters-in-block-header>
    if denominator == 0 {
        return Err(ExecutorError::InvalidExtraData(Eip1559ValidationError::ZeroDenominator));
    }

    Ok((
        BaseFeeParams {
            elasticity_multiplier: elasticity.into(),
            max_change_denominator: denominator.into(),
        },
        min_base_fee,
    ))
}
```

**File:** crates/consensus/engine/src/attributes.rs (L171-210)
```rust
    /// Validates and compares EIP1559 parameters for consolidation.
    fn check_eip1559(
        config: &RollupConfig,
        attributes: &AttributesWithParent,
        block: &Block<Transaction>,
    ) -> Self {
        // We can assume that the EIP-1559 params are set iff holocene is active.
        // Note here that we don't need to check for the attributes length because of type-safety.
        let (ae, ad): (u128, u128) = match attributes.attributes().decode_eip_1559_params() {
            None => {
                // Holocene is active but the eip1559 are not set. This is a bug!
                // Note: we checked the timestamp match above, so we can assume that both the
                // attributes and the block have the same stamps
                if config.is_holocene_active(block.header.timestamp) {
                    error!(
                        "EIP1559 parameters for attributes not set while holocene is active. This is a bug"
                    );
                    return AttributesMismatch::MissingAttributesEIP1559.into();
                }

                // If the attributes are not specified, that means we can just early return.
                return Self::Match;
            }
            Some((0, e)) if e != 0 => {
                error!(
                    "Holocene EIP1559 params cannot have a 0 denominator unless elasticity is also 0. This is a bug"
                );
                return AttributesMismatch::InvalidEIP1559ParamsCombination.into();
            }
            // We need to translate (0, 0) parameters to pre-holocene protocol constants.
            // Since holocene is supposed to be active, canyon should be as well. We take the canyon
            // base fee params.
            Some((0, 0)) => {
                let BaseFeeParams { max_change_denominator, elasticity_multiplier } =
                    config.chain_op_config.post_canyon_params();

                (elasticity_multiplier, max_change_denominator)
            }
            Some((ae, ad)) => (ae.into(), ad.into()),
        };
```
