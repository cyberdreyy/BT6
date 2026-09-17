### Title
Chain operator can force an unrecoverable network-upgrade-block halt by lowering the L2 gas limit — no minimum-gas-limit floor on `GasLimit` SystemConfig updates - (File: `crates/common/genesis/src/updates/gas_limit.rs`)

### Summary
`GasLimitUpdate::apply` writes an L1-operator-controlled gas limit straight into `SystemConfig.gas_limit` with no lower-bound validation, and the attributes builder (`StatefulAttributesBuilder::prepare_payload_attributes`) unconditionally packs the L1-info deposit, all pending user deposits, and every pending network-upgrade deposit transaction into the block without checking that their combined gas fits under that limit. Because network-upgrade transactions are deposits, the existing Holocene "deposit-only retry" safety net cannot rescue an over-gas upgrade block, since it is already deposit-only. This reproduces the reported OP Stack "chain operator can DOS entire cluster during upgrade block" bug class inside this repo's Rust node/fault-proof implementation.

### Finding Description
`GasLimitUpdate::apply` simply overwrites the gas limit with no floor check: [1](#0-0) 

The corresponding decode path only validates ABI encoding shape, never the numeric value against any minimum: [2](#0-1) 

`SystemConfigUpdate::apply` dispatches directly into this unchecked setter for every `GasLimit`-kind log derived from L1: [3](#0-2) 

At upgrade activation, `StatefulAttributesBuilder::prepare_payload_attributes` builds the block's transaction list as `[l1_info_tx, base_time_tx?, ...deposit_transactions, ...upgrade_transactions]` with no check that the sum of gas across all of these (L1 info tx, all pending user deposits, and all network-upgrade deposits) is less than `sys_config.gas_limit`: [4](#0-3) 

The L1 info deposit alone reserves 150,000,000 gas pre-Regolith or a fixed `REGOLITH_SYSTEM_TX_GAS` post-Regolith: [5](#0-4) 

and upgrade transactions such as Isthmus/Jovian each carry their own hard-coded gas (e.g. deploy txs of ~425k–1.75M gas per upgrade): [6](#0-5) [7](#0-6) 

When the resulting block's cumulative deposit gas exceeds the L2 gas limit, execution rejects the transaction with `TransactionGasLimitMoreThanAvailableBlockGas` / `BlockGasLimitExceeded`, since post-Regolith deposits are explicitly *not* exempt from the block-gas check: [8](#0-7) [9](#0-8) [10](#0-9) 

The fault-proof/derivation driver has a Holocene-era mitigation that strips non-deposit transactions and retries when execution fails on a retryable error (which explicitly includes `BlockGasLimitExceeded`): [11](#0-10) [12](#0-11) 

However, at an upgrade block the transaction list is *already* deposit-only (L1 info tx + user deposits + upgrade deposit txs are all `TxDeposit`). Stripping non-deposits removes nothing, so the retry attempt fails identically and the driver returns `DriverError::Executor(e)` — a critical, unrecoverable error that halts derivation/fault-proof execution: [13](#0-12) 

### Impact Explanation
A single chain operator (or anyone able to submit the `GasLimit` `SystemConfigUpdate` log via the L1 SystemConfig, and anyone able to submit a large L1→L2 deposit) can engineer an upgrade block whose required deposit gas (L1 info tx + maximal user deposit(s) + fixed-size network-upgrade transactions) exceeds the chain's L2 gas limit. Because there is no enforced minimum gas limit and no pre-flight budget check reserving headroom for upgrade transactions, this causes:
- Rejection of the block during execution (`BlockGasLimitExceeded` / `TransactionGasLimitMoreThanAvailableBlockGas`).
- Failure of the Holocene deposit-only retry, because the block is already deposit-only, escalating to a hard driver error.
- A halt of block derivation and stateless/fault-proof execution (`proof/driver`, `proof/executor`), analogous to the reported DoS of "op-node and op-program" instances, since both the live node's derivation pipeline and the fault-proof program share this attributes-building/execution logic.

This matches the report's "High Risk" impact class: a single operator action (or coordinated operator + user deposit) can deny service to the entire network at a scheduled hard fork boundary, and if fault-proof re-derivation cannot proceed past that point, it can also block legitimate dispute resolution.

### Likelihood Explanation
Likelihood is bounded by two factors this analysis could not fully verify from the index: (1) whether the deployment configuration enforces a sane minimum gas limit outside of this code path (e.g., in the SystemConfig L1 contract or an operational gate not present in this repo), and (2) the current default gas limit versus the fixed sizes of upgrade transactions, which are on the order of a few million gas — small relative to typical 30M+ mainnet gas limits, but the report's threat model is precisely an operator who *lowers* the gas limit specifically for this purpose combined with a maximal deposit. Given that the code contains no protocol-level floor tied to `systemTxMaxGas + maxResourceLimit`-equivalent reservations, and no pre-submission budget check for upgrade transactions, the vulnerability is directly reachable by a malicious or compromised chain operator at any future hard-fork boundary that introduces new upgrade transactions.

### Recommendation
1. Enforce a minimum L2 gas limit when applying `GasLimitUpdate` (and at genesis/config validation) that is at least the sum of: the L1 info deposit gas reservation, the maximum aggregate user-deposit gas admitted per block, and the total gas of all currently-scheduled (and, ideally, all future known) network-upgrade transactions, plus a safety buffer — mirroring the OP Stack fix direction of reserving `systemTxMaxGas + maxResourceLimit + upgrade-tx buffer`.
2. In `StatefulAttributesBuilder::prepare_payload_attributes`, before returning attributes for a block containing `upgrade_transactions`, explicitly validate that `l1_info_tx gas + deposit_transactions gas + upgrade_transactions gas <= sys_config.gas_limit`, and treat a violation as a critical/reset pipeline error surfaced early (rather than only at execution time).
3. Since the Holocene deposit-only retry cannot rescue an already-deposit-only upgrade block, add an explicit guard/test ensuring upgrade blocks cannot be constructed with insufficient gas headroom, independent of the general deposit-only retry mechanism.

### Proof of Concept
1. Chain operator submits (or the L1 SystemConfig for this chain emits) a `ConfigUpdate(GasLimit)` log reducing `gas_limit` to a small value, e.g. just above the L1-info-tx reservation (`REGOLITH_SYSTEM_TX_GAS`). This is accepted unconditionally by `GasLimitUpdate::try_from` / `apply` with no floor check (`crates/common/genesis/src/updates/gas_limit.rs:16-57`).
2. Shortly before an upgrade activation timestamp (e.g. Isthmus/Jovian), the operator (or any user) submits a large L1→L2 deposit sized to consume nearly all remaining gas headroom.
3. At the upgrade activation block, `StatefulAttributesBuilder::prepare_payload_attributes` assembles `[l1_info_tx, deposit_transactions, upgrade_transactions]` (`crates/consensus/derive/src/attributes/stateful.rs:182-239`) without checking total gas against `sys_config.gas_limit`.
4. Block execution processes the L1 info tx and the large deposit, then hits the upgrade transactions with insufficient remaining block gas, returning `BlockGasLimitExceeded`/`TransactionGasLimitMoreThanAvailableBlockGas` (`crates/common/evm2/src/executor.rs:257-277`, `crates/common/evm/src/executor/block_executor.rs:216-228`).
5. The driver's Holocene retry strips non-deposit transactions, but the block is already deposit-only, so the retry fails identically and `DriverError::Executor(e)` is returned as a critical halt (`crates/proof/driver/src/core.rs:134-147`), stalling both the live node's derivation and the stateless fault-proof executor at the upgrade boundary.

### Citations

**File:** crates/common/genesis/src/updates/gas_limit.rs (L16-21)
```rust
impl GasLimitUpdate {
    /// Applies the update to the [`SystemConfig`].
    pub const fn apply(&self, config: &mut SystemConfig) {
        config.gas_limit = self.gas_limit;
    }
}
```

**File:** crates/common/genesis/src/updates/gas_limit.rs (L23-57)
```rust
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

**File:** crates/common/genesis/src/system/update.rs (L42-54)
```rust
    /// Applies the update to the [`SystemConfig`].
    pub const fn apply(&self, config: &mut SystemConfig) {
        match self {
            Self::Batcher(update) => update.apply(config),
            Self::GasConfig(update) => update.apply(config),
            Self::GasLimit(update) => update.apply(config),
            Self::UnsafeBlockSigner(_) => { /* Ignored in derivation */ }
            Self::Eip1559(update) => update.apply(config),
            Self::OperatorFee(update) => update.apply(config),
            Self::MinBaseFee(update) => update.apply(config),
            Self::DaFootprintGasScalar(update) => update.apply(config),
        }
    }
```

**File:** crates/consensus/derive/src/attributes/stateful.rs (L182-239)
```rust
        let mut upgrade_transactions: Vec<Bytes> = vec![];
        if self.rollup_cfg.is_ecotone_active(target_l2_time)
            && !self.rollup_cfg.is_ecotone_active(l2_parent.block_info.timestamp)
        {
            upgrade_transactions.extend(Upgrades::ECOTONE.txs());
        }
        if self.rollup_cfg.is_fjord_active(target_l2_time)
            && !self.rollup_cfg.is_fjord_active(l2_parent.block_info.timestamp)
        {
            upgrade_transactions.extend(Upgrades::FJORD.txs());
        }
        if self.rollup_cfg.is_isthmus_active(target_l2_time)
            && !self.rollup_cfg.is_isthmus_active(l2_parent.block_info.timestamp)
        {
            upgrade_transactions.extend(Upgrades::ISTHMUS.txs());
        }
        if self.rollup_cfg.is_jovian_active(target_l2_time)
            && !self.rollup_cfg.is_jovian_active(l2_parent.block_info.timestamp)
        {
            upgrade_transactions.extend(Upgrades::JOVIAN.txs());
        }

        // Build and encode the L1 info transaction for the current payload.
        let (_, l1_info_tx_envelope) = L1BlockInfoTx::try_new_with_deposit_tx(
            &self.rollup_cfg,
            &self.l1_cfg,
            &sys_config,
            sequence_number,
            &l1_header,
            l2_parent.block_info.timestamp,
            target_l2_time,
        )
        .map_err(|e| {
            PipelineError::AttributesBuilder(BuilderError::Custom(e.to_string())).crit()
        })?;
        let mut encoded_l1_info_tx = Vec::with_capacity(l1_info_tx_envelope.length());
        l1_info_tx_envelope.encode_2718(&mut encoded_l1_info_tx);

        let base_time_active = self.rollup_cfg.is_denim_active(target_l2_time);
        let mut txs = Vec::with_capacity(
            1 + usize::from(base_time_active)
                + deposit_transactions.len()
                + upgrade_transactions.len(),
        );
        txs.push(encoded_l1_info_tx.into());

        if base_time_active {
            let base_time = BaseTimeUpdateTx::new(target_l2_millis).map_err(|e| {
                PipelineError::AttributesBuilder(BuilderError::BaseTimeUpdate(e)).crit()
            })?;
            let envelope = base_time.into_deposit_tx(target_l2_number);
            let mut encoded = Vec::with_capacity(envelope.length());
            envelope.encode_2718(&mut encoded);
            txs.push(encoded.into());
        }

        txs.extend(deposit_transactions);
        txs.extend(upgrade_transactions);
```

**File:** crates/consensus/protocol/src/info/variant.rs (L221-239)
```rust
        let mut deposit_tx = TxDeposit {
            source_hash: source.source_hash(),
            from: SystemAddresses::DEPOSITOR_ACCOUNT,
            to: TxKind::Call(Predeploys::L1_BLOCK_INFO),
            mint: 0,
            value: U256::ZERO,
            gas_limit: 150_000_000,
            is_system_transaction: true,
            input: self.encode_calldata(),
        };

        // With the regolith upgrade, system transactions were deprecated, and we allocate
        // a constant amount of gas for special transactions like L1 block info.
        if rollup_config.is_regolith_active(l2_block_time) {
            deposit_tx.is_system_transaction = false;
            deposit_tx.gas_limit = REGOLITH_SYSTEM_TX_GAS;
        }

        deposit_tx.seal_slow()
```

**File:** crates/consensus/upgrades/src/isthmus.rs (L128-212)
```rust
    pub fn deposits() -> impl Iterator<Item = TxDeposit> {
        ([
            TxDeposit {
                source_hash: Self::deploy_l1_block_source(),
                from: Deployers::ISTHMUS_L1_BLOCK,
                to: TxKind::Create,
                mint: 0,
                value: U256::ZERO,
                gas_limit: 425_000,
                is_system_transaction: false,
                input: Self::l1_block_deployment_bytecode(),
            },
            TxDeposit {
                source_hash: Self::deploy_gas_price_oracle_source(),
                from: Deployers::ISTHMUS_GAS_PRICE_ORACLE,
                to: TxKind::Create,
                mint: 0,
                value: U256::ZERO,
                gas_limit: 1_625_000,
                is_system_transaction: false,
                input: Self::gas_price_oracle_deployment_bytecode(),
            },
            TxDeposit {
                source_hash: Self::deploy_operator_fee_vault_source(),
                from: Deployers::ISTHMUS_OPERATOR_FEE_VAULT,
                to: TxKind::Create,
                mint: 0,
                value: U256::ZERO,
                gas_limit: 500_000,
                is_system_transaction: false,
                input: Self::operator_fee_vault_deployment_bytecode(),
            },
            TxDeposit {
                source_hash: Self::update_l1_block_source(),
                from: Address::default(),
                to: TxKind::Call(Predeploys::L1_BLOCK_INFO),
                mint: 0,
                value: U256::ZERO,
                gas_limit: 50_000,
                is_system_transaction: false,
                input: UpgradeCalldata::build(Self::NEW_L1_BLOCK),
            },
            TxDeposit {
                source_hash: Self::update_gas_price_oracle_source(),
                from: Address::default(),
                to: TxKind::Call(Predeploys::GAS_PRICE_ORACLE),
                mint: 0,
                value: U256::ZERO,
                gas_limit: 50_000,
                is_system_transaction: false,
                input: UpgradeCalldata::build(Self::GAS_PRICE_ORACLE),
            },
            TxDeposit {
                source_hash: Self::update_operator_fee_vault_source(),
                from: Address::default(),
                to: TxKind::Call(Predeploys::OPERATOR_FEE_VAULT),
                mint: 0,
                value: U256::ZERO,
                gas_limit: 50_000,
                is_system_transaction: false,
                input: UpgradeCalldata::build(Self::OPERATOR_FEE_VAULT),
            },
            TxDeposit {
                source_hash: Self::enable_isthmus_source(),
                from: SystemAddresses::DEPOSITOR_ACCOUNT,
                to: TxKind::Call(Predeploys::GAS_PRICE_ORACLE),
                mint: 0,
                value: U256::ZERO,
                gas_limit: 90_000,
                is_system_transaction: false,
                input: Self::ENABLE_ISTHMUS_INPUT.into(),
            },
            TxDeposit {
                source_hash: Self::block_hash_history_contract_source(),
                from: Self::EIP2935_FROM,
                to: TxKind::Create,
                mint: 0,
                value: U256::ZERO,
                gas_limit: 250_000,
                is_system_transaction: false,
                input: Self::eip2935_creation_data(),
            },
        ])
        .into_iter()
    }
```

**File:** crates/consensus/upgrades/src/jovian.rs (L81-136)
```rust
    /// Returns the list of [`TxDeposit`]s for the network upgrade.
    pub fn deposits() -> impl Iterator<Item = TxDeposit> {
        ([
            TxDeposit {
                source_hash: Self::deploy_l1_block_source(),
                from: Deployers::JOVIAN_L1_BLOCK,
                to: TxKind::Create,
                mint: 0,
                value: U256::ZERO,
                gas_limit: 447_315,
                is_system_transaction: false,
                input: Self::l1_block_deployment_bytecode(),
            },
            TxDeposit {
                source_hash: Self::l1_block_proxy_update(),
                from: Address::ZERO,
                to: TxKind::Call(Predeploys::L1_BLOCK_INFO),
                mint: 0,
                value: U256::ZERO,
                gas_limit: 50_000,
                is_system_transaction: false,
                input: UpgradeCalldata::build(Self::l1_block_address()),
            },
            TxDeposit {
                source_hash: Self::gas_price_oracle(),
                from: Deployers::JOVIAN_GAS_PRICE_ORACLE,
                to: TxKind::Create,
                mint: 0,
                value: U256::ZERO,
                gas_limit: 1_750_714,
                is_system_transaction: false,
                input: Self::gas_price_oracle_deployment_bytecode(),
            },
            TxDeposit {
                source_hash: Self::gas_price_oracle_proxy_update(),
                from: Address::ZERO,
                to: TxKind::Call(Predeploys::GAS_PRICE_ORACLE),
                mint: 0,
                value: U256::ZERO,
                gas_limit: 50_000,
                is_system_transaction: false,
                input: UpgradeCalldata::build(Self::gas_price_oracle_address()),
            },
            TxDeposit {
                source_hash: Self::gas_price_oracle_enable_jovian(),
                from: SystemAddresses::DEPOSITOR_ACCOUNT,
                to: TxKind::Call(Predeploys::GAS_PRICE_ORACLE),
                mint: 0,
                value: U256::ZERO,
                gas_limit: 90_000,
                is_system_transaction: false,
                input: Self::gas_price_oracle_enable_jovian_bytecode(),
            },
        ])
        .into_iter()
    }
```

**File:** crates/common/evm/src/executor/block_executor.rs (L216-228)
```rust
        // The sum of the gas the transaction may consume, Tg, and the gas utilized in this block
        // prior, must be no greater than the block's gasLimit. For EIP-8130 the reserved amount is
        // `gas_limit + payer_auth`, since payer authentication is metered on top of the declared
        // gas_limit (see `reserved_block_gas`); for every other transaction it is `gas_limit`.
        let reserved_gas = Self::reserved_block_gas(&tx_env, tx.tx().gas_limit())?;
        let block_available_gas = self.evm.block().gas_limit().saturating_sub(self.gas_used);
        if reserved_gas > block_available_gas && (self.is_regolith || !is_deposit) {
            return Err(BlockValidationError::TransactionGasLimitMoreThanAvailableBlockGas {
                transaction_gas_limit: reserved_gas,
                block_available_gas,
            }
            .into());
        }
```

**File:** crates/common/evm2/src/executor.rs (L257-277)
```rust
        // Reject a transaction whose reserved gas exceeds the block's remaining gas, before
        // executing it. For an EIP-8130 transaction the reservation adds the payer's worst-case
        // authentication gas (metered on top of the sender-signed gas limit), matching the
        // reference `reserved_block_gas`. Pre-Regolith deposits are exempt (matching the reference's
        // `is_regolith || !is_deposit` guard); every other transaction is checked.
        let mut reserved_gas = tx.gas_limit();
        if let Some(signed) = tx.as_eip8130() {
            let payer_auth =
                IntrinsicGas::max_payer_auth_cost(signed).map_err(HandlerError::external)?;
            reserved_gas = reserved_gas.saturating_add(payer_auth);
        }
        let block_gas_limit = self.evm.block().gas_limit.saturating_to::<u64>();
        let block_available_gas = block_gas_limit.saturating_sub(self.gas_used);
        let is_regolith =
            (self.evm.config_spec_id().upgrade() as u8) >= (BaseUpgrade::Regolith as u8);
        if reserved_gas > block_available_gas && (is_regolith || !is_deposit) {
            return Err(HandlerError::external(BlockGasLimitExceeded {
                transaction_gas_limit: reserved_gas,
                block_available_gas,
            }));
        }
```

**File:** crates/common/evm2/tests/block_execution.rs (L167-181)
```rust
#[test]
fn rejects_post_regolith_deposit_over_block_gas_limit() {
    // Post-Regolith deposits ARE subject to the block-gas check.
    let mut executor =
        executor_with_block_gas_limit(BaseSpecId::new(BaseUpgrade::Regolith), 50_000);
    let deposit = TxDeposit {
        from: SENDER,
        to: TxKind::Call(TARGET),
        gas_limit: 100_000,
        ..Default::default()
    };
    executor
        .execute_transaction(&Recovered::new_unchecked(BaseTxEnvelope::Deposit(deposit), SENDER))
        .expect_err("post-Regolith deposit over the block gas limit is rejected");
}
```

**File:** crates/proof/executor/src/errors.rs (L206-222)
```rust
impl ExecutorError {
    /// Returns whether this error represents an invalid payload that may use Holocene
    /// deposit-only recovery.
    ///
    /// Only errors caused by per-transaction invalidity are eligible: stripping
    /// non-deposit transactions cannot rescue a block whose header/attribute fields are
    /// themselves invalid (e.g. `InvalidExtraData`, `MissingEIP1559Params`), so those
    /// propagate as a hard halt instead.
    pub const fn is_deposit_only_retryable(&self) -> bool {
        matches!(
            self,
            Self::BlockGasLimitExceeded
                | Self::UnsupportedTransactionType(_)
                | Self::ExecutionError(BlockExecutionError::Validation(_))
                | Self::Recovery(_)
        )
    }
```

**File:** crates/proof/driver/src/core.rs (L104-147)
```rust
            let outcome = match self.executor.execute_payload(attributes.clone()).await {
                Ok(outcome) => outcome,
                Err(e) => {
                    error!(target: "client", error = %e, "Failed to execute L2 block");

                    if !cfg.is_holocene_active(attributes.payload_attributes.timestamp) {
                        // Pre-Holocene, discard the block if execution fails.
                        continue;
                    }

                    if !E::is_deposit_only_retryable(&e) {
                        return Err(DriverError::Executor(e));
                    }

                    // Retry with a deposit-only block.
                    warn!(target: "client", "Flushing current channel and retrying deposit only block");

                    // Flush the current batch and channel - if a block was replaced with a
                    // deposit-only block due to execution failure, the
                    // batch and channel it is contained in is forwards
                    // invalidated.
                    self.pipeline.signal(Signal::FlushChannel).await?;

                    // Strip out all transactions that are not deposits.
                    attributes.transactions = attributes.transactions.map(|txs| {
                        txs.into_iter()
                            .filter(|tx| !tx.is_empty() && tx[0] == OpTxType::Deposit as u8)
                            .collect::<Vec<_>>()
                    });

                    // Retry the execution.
                    self.executor.update_safe_head(tip_cursor.l2_safe_head_header.clone());
                    match self.executor.execute_payload(attributes.clone()).await {
                        Ok(header) => header,
                        Err(e) => {
                            error!(
                                target: "client",
                                error = %e,
                                "Critical - Failed to execute deposit-only block",
                            );
                            return Err(DriverError::Executor(e));
                        }
                    }
                }
```
