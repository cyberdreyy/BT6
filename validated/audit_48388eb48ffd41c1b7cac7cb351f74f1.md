## Confirmed

`TxEip8130` fields committed to by both the sender and payer signatures are `chain_id, sender, nonce_key, nonce_sequence, valid_after, valid_before, max_priority_fee_per_gas, max_fee_per_gas, gas_limit, account_changes, calls, metadata, payer` [1](#0-0) . There is no field bounding the L1 data fee or the Isthmus operator fee.

### Title
Sponsored EIP-8130 payer's signed fee ceiling excludes the L1 data fee and operator fee, letting a chain-config change extract additional value from a pre-signed sponsorship without the payer's consent - (File: crates/execution/eip8130/src/fee.rs, crates/common/evm/src/eip8130.rs)

### Summary
An EIP-8130 sponsor (`payer`) signs `payer_signature_hash`, which commits only to `gas_limit` and `max_fee_per_gas`/`max_priority_fee_per_gas` [2](#0-1) . The pre-admission balance check (`FeeCheck::validate_balance`) enforces only `(gas_limit + payer_auth_cost) * max_fee_per_gas` [3](#0-2) . At actual execution, `prepay()` additionally and unconditionally debits the L1 data fee and the operator fee, computed from the block's live `L1FeeParams`/`L1BlockInfo` (itself derived from `SystemConfig`), with no cap tied to anything the payer signed [4](#0-3) .

This mirrors the SAM report's root cause: a party (there, the artist adjusting `artistFee`/`affiliateFee`; here, whoever can raise the effective L1/operator fee via a `SystemConfig` update) can change a fee input between when a counterparty commits (buys, or here signs a sponsorship) and when it is settled, extracting more value than the counterparty authorized, with no revert-on-deviation protection.

### Finding Description
- `L1FeeParams`/operator-fee scalars are populated per block from `SystemConfig`, which is itself derived from attacker/owner-written L1 `ConfigUpdate` logs processed by `GasConfigUpdate`/`OperatorFeeUpdate` and folded into the L1 info deposit at each block [5](#0-4) [6](#0-5) .
- The EIP-8130 `prepay()` step charges the payer `gas_charge + l1_cost + operator_cost` where `l1_cost`/`operator_cost` are computed from the block's *current* `L1FeeParams` at inclusion time, not from any value fixed in the payer's signature [7](#0-6) .
- The only pre-flight balance/fee validation the payer's authorization is checked against (`FeeCheck::validate_balance`/`validate_fees`) covers gas price only, never these two components [8](#0-7) .
- Because a sponsored transaction may be pre-signed well before inclusion (the payer signs a digest over the whole tx body once, and the tx can sit in the pool or be delayed), an L1/operator fee scalar increase committed between the payer's signature and the block that includes the transaction changes the actual amount debited from the payer, with the excess routed to `L1_FEE_VAULT`/`OPERATOR_FEE_VAULT` [9](#0-8) , and there is no revert path analogous to the SAM fix (revert on unexpected fee deviation) — the transaction simply proceeds and the payer pays whatever the live config computes, or fails outright with a generic insufficient-balance error if it exceeds their balance [10](#0-9) .

### Impact Explanation
A sponsor authorizing a payer signature effectively grants an unbounded, mutable liability for the L1 data fee and operator fee on top of the gas price cap they explicitly signed. Whoever controls the L1 `SystemConfig` (chain-governance owner) can raise `operator_fee_scalar`/`operator_fee_constant`/`l1_base_fee_scalar` shortly before a known sponsored transaction is included, extracting additional ETH from the payer beyond what their signature authorized — functionally equivalent to the SAM artist raising `artistFee`/`affiliateFee` right before a pending `buy()`. This is an unauthorized-fee-extraction / theft-of-funds pattern against a signed authorization, reachable purely through ordinary EIP-8130 transaction sponsorship plus a legitimate system-config update path, not through any sequencer/builder misbehavior.

### Likelihood Explanation
Requires (a) a sponsor pre-authorizing a payer signature that is not immediately included (normal usage pattern for gasless/sponsored UX), and (b) a `SystemConfig` fee-parameter change landing on L1 and propagating through derivation before the sponsored tx lands — both of which are ordinary, expected occurrences (fee parameters are adjusted periodically), making the exposure realistic without requiring any privileged-attacker assumption beyond the existing SystemConfig-owner trust model.

### Recommendation
Bind the payer's signed digest (or add an explicit, signed cap field to `TxEip8130`) to a maximum L1-fee/operator-fee amount (or a maximum total-fee ceiling inclusive of L1+operator fee), and have `prepay()`/`FeeCheck` revert the transaction if the computed `l1_cost + operator_cost` at inclusion time exceeds that signed ceiling, rather than silently charging whatever the live `SystemConfig`-derived parameters yield.

### Proof of Concept
Not independently executable from the index alone; the mechanism is demonstrated by the code path: `payer_signature_hash` covers only `gas_limit`/`max_fee_per_gas` fields [2](#0-1) , `FeeCheck::validate_balance` checks only that same amount [3](#0-2) , while `prepay()` additionally debits `l1_cost`/`operator_cost` computed from the block's live fee params [4](#0-3)  — a `GasConfigUpdate`/`OperatorFeeUpdate` raising those scalars between signature time and inclusion (test harness has `enqueue_gas_config_update`/`enqueue_operator_fee_update` primitives already used in the derivation test suite [11](#0-10) [12](#0-11) ) increases the debit against a payer signature that never bounded these components.

### Citations

**File:** crates/common/consensus/src/transaction/eip8130/tx.rs (L46-89)
```rust
pub struct TxEip8130 {
    /// EIP-155 chain ID this transaction is bound to.
    pub chain_id: ChainId,
    /// Explicit sender account address, or `None` for the EOA path.
    pub sender: Option<Address>,
    /// High 192 bits of the compound nonce; with `nonce_sequence` forms the
    /// per-account replay protection key.
    pub nonce_key: U256,
    /// Sequence number within the nonce key.
    pub nonce_sequence: u64,
    /// Lower bound of the validity window: a Unix timestamp in **milliseconds**.
    /// The transaction is invalid when `block.timestamp * 1000 < valid_after`;
    /// `0` means no lower bound.
    pub valid_after: u64,
    /// Upper bound of the validity window: a Unix timestamp in **milliseconds**.
    /// The bound is inclusive — the transaction is still valid at
    /// `block.timestamp * 1000 == valid_before` and invalid only once
    /// `block.timestamp * 1000 > valid_before`; `0` means no expiry. It MUST be
    /// non-zero for nonce-free (`nonce_key == NONCE_KEY_MAX`) transactions, which
    /// additionally require `valid_before` to be strictly in the future when the
    /// nonce is recorded: the nonce-manager replay ring's admission window is
    /// `(now, now + NONCE_FREE_MAX_EXPIRY_WINDOW]`, so a nonce-free transaction
    /// cannot actually be included exactly at `valid_before`.
    pub valid_before: u64,
    /// Max priority fee per gas (tip) the sender is willing to pay.
    #[cfg_attr(feature = "serde", serde(with = "alloy_serde::quantity"))]
    pub max_priority_fee_per_gas: u128,
    /// Max total fee per gas (base + tip cap) the sender is willing to pay.
    #[cfg_attr(feature = "serde", serde(with = "alloy_serde::quantity"))]
    pub max_fee_per_gas: u128,
    /// Gas limit for the entire AA transaction execution.
    pub gas_limit: u64,
    /// Account-mutation entries applied before calls execute.
    pub account_changes: Vec<AccountChange>,
    /// Calls dispatched by the protocol after account changes apply, grouped
    /// into phases (`Vec<Vec<Call>>`).
    pub calls: Vec<Vec<Call>>,
    /// Opaque attribution/annotation bytes; empty when unused. Carried in the
    /// wire body between `calls` and `payer` and committed to by both the
    /// sender and payer signatures, but otherwise uninterpreted by the protocol.
    pub metadata: Bytes,
    /// Optional explicit payer; `None` means the resolved sender pays gas.
    pub payer: Option<Address>,
}
```

**File:** crates/common/consensus/src/transaction/eip8130/tx.rs (L346-363)
```rust
    /// Signing-hash preimage for the payer, per [EIP-8130].
    ///
    /// `keccak256(EIP8130_PAYER_TYPE || rlp([all body fields through `payer`]))`
    /// with the `sender` slot replaced by the recovered sender address. The
    /// payer commits to the full transaction body — including the `payer` slot
    /// itself — and only the `sender_auth` / `payer_auth` slots (which live in
    /// the signed wrapper) are excluded.
    ///
    /// [EIP-8130]: https://eips.ethereum.org/EIPS/eip-8130
    pub fn payer_signature_hash(&self, resolved_sender: Address) -> B256 {
        let sender = Some(resolved_sender);
        let payload_length = self.rlp_encoded_fields_length_with_sender(&sender);
        let mut buf = Vec::with_capacity(1 + length_of_length(payload_length) + payload_length);
        buf.put_u8(Eip8130Constants::EIP8130_PAYER_TYPE);
        Header { list: true, payload_length }.encode(&mut buf);
        self.rlp_encode_fields_with_sender(&sender, &mut buf);
        keccak256(&buf)
    }
```

**File:** crates/execution/eip8130/src/fee.rs (L74-111)
```rust
    /// Validates the EIP-1559 fee caps against the block base fee.
    ///
    /// # Errors
    /// - [`FeeError::TipAboveFeeCap`] — the priority fee cap exceeds the total cap.
    /// - [`FeeError::FeeCapBelowBaseFee`] — the total cap is below the base fee.
    #[must_use = "discarding the result silently skips the fee-cap check"]
    pub const fn validate_fees(
        max_fee: u128,
        max_priority_fee: u128,
        base_fee: u128,
    ) -> Result<(), FeeError> {
        if max_priority_fee > max_fee {
            return Err(FeeError::TipAboveFeeCap { tip: max_priority_fee, max_fee });
        }
        if max_fee < base_fee {
            return Err(FeeError::FeeCapBelowBaseFee { base_fee, max_fee });
        }
        Ok(())
    }

    /// Validates that the gas payer (a sponsor, or the sender for self-pay) can
    /// cover the worst-case gas charge at `max_fee_per_gas`.
    ///
    /// # Errors
    /// - [`FeeError::InsufficientBalance`] — the balance is below the maximum charge.
    #[must_use = "discarding the result silently skips the balance check"]
    pub fn validate_balance(
        balance: U256,
        gas_limit: u64,
        payer_auth_cost: u64,
        max_fee: u128,
    ) -> Result<(), FeeError> {
        let required = Self::max_fee_charge(gas_limit, payer_auth_cost, max_fee);
        if balance < required {
            return Err(FeeError::InsufficientBalance { balance, required });
        }
        Ok(())
    }
```

**File:** crates/common/evm/src/eip8130.rs (L1151-1173)
```rust
        // Worst-case chargeable gas: the full sender budget plus payer
        // authentication, both billed at the effective price.
        let max_gas = FeeCheck::max_chargeable_gas(outcome.gas_limit, outcome.payer_auth);
        let gas_charge = U256::from(max_gas)
            .checked_mul(U256::from(outcome.effective))
            .ok_or_else(|| BaseTransactionError::eip8130("EIP-8130 gas pre-charge overflow"))?;
        let l1_cost = ctx.chain_mut().calculate_tx_l1_cost(encoded, spec);
        let operator_cost = ctx.chain().operator_fee_charge(encoded, U256::from(max_gas), spec);
        let prepay = gas_charge
            .checked_add(l1_cost)
            .and_then(|v| v.checked_add(operator_cost))
            .ok_or_else(|| BaseTransactionError::eip8130("EIP-8130 pre-charge overflow"))?;

        let mut payer_acc =
            ctx.journal_mut().load_account_mut(outcome.payer).map_err(EVMError::Database)?;
        let debited = payer_acc.balance().checked_sub(prepay).ok_or_else(|| {
            EVMError::Transaction(BaseTransactionError::eip8130(
                "payer balance is below the worst-case fee",
            ))
        })?;
        payer_acc.set_balance(debited);

        Ok(prepay)
```

**File:** crates/common/genesis/src/updates/gas_config.rs (L18-27)
```rust
impl GasConfigUpdate {
    /// Applies the update to the [`SystemConfig`].
    pub const fn apply(&self, config: &mut SystemConfig) {
        if let Some(scalar) = self.scalar {
            config.scalar = scalar;
        }
        if let Some(overhead) = self.overhead {
            config.overhead = overhead;
        }
    }
```

**File:** crates/consensus/protocol/src/utils.rs (L114-141)
```rust
    let mut cfg = SystemConfig {
        batcher_address: l1_info.batcher_address(),
        overhead: l1_info.l1_fee_overhead(),
        scalar: l1_fee_scalar,
        gas_limit,
        ..Default::default()
    };

    // After holocene's activation, the EIP-1559 parameters are stored in the block header's nonce.
    if rollup_config.is_jovian_active(timestamp) {
        let (elasticity, denominator, min_base_fee) = JovianExtraData::decode(extra_data)?;
        cfg.eip1559_denominator = Some(denominator);
        cfg.eip1559_elasticity = Some(elasticity);
        cfg.min_base_fee = Some(min_base_fee);
    } else if rollup_config.is_holocene_active(timestamp) {
        let (elasticity, denominator) = HoloceneExtraData::decode(extra_data)?;
        cfg.eip1559_denominator = Some(denominator);
        cfg.eip1559_elasticity = Some(elasticity);
    }

    if rollup_config.is_isthmus_active(timestamp) {
        cfg.operator_fee_scalar = Some(l1_info.operator_fee_scalar());
        cfg.operator_fee_constant = Some(l1_info.operator_fee_constant());
    }

    if let Some(da_footprint) = l1_info.da_footprint() {
        cfg.da_footprint_gas_scalar = Some(da_footprint);
    }
```

**File:** crates/common/evm2/src/handler.rs (L156-163)
```rust
        // Distribute the collected OP-stack fees to their vaults.
        Self::credit(host, Predeploys::L1_FEE_VAULT, l1_fee)?;
        Self::credit(
            host,
            Predeploys::BASE_FEE_VAULT,
            basefee.saturating_mul(U256::from(gas_used)),
        )?;
        Self::credit(host, Predeploys::OPERATOR_FEE_VAULT, operator_fee_used)?;
```

**File:** actions/harness/src/l1/miner.rs (L324-350)
```rust
    /// Encodes a GPO `overhead` and `scalar` update. The derivation pipeline
    /// applies this through the `SystemConfig` update mechanism.
    pub fn enqueue_gas_config_update(
        &mut self,
        l1_sys_cfg_addr: Address,
        overhead: u64,
        scalar: u64,
    ) {
        let mut data = [0u8; 128];
        data[31] = 0x20; // pointer = 32
        data[63] = 0x40; // length  = 64
        data[88..96].copy_from_slice(&overhead.to_be_bytes());
        data[120..128].copy_from_slice(&scalar.to_be_bytes());
        let mut update_type = [0u8; 32];
        update_type[31] = 1; // GasConfig = 1
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

**File:** actions/harness/tests/upgrade/operator_fees.rs (L578-602)
```rust
#[tokio::test]
async fn operator_fee_config_update_propagates_to_l1_info() {
    const OLD_SCALAR: u32 = 1_000;
    const OLD_CONSTANT: u64 = 500;
    const NEW_SCALAR: u32 = 3_000;
    const NEW_CONSTANT: u64 = 700;

    let l1_sys_cfg_addr = Address::repeat_byte(0xCC);
    let batcher_cfg = BatcherConfig::default();
    let mut rollup_cfg =
        TestRollupConfigBuilder::base_mainnet(&batcher_cfg).through_isthmus().build();
    rollup_cfg.l1_system_config_address = l1_sys_cfg_addr;
    let sys_cfg = rollup_cfg.genesis.system_config.as_mut().unwrap();
    sys_cfg.operator_fee_scalar = Some(OLD_SCALAR);
    sys_cfg.operator_fee_constant = Some(OLD_CONSTANT);

    // Standard L1 block_time (12 s). With L2 block_time=2 s, six L2 blocks
    // fit in each L1 epoch. The epoch change from 0 to 1 occurs at L2 block 6
    // (ts=12), when ts(L1 block 1)=12 ≤ ts(L2 block 6)=12.
    let mut h = ActionTestHarness::new(L1MinerConfig::default(), rollup_cfg);

    // Pre-mine L1 block 1 (ts=12) with the OperatorFee update so the sequencer
    // can reference epoch 1 when it builds L2 block 6.
    h.l1.enqueue_operator_fee_update(l1_sys_cfg_addr, NEW_SCALAR, NEW_CONSTANT);
    h.l1.mine_block(); // L1 block 1, ts=12
```
