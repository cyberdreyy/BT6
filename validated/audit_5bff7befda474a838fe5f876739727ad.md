### Title
Missing `elasticity` zero-check in the fault-proof executor's EIP-1559 header decoders can panic the stateless block builder - (File: `crates/proof/executor/src/util.rs`)

### Summary
The proof-executor's Holocene/Jovian extra-data decoders only guard against a zero `denominator`, but not a zero `elasticity_multiplier`, even though the sibling implementation in the execution chainspec crate explicitly validates both. Since `elasticity_multiplier` is later used as a divisor when computing the next block's base fee, a header whose extra data encodes `elasticity == 0` (with a non-zero denominator) will not be rejected by the proof executor and can panic the stateless L2 builder used by the fault-proof program.

### Finding Description
`decode_holocene_eip_1559_params_block_header` and `decode_jovian_eip_1559_params_block_header` in [1](#0-0)  decode the EIP-1559 `elasticity`/`denominator` pair from a block header's `extra_data` and build a `BaseFeeParams`. Both functions comment "Check for potential division by zero" but only implement the check for `denominator == 0`; `elasticity == 0` is accepted unconditionally and packed straight into `BaseFeeParams { elasticity_multiplier: elasticity.into(), .. }`.

This is inconsistent with the equivalent logic in the execution chainspec crate, `base_fee_params_from_extra_data` in [2](#0-1) , which explicitly rejects the case `elasticity == 0 || denominator == 0` (unless both are zero, signaling "use chain defaults"). The chainspec crate even has regression tests asserting this rejection, e.g. [3](#0-2) .

The `BaseFeeParams` produced by the proof-executor's under-validated decoder flows into `calc_next_block_base_fee` (from `alloy_eips`) via `next_block_base_fee`/`prepare_block_env` in [4](#0-3) . `calc_next_block_base_fee`'s EIP-1559 target-gas computation divides `gas_limit` by `elasticity_multiplier`; with `elasticity_multiplier == 0` this is an integer division by zero, which panics in Rust rather than returning a `Result::Err`.

The `StatelessL2Builder` in `crates/proof/executor` is exactly the kind of stateless, header-driven block execution engine used by the fault-proof program to independently re-derive and validate L2 state transitions from on-chain header data — one of the explicitly in-scope reachable surfaces for this analysis ("the fault-proof program and MPT").

### Impact Explanation
If a block header with an `elasticity == 0`, non-zero `denominator` extra-data encoding is ever presented to the stateless proof executor (e.g., during dispute-game execution, or fault-proof re-derivation of a chain segment), the decoder silently accepts it and the subsequent base-fee computation panics. This halts the fault-proof program/stateless executor for that block, preventing it from producing a state/output root — i.e., a wrong/unavailable provable output root or an unrecoverable halt of the proof-generation path, which maps to a valid Medium-severity impact under the given scope (node/proof-program halt, wrong provable output root).

### Likelihood Explanation
The likelihood depends on whether such a header combination can ever reach canonical or disputed chain data. Under normal sequencer operation, `ensure_well_formed_attributes` in [5](#0-4)  and the consolidation check `check_eip1559` in [6](#0-5)  validate the elasticity/denominator pair before a block is built or accepted, which reduces the likelihood of a "normal" chain producing such a header. However, the proof executor is designed to independently re-derive execution from raw header/extra-data without depending on that earlier validation, and the missing check represents a genuine gap versus the parallel, already-hardened chainspec implementation — any divergence in how a header reaches the fault-proof program (e.g. different derivation path, different validation ordering, or a future code path that skips the engine-level check) would trigger the panic.

### Recommendation
Mirror the chainspec crate's validation in the proof-executor decoders: reject `elasticity == 0` together with a non-zero `denominator` (and vice versa) in both `decode_holocene_eip_1559_params_block_header` and `decode_jovian_eip_1559_params_block_header`, returning an `Eip1559ValidationError` instead of allowing a zero elasticity value to propagate into the base-fee division.

### Proof of Concept
1. Construct a header whose `extra_data` encodes Holocene/Jovian EIP-1559 params with `elasticity = 0` and `denominator != 0` (e.g. version byte + `denominator=8`, `elasticity=0`, as in the existing test helpers in [7](#0-6) , but with elasticity zeroed out).
2. Call `decode_holocene_eip_1559_params_block_header`/`decode_jovian_eip_1559_params_block_header` on this header — observe it returns `Ok(BaseFeeParams { elasticity_multiplier: 0, max_change_denominator: 8 })` instead of an error.
3. Feed the resulting `BaseFeeParams` through `StatelessL2Builder::next_block_base_fee`/`prepare_block_env` in [4](#0-3) , which calls `calc_next_block_base_fee(gas_used, gas_limit, base_fee, params)` — this panics on division by zero when computing the elasticity-scaled gas target, aborting the stateless executor/fault-proof program run for that block.

### Citations

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

**File:** crates/proof/executor/src/util.rs (L100-151)
```rust
#[cfg(all(test, feature = "test-utils"))]
mod test {
    use alloy_consensus::Header;
    use alloy_primitives::{B64, b64, bytes};
    use alloy_rpc_types_engine::PayloadAttributes;
    use base_common_genesis::{FeeConfig, RollupConfig};
    use base_common_rpc_types_engine::BasePayloadAttributes;

    use super::decode_holocene_eip_1559_params_block_header;
    use crate::util::{
        decode_jovian_eip_1559_params_block_header, encode_holocene_eip_1559_params,
    };

    fn mock_payload(eip_1559_params: Option<B64>) -> BasePayloadAttributes {
        BasePayloadAttributes {
            payload_attributes: PayloadAttributes {
                timestamp: 0,
                prev_randao: Default::default(),
                suggested_fee_recipient: Default::default(),
                withdrawals: Default::default(),
                parent_beacon_block_root: Default::default(),
                slot_number: None,
                target_gas_limit: None,
            },
            transactions: None,
            no_tx_pool: None,
            gas_limit: None,
            eip_1559_params,
            min_base_fee: None,
        }
    }

    #[test]
    fn test_decode_holocene_eip_1559_params() {
        let params = bytes!("00BEEFBABE0BADC0DE");
        let mock_header = Header { extra_data: params, ..Default::default() };
        let params = decode_holocene_eip_1559_params_block_header(&mock_header).unwrap();

        assert_eq!(params.elasticity_multiplier, 0x0BAD_C0DE);
        assert_eq!(params.max_change_denominator, 0xBEEF_BABE);
    }

    #[test]
    fn test_decode_jovian_eip_1559_params() {
        let params = bytes!("01BEEFBABE0BADC0DE00000000DEADBEEF");
        let mock_header = Header { extra_data: params, ..Default::default() };
        let (params, base_fee) = decode_jovian_eip_1559_params_block_header(&mock_header).unwrap();

        assert_eq!(params.elasticity_multiplier, 0x0BAD_C0DE);
        assert_eq!(params.max_change_denominator, 0xBEEF_BABE);
        assert_eq!(base_fee, 0xDEAD_BEEF);
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

**File:** crates/execution/chainspec/src/basefee.rs (L207-219)
```rust
    #[test]
    fn test_next_base_fee_jovian_rejects_zero_elasticity_with_nonzero_denominator() {
        let chain_spec = get_chainspec();
        let mut parent = chain_spec.genesis_header().clone();

        parent.extra_data =
            Bytes::copy_from_slice(&[1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]);

        assert_eq!(
            EIP1559ParamError::InvalidParams,
            compute_jovian_base_fee(chain_spec, &parent, JOVIAN_TIMESTAMP).unwrap_err()
        );
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

**File:** crates/execution/node/src/engine.rs (L312-332)
```rust
        if self
            .chain_spec()
            .is_holocene_active_at_timestamp(attributes.payload_attributes.timestamp)
        {
            let (elasticity, denominator) =
                attributes.decode_eip_1559_params().ok_or_else(|| {
                    EngineObjectValidationError::InvalidParams(
                        "MissingEip1559ParamsInPayloadAttributes".to_string().into(),
                    )
                })?;

            if elasticity != 0 && denominator == 0 {
                return Err(EngineObjectValidationError::InvalidParams(
                    "Eip1559ParamsDenominatorZero".to_string().into(),
                ));
            } else if denominator != 0 && elasticity == 0 {
                return Err(EngineObjectValidationError::InvalidParams(
                    "Eip1559ParamsElasticityZero".to_string().into(),
                ));
            }
        }
```

**File:** crates/consensus/engine/src/attributes.rs (L171-252)
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

        // The Jovian extra_data carries an additional `min_base_fee`. The `Option` doubles as
        // a fork signal: `Some` iff Jovian is active.
        let extra_data_decoded: Result<(u32, u32, Option<u64>), EIP1559ParamError> =
            if config.is_jovian_active(block.header.timestamp) {
                JovianExtraData::decode(&block.header.extra_data)
                    .map(|(be, bd, mbf)| (be, bd, Some(mbf)))
            } else if config.is_holocene_active(block.header.timestamp) {
                HoloceneExtraData::decode(&block.header.extra_data).map(|(be, bd)| (be, bd, None))
            } else {
                return AttributesMismatch::MissingBlockEIP1559.into();
            };

        let (be, bd, jovian_block_mbf): (u128, u128, Option<u64>) = match extra_data_decoded {
            Ok((be, bd, mbf)) => (be.into(), bd.into(), mbf),
            Err(EIP1559ParamError::NoEIP1559Params) => {
                error!(
                    "EIP1559 parameters for the block not set while holocene is active. This is a bug"
                );
                return AttributesMismatch::MissingBlockEIP1559.into();
            }
            Err(EIP1559ParamError::InvalidVersion(v)) => {
                error!(
                    version = v,
                    "The version in the extra data EIP1559 payload is incorrect. Should be 0. This is a bug",
                );
                return AttributesMismatch::InvalidExtraDataVersion.into();
            }
            Err(e) => {
                error!(err = ?e, "An unknown extra data decoding error occurred. This is a bug",);

                return AttributesMismatch::UnknownExtraDataDecodingError(e).into();
            }
        };

        if ae != be || ad != bd {
            return AttributesMismatch::EIP1559Parameters(
                BaseFeeParams { max_change_denominator: ad, elasticity_multiplier: ae },
                BaseFeeParams { max_change_denominator: bd, elasticity_multiplier: be },
            )
            .into();
        }
```
