### Title
Missing Value-Range Validation on Operator-Fee `ConfigUpdate` Decoding Allows Unbounded L2 Fee Extraction - (File: `crates/common/genesis/src/updates/operator_fee.rs`)

### Summary
The derivation-pipeline decoder for `ConfigUpdate(OperatorFee)` L1 logs, `OperatorFeeUpdate::try_from`, only validates the ABI encoding shape of the log payload (pointer/length checks via `UpdateDataValidator`) and never bounds the decoded `operator_fee_scalar` (`u32`) or `operator_fee_constant` (`u64`) values before they are committed into `SystemConfig` and subsequently propagated to every L2 block's L1-attributes deposit transaction.

### Finding Description
`OperatorFeeUpdate::try_from` decodes the raw log payload straight into `operator_fee_scalar`/`operator_fee_constant` with no upper-bound sanity check, then `OperatorFeeUpdate::apply` writes them unconditionally into `SystemConfig`: [1](#0-0) [2](#0-1) 

This is inconsistent with the sibling `GasConfigUpdate` decoder, which at least rejects malformed/out-of-range scalar encodings via `check_ecotone_l1_system_config_scalar` before accepting the update: [3](#0-2) 

No equivalent guard exists for the operator-fee scalar/constant pair. Once committed to `SystemConfig`, these values flow through the `StatefulAttributesBuilder` into every subsequent L2 block's L1 info deposit transaction (as shown by the harness/test helper that encodes exactly this update type): [4](#0-3) 

From there they are read out of the `L1_BLOCK_INFO` predeploy storage on L2 and used directly in the per-transaction operator-fee charge: [5](#0-4) [6](#0-5) 

Since `operator_fee_constant` is a flat `u64` additive term charged on every L2 transaction (independent of the sender's signed `max_fee_per_gas`), there is no protocol-level ceiling preventing the L1 `SystemConfig` owner from pushing this constant (or the scalar) to near-`u64::MAX`/`u32::MAX`, causing every L2 transaction to be charged an enormous, effectively arbitrary fee the moment the update is derived — mirroring the audited bug class of fee setters lacking value-range checks that let a privileged party front-run/extract funds from users who cannot anticipate or bound the change.

### Impact Explanation
This falls squarely in the allowed "derivation of attacker-written L1 deposit and system-config data" path: the L2 derivation pipeline treats the L1-emitted `ConfigUpdate(OperatorFee)` log as trusted input and applies it with no sanity bound. An out-of-range update silently becomes chain-consensus state for every node deriving L2 blocks, imposing unauthorized, unbounded fee extraction on every L2 transaction sender from the moment the epoch containing the update is processed, until a corrective update is posted. This can drain user funds beyond what senders authorized in their signed transactions and, depending on gas/fee-cap validation ordering, can also make transactions unpayable/rejected chain-wide, effectively halting normal L2 operation for all senders.

### Likelihood Explanation
Likelihood depends on the L1 `SystemConfig` owner key being misused or compromised, but the point of this class of finding (per the referenced report) is precisely that the value has no independent, protocol-enforced ceiling — any legitimate-looking update, including an operator error or a front-run change, is accepted verbatim by every node with no defense-in-depth range check, unlike the adjacent gas-config path which does validate its scalar.

### Recommendation
Add explicit range validation to `OperatorFeeUpdate::try_from`/`apply` (e.g., cap `operator_fee_scalar` to a sane maximum fraction and `operator_fee_constant` to a maximum absolute wei value, consistent with the OP-stack spec's intended bounds), mirroring the existing `check_ecotone_l1_system_config_scalar` pattern used for gas-config scalars, and reject/ignore out-of-range updates rather than applying them unconditionally.

### Proof of Concept
1. On L1, emit a `ConfigUpdate(OperatorFee)` log (as constructed by `enqueue_operator_fee_update`) with `operator_fee_scalar = u32::MAX` and `operator_fee_constant = u64::MAX`. [7](#0-6) 
2. The derivation pipeline decodes and applies this update with no range check: [8](#0-7) 
3. On the next epoch change, `StatefulAttributesBuilder` bakes these values into the L1 info deposit transaction for all subsequent L2 blocks (confirmed by the propagation test flow), and every L2 transaction thereafter is charged the resulting operator fee via `L1BlockInfo`/`L1FeeParams::operator_fee_charge`, with no cap enforced anywhere in the chain. [5](#0-4)

### Citations

**File:** crates/common/genesis/src/updates/operator_fee.rs (L19-66)
```rust
impl OperatorFeeUpdate {
    /// Applies the update to the [`SystemConfig`].
    pub const fn apply(&self, config: &mut SystemConfig) {
        config.operator_fee_scalar = Some(self.operator_fee_scalar);
        config.operator_fee_constant = Some(self.operator_fee_constant);
    }
}

impl TryFrom<&SystemConfigLog> for OperatorFeeUpdate {
    type Error = OperatorFeeUpdateError;

    fn try_from(log: &SystemConfigLog) -> Result<Self, Self::Error> {
        let LogData { data, .. } = &log.log.data;

        let validated = UpdateDataValidator::validate(data).map_err(|e| match e {
            ValidationError::InvalidDataLen(_expected, actual) => {
                OperatorFeeUpdateError::InvalidDataLen(actual)
            }
            ValidationError::PointerDecodingError => OperatorFeeUpdateError::PointerDecodingError,
            ValidationError::InvalidDataPointer(pointer) => {
                OperatorFeeUpdateError::InvalidDataPointer(pointer)
            }
            ValidationError::LengthDecodingError => OperatorFeeUpdateError::LengthDecodingError,
            ValidationError::InvalidDataLength(length) => {
                OperatorFeeUpdateError::InvalidDataLength(length)
            }
        })?;

        // The operator fee scalar and constant are
        // packed into a single u256 as follows:
        //
        // | Bytes    | Actual Size | Variable |
        // |----------|-------------|----------|
        // | 0 .. 24  | uint32      | scalar   |
        // | 24 .. 32 | uint64      | constant |
        // |----------|-------------|----------|

        let payload = validated.payload();
        let mut be_bytes = [0u8; 4];
        be_bytes[0..4].copy_from_slice(&payload[20..24]);
        let operator_fee_scalar = u32::from_be_bytes(be_bytes);

        let mut be_bytes = [0u8; 8];
        be_bytes[0..8].copy_from_slice(&payload[24..32]);
        let operator_fee_constant = u64::from_be_bytes(be_bytes);

        Ok(Self { operator_fee_scalar, operator_fee_constant })
    }
```

**File:** crates/common/genesis/src/updates/gas_config.rs (L60-65)
```rust
        if sys_log.ecotone_active
            && RollupConfig::check_ecotone_l1_system_config_scalar(scalar.to_be_bytes()).is_err()
        {
            // ignore invalid scalars, retain the old system-config scalar
            return Ok(Self::default());
        }
```

**File:** actions/harness/src/l1/miner.rs (L373-400)
```rust
    /// Queue a `ConfigUpdate(OperatorFee)` log for the next mined block.
    ///
    /// Encodes an operator fee update with the given `scalar` and `constant`.
    pub fn enqueue_operator_fee_update(
        &mut self,
        l1_sys_cfg_addr: Address,
        operator_fee_scalar: u32,
        operator_fee_constant: u64,
    ) {
        let mut data = [0u8; 96];
        data[31] = 0x20; // pointer = 32
        data[63] = 0x20; // length  = 32
        data[84..88].copy_from_slice(&operator_fee_scalar.to_be_bytes());
        data[88..96].copy_from_slice(&operator_fee_constant.to_be_bytes());
        let mut update_type = [0u8; 32];
        update_type[31] = 5; // OperatorFee = 5
        self.enqueue_log(Log {
            address: l1_sys_cfg_addr,
            data: LogData::new_unchecked(
                vec![
                    SystemConfigUpdate::TOPIC,
                    SystemConfigUpdate::EVENT_VERSION_0,
                    B256::from(update_type),
                ],
                data.into(),
            ),
        });
    }
```

**File:** crates/common/evm/src/l1block.rs (L117-140)
```rust
    /// Try to fetch the L1 block info from the database, post-Isthmus.
    fn try_fetch_isthmus<DB: Database>(&mut self, db: &mut DB) -> Result<(), DB::Error> {
        // Post-isthmus L1 block info
        let operator_fee_scalars = db
            .storage(Predeploys::L1_BLOCK_INFO, Self::OPERATOR_FEE_SCALARS_SLOT)?
            .to_be_bytes::<32>();

        // The `operator_fee_scalar` is stored as a big endian u32 at
        // OPERATOR_FEE_SCALAR_OFFSET.
        self.operator_fee_scalar = Some(U256::from_be_slice(
            operator_fee_scalars
                [Self::OPERATOR_FEE_SCALAR_OFFSET..Self::OPERATOR_FEE_SCALAR_OFFSET + 4]
                .as_ref(),
        ));
        // The `operator_fee_constant` is stored as a big endian u64 at
        // OPERATOR_FEE_CONSTANT_OFFSET.
        self.operator_fee_constant = Some(U256::from_be_slice(
            operator_fee_scalars
                [Self::OPERATOR_FEE_CONSTANT_OFFSET..Self::OPERATOR_FEE_CONSTANT_OFFSET + 8]
                .as_ref(),
        ));

        Ok(())
    }
```

**File:** crates/common/l1-fees/src/params.rs (L40-48)
```rust
    /// The operator fee scalar. `None` before Isthmus.
    pub operator_fee_scalar: Option<U256>,
    /// The operator fee constant. `None` before Isthmus.
    pub operator_fee_constant: Option<U256>,
    /// True if Ecotone is activated but the L1 fee scalars have not yet been set.
    pub empty_ecotone_scalars: bool,
    /// The Jovian DA-footprint gas scalar. `None` before Jovian (or when unset).
    pub da_footprint_gas_scalar: Option<U256>,
}
```
