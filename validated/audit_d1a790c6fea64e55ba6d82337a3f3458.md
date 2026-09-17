## Finding

### Title
Unbounded operator fee scalar/constant allows a malicious SystemConfig owner to drain user funds via the operator fee - (File: crates/common/genesis/src/updates/operator_fee.rs)

### Summary
The reported Teller bug is that an admin-controlled fee setter (`setProtocolFee`) has no upper bound, letting a malicious/compromised admin extract nearly all of a user's funds on every transaction. Base has a directly analogous, in-scope mechanism: the L1 `SystemConfig` contract's owner can push an `OperatorFee` `ConfigUpdate` (scalar + constant) that the node derivation pipeline decodes and applies to every L2 transaction with **no value-range validation**, and this value is charged to every unsuspecting L2 transaction sender.

### Finding Description
The `OperatorFeeUpdate::try_from` decoder only validates the ABI *structure* of the log (pointer/length), never the numeric range of the decoded `operator_fee_scalar` (u32) or `operator_fee_constant` (u64): [1](#0-0) 

The decoded values are applied verbatim to `SystemConfig` with no min/max clamp: [2](#0-1) 

These `SystemConfig` fields flow, unmodified, into `L1FeeParams`/`L1BlockInfo` and are used directly to compute the per-transaction operator fee that every L2 sender must pay: [3](#0-2) 

The resulting fee is charged upfront from the sender's balance and credited to `OPERATOR_FEE_VAULT` during EVM handling, on every non-deposit transaction: [4](#0-3) [5](#0-4) 

The txpool admission path independently mirrors this same unbounded-fee computation (`l1_block_info.tx_cost`) to gate transaction inclusion, so a maliciously high scalar/constant also affects mempool admission economics: [6](#0-5) 

Nothing in this pipeline enforces a ceiling on `operator_fee_scalar` or `operator_fee_constant` (unlike the Ecotone L1-fee scalar path, which has `RollupConfig::check_ecotone_l1_system_config_scalar` validation before being applied — see `crates/common/genesis/src/updates/gas_config.rs`). The Jovian formula (`gas * scalar * 100`) has no saturation guard other than `saturating_mul`, meaning the L1 `SystemConfig` owner can set `operator_fee_scalar` to `u32::MAX` (or `operator_fee_constant` to `u64::MAX`), making every L2 transaction's operator fee astronomically large — up to `U256::MAX` after saturation — with the fee taken from each sender's balance on every transaction.

### Impact Explanation
This is a direct funds-theft vector reachable purely through data the L1 `SystemConfig` owner writes and that Base's derivation pipeline consumes without validation — squarely within the in-scope "derivation of attacker-written L1 deposit and system-config data" path. A malicious or compromised `SystemConfig` owner (the L1-side "admin" analogous to Teller's protocol admin) can, with a single L1 transaction, cause every L2 user transaction to be charged an unbounded operator fee taken from the sender's balance and routed to `OPERATOR_FEE_VAULT`, effectively stealing funds from every unsuspecting user until the update is reversed. There is no timelock, no min/max clamp, and no consensus-level sanity check comparable to the Ecotone scalar validator.

### Likelihood Explanation
Requires control of the `SystemConfig` contract owner key on L1 (a privileged but plausible-to-compromise role, exactly analogous to the "malicious admin" premise accepted in the original report). Once that key is available, the attack is a single `setOperatorFeeScalars`-style L1 transaction; no additional conditions are needed and it affects all subsequent L2 transactions immediately upon epoch advance.

### Recommendation
Add bounds validation for `operator_fee_scalar` and `operator_fee_constant` analogous to the existing Ecotone scalar check (`RollupConfig::check_ecotone_l1_system_config_scalar`) inside `OperatorFeeUpdate::try_from` / `GasConfigUpdate::try_from`, rejecting or clamping values above a sane maximum before they are applied to `SystemConfig`. Consider also enforcing a protocol-level maximum operator fee (e.g., a hard cap checked in `operator_fee_charge_inner`) so that even an unvalidated `SystemConfig` value cannot impose unbounded costs on L2 senders.

### Proof of Concept
1. As the `SystemConfig` owner on L1, emit a `ConfigUpdate(OperatorFee)` log with `operator_fee_scalar = u32::MAX` and `operator_fee_constant = u64::MAX` (encoding per `enqueue_operator_fee_update` in `actions/harness/src/l1/miner.rs:376-400`).
2. The derivation pipeline decodes this via `OperatorFeeUpdate::try_from` (`crates/common/genesis/src/updates/operator_fee.rs:27-65`), which only checks ABI structure, not value bounds, and applies it to `SystemConfig`.
3. Once the L2 epoch advances to include this update, `L1BlockInfo`/`L1FeeParams` on every subsequent L2 block reflect the malicious scalar/constant (`crates/common/evm/src/l1block.rs:117-140`).
4. Every non-deposit L2 transaction now pays `operator_fee_charge_inner` computed with these values (`crates/common/l1-fees/src/params.rs:184-199`), charged upfront from the sender and credited to `OPERATOR_FEE_VAULT` (`crates/common/evm/src/handler.rs:286-306`), draining user balances far beyond normal gas costs.

### Citations

**File:** crates/common/genesis/src/updates/operator_fee.rs (L19-24)
```rust
impl OperatorFeeUpdate {
    /// Applies the update to the [`SystemConfig`].
    pub const fn apply(&self, config: &mut SystemConfig) {
        config.operator_fee_scalar = Some(self.operator_fee_scalar);
        config.operator_fee_constant = Some(self.operator_fee_constant);
    }
```

**File:** crates/common/genesis/src/updates/operator_fee.rs (L27-65)
```rust
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
```

**File:** crates/common/l1-fees/src/params.rs (L184-199)
```rust
    /// Calculates the operator fee for a given `gas` amount.
    ///
    /// Missing scalars fall back to zero to match the execution path during the
    /// txpool bootstrap window before the first L1 attributes deposit.
    pub fn operator_fee_charge_inner(&self, gas: U256, upgrade: BaseUpgrade) -> U256 {
        let operator_fee_scalar = self.operator_fee_scalar.unwrap_or_default();
        let operator_fee_constant = self.operator_fee_constant.unwrap_or_default();

        let product = if Self::is_enabled(upgrade, BaseUpgrade::Jovian) {
            gas.saturating_mul(operator_fee_scalar)
                .saturating_mul(U256::from(OPERATOR_FEE_JOVIAN_MULTIPLIER))
        } else {
            gas.saturating_mul(operator_fee_scalar) / U256::from(OPERATOR_FEE_SCALAR_DECIMAL)
        };
        product.saturating_add(operator_fee_constant)
    }
```

**File:** crates/common/evm/src/handler.rs (L286-306)
```rust

        let l1_cost = l1_block_info.calculate_tx_l1_cost(enveloped_tx, spec);
        let operator_fee_cost = if spec.is_enabled_in(BaseUpgrade::Isthmus) {
            l1_block_info.operator_fee_charge(
                enveloped_tx,
                U256::from(frame_result.gas().used()),
                spec,
            )
        } else {
            U256::ZERO
        };
        let base_fee_amount = U256::from(basefee.saturating_mul(frame_result.gas().used() as u128));

        // Send fees to their respective recipients
        for (recipient, amount) in [
            (Predeploys::L1_FEE_VAULT, l1_cost),
            (Predeploys::BASE_FEE_VAULT, base_fee_amount),
            (Predeploys::OPERATOR_FEE_VAULT, operator_fee_cost),
        ] {
            ctx.journal_mut().balance_incr(recipient, amount)?;
        }
```

**File:** crates/common/evm2/src/handler.rs (L108-132)
```rust
impl TxHandlerHooks<BaseEvmTypes> for BaseTxHandlerHooks {
    fn before_execution(
        host: &mut Evm<'_, BaseEvmTypes>,
        envelope: &BaseTxEnvelope,
        caller: Address,
        upfront_fee: U256,
    ) -> HandlerResult<()> {
        // Charge the upfront gas cost, the L1 data fee, and the operator fee (on the gas limit)
        // from the caller. Both fees are stashed for settlement so it charges/refunds against the
        // exact amounts collected here, without recomputing them.
        let l1_fee = Self::l1_fee(host, envelope);
        let operator_fee = Self::operator_fee(host, envelope, envelope.gas_limit());
        let ext = host.ext_mut();
        ext.l1_fee = l1_fee;
        ext.operator_fee = operator_fee;
        // `validate_sender` only checks the gas cost and value; the L1 and operator fees are
        // charged on top here via `charge_upfront`'s wrapping subtraction, so reject an
        // underfunded caller up front rather than let its balance wrap to a spurious value.
        Self::ensure_can_pay_fees(host, envelope, caller, l1_fee, operator_fee)?;
        charge_upfront(
            host,
            caller,
            upfront_fee.saturating_add(l1_fee).saturating_add(operator_fee),
        )
    }
```

**File:** crates/execution/txpool/src/validator.rs (L2093-2105)
```rust
            let encoded = valid_tx.transaction().encoded_2718();

            // Must mirror the execution-side cost in `BaseHandler` (L1 data fee + operator fee
            // post-Isthmus); otherwise operator-fee-underfunded txs get admitted but never execute.
            let spec_id = BaseSpecId::from_timestamp(self.chain_spec(), self.block_timestamp());
            let cost_addition = l1_block_info.tx_cost(
                &encoded,
                U256::from(
                    valid_tx.transaction().gas_limit().saturating_add(operator_fee_gas_addition),
                ),
                spec_id,
            );
            let cost = valid_tx.transaction().cost().saturating_add(cost_addition);
```
