### Title
Unchecked zero `elasticity_multiplier` causes division-by-zero panic in the fault-proof stateless executor - (File: `crates/proof/executor/src/util.rs`)

### Summary
`decode_holocene_eip_1559_params_block_header` and `decode_jovian_eip_1559_params_block_header` in the stateless L2 fault-proof executor only validate that the decoded `denominator` is non-zero, but never validate that `elasticity` is non-zero, before constructing a `BaseFeeParams` that is later used as a divisor in EIP-1559 base-fee arithmetic. This mirrors the "unsafe division with unchecked divisor" bug class from the referenced `LibMathEx.wdiv` report, just applied to Rust integer division instead of Solidity.

### Finding Description
The block-header EIP-1559 parameter decoders in the fault-proof executor check only the denominator: [1](#0-0) [2](#0-1) 

Both functions decode `(elasticity, denominator[, min_base_fee])` from the header's `extra_data`, guard only against `denominator == 0`, and then construct `BaseFeeParams { elasticity_multiplier: elasticity.into(), max_change_denominator: denominator.into() }` unconditionally — `elasticity == 0` is accepted.

This `BaseFeeParams` is passed into `next_block_base_fee`, which for Jovian-active parents calls `calc_next_block_base_fee(gas_used, gas_limit, base_fee, params)`: [3](#0-2) 

The standard EIP-1559 `calc_next_block_base_fee` implementation (from `alloy-eips`) computes `gas_target = gas_limit / params.elasticity_multiplier`. If `elasticity_multiplier == 0`, this is an integer division by zero, which panics in Rust rather than returning an error.

Notably, the equivalent validation logic used by the canonical execution-layer chainspec path (`crates/execution/chainspec/src/basefee.rs`) does check *both* fields before constructing `BaseFeeParams`: [4](#0-3) 

This shows the correct, symmetric validation pattern was known and implemented in one place but not consistently reused in `proof/executor/src/util.rs`, leaving an asymmetric validation gap in the fault-proof stateless executor.

### Impact Explanation
The stateless executor (`crates/proof/executor`) is the block-execution engine used to (re-)derive L2 state and output roots, notably in the fault-proof program that a dispute-game participant relies on to compute the provable output root. Header `extra_data` (which encodes the Holocene/Jovian EIP-1559 params) originates from L2 blocks/derivation input that an attacker can influence (a malicious or buggy block producer can craft a header with `elasticity = 0, denominator != 0`, which passes the header-length/version checks and the denominator-only zero check). When the fault-proof executor subsequently processes such a header as a "parent" during Jovian-active proof derivation, it panics instead of returning a decode error, halting the executor and preventing it from producing a valid provable output root for that block. This directly implicates the "fault-proof program and MPT" reachable surface and the "node halt / wrong provable output root" impact class permitted by the validation rules.

### Likelihood Explanation
Likelihood is moderate: this requires a block header with `elasticity_multiplier == 0` and a non-zero `denominator` to enter the chain's history (via a malicious/misbehaving sequencer or block-derivation bug) and for the Jovian base-fee computation path to later process it as a parent header in the stateless executor. The chainspec-level equivalent (`base_fee_params_from_extra_data`) correctly rejects this combination, indicating the class of malformed input is anticipated by the protocol design — it is only the executor-local decode helper that fails to enforce the same invariant, making this an inconsistency/regression bug rather than a purely theoretical concern.

### Recommendation
Add an explicit `elasticity == 0` check (mirroring `base_fee_params_from_extra_data`'s combined `elasticity == 0 || denominator == 0` validation) to both `decode_holocene_eip_1559_params_block_header` and `decode_jovian_eip_1559_params_block_header` in `crates/proof/executor/src/util.rs`, returning `ExecutorError::InvalidExtraData` (or a new dedicated `ZeroElasticity` variant) instead of allowing the zero value to flow into `calc_next_block_base_fee`.

### Proof of Concept
1. Construct a Holocene- or Jovian-encoded `extra_data` byte sequence with `elasticity = 0` and `denominator != 0` (e.g., `[0, 0,0,0,1, 0,0,0,0]` for Holocene: version=0, denominator=1, elasticity=0).
2. Feed this as a parent header's `extra_data` into `decode_holocene_eip_1559_params_block_header` (or the Jovian variant) — it passes the `denominator == 0` check and returns `Ok(BaseFeeParams { elasticity_multiplier: 0, max_change_denominator: 1 })`.
3. Call the stateless executor's `next_block_base_fee`/`calc_next_block_base_fee` path with this `BaseFeeParams` (Jovian-active parent): the internal `gas_limit / elasticity_multiplier` division panics with a divide-by-zero error, aborting the fault-proof executor process instead of returning a decode error.

### Citations

**File:** crates/proof/executor/src/util.rs (L20-36)
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
```

**File:** crates/proof/executor/src/util.rs (L38-57)
```rust
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

**File:** crates/proof/executor/src/builder/env.rs (L57-90)
```rust
    fn next_block_base_fee(
        &self,
        params: BaseFeeParams,
        parent: &Header,
        min_base_fee: u64,
    ) -> Option<u64> {
        if !self.config.is_jovian_active(parent.timestamp()) {
            return parent.next_block_base_fee(params);
        }

        // Starting from Jovian, we use the maximum of the gas used and the blob gas used to
        // calculate the next base fee.
        let gas_used = if parent.blob_gas_used().unwrap_or_default() > parent.gas_used() {
            parent.blob_gas_used().unwrap_or_default()
        } else {
            parent.gas_used()
        };

        let mut next_block_base_fee = calc_next_block_base_fee(
            gas_used,
            parent.gas_limit(),
            parent.base_fee_per_gas().unwrap_or_default(),
            params,
        );

        // If the next block base fee is less than the min base fee, set it to the min base fee.
        // # Note
        // Before Jovian activation, the min-base-fee is 0 so this is a no-op.
        if next_block_base_fee < min_base_fee {
            next_block_base_fee = min_base_fee;
        }

        Some(next_block_base_fee)
    }
```

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
