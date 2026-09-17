### Title
Unbounded L1 Deposit Scan Can Produce a Deposit-Only Block That Exceeds the L2 Block Gas Limit, Permanently Halting Derivation - (File: `crates/consensus/derive/src/attributes/stateful.rs`)

### Summary
`StatefulAttributesBuilder::prepare_payload_attributes` scans *all* deposit events emitted in a single L1 origin block via `derive_deposits` and unconditionally appends every derived deposit transaction to the L2 payload's transaction list, with no cap on count or aggregate gas. [1](#0-0)  If execution of the resulting block fails, the driver's fallback path strips non-deposit transactions and retries with a "deposit-only" block, but if the deposit-only block itself exceeds the L2 block gas limit, the driver treats it as an unrecoverable error and aborts derivation. [2](#0-1) 

### Finding Description
This is the same bug class as the Sherlock report: an unbounded loop over externally-supplied data (there, an account's asset list; here, an L1 block's deposit-contract logs) feeds a downstream process that must fit within a hard resource ceiling (there, a single tx's gas; here, the L2 block gas limit).

`derive_deposits` iterates every receipt/log in the L1 origin block and decodes every matching deposit event with no upper bound on the number of deposits collected. [3](#0-2)  `prepare_payload_attributes` then extends the L2 payload's `txs` with the full `deposit_transactions` vector unconditionally. [4](#0-3)  Each deposit transaction carries its own attacker-chosen `gas_limit` (bounded only by the L1 deposit contract's per-deposit gas cap, not by any aggregate check here), and post-Regolith deposits are explicitly subject to the L2 block gas limit check in the block executor. [5](#0-4) [6](#0-5) 

If a sufficiently large number of deposits (or deposits with a sufficiently large aggregate gas_limit) are included by an L1 sequencer/user in a single L1 block, the resulting L2 block execution fails with `TransactionGasLimitMoreThanAvailableBlockGas`. Post-Holocene, the driver's recovery path strips all non-deposit transactions and retries with a deposit-only block. [7](#0-6)  Critically, this retry does **not** trim the deposit list itself — it only removes non-deposit transactions. If the deposit-only block still exceeds the L2 block gas limit (because the deposits alone are too numerous/heavy), the second `execute_payload` call also fails, and the driver treats this as fatal, returning `DriverError::Executor(e)` with no further recovery. [8](#0-7)  This error propagates out of `advance_to_target`, halting derivation of that L2 chain segment entirely — a chain/node halt in the sense of the rules (node can no longer make progress deriving canonical L2 state for that epoch).

### Impact Explanation
This maps to "node halt" / inability to derive/advance the canonical L2 chain: any node running this derivation pipeline (validators, the fault-proof program's derivation stage, and any full node deriving state from L1) would get permanently stuck at the epoch boundary once an L1 block contains deposits whose *combined* gas exceeds the configured L2 block gas limit, since there is no mechanism to split deposits across multiple L2 blocks or reject/skip individual oversized deposit sets — the only fallback (deposit-only retry) does not address deposits that are themselves too numerous. Because the fault-proof program and L2 nodes must derive identically from L1, this could also block dispute-game claim validation for the affected epoch.

### Likelihood Explanation
Triggering this requires only an anonymous L1 depositor submitting many deposit transactions targeting the OptimismPortal/deposit contract within a single L1 block — no privileged access or protocol-level compromise needed, and gas cost on L1 to spam deposits scales roughly linearly and is bounded by the L1 block gas limit, meaning an attacker with a full L1 block's worth of gas could plausibly generate an L2 deposit-only payload that exceeds a much smaller configured L2 block gas limit (particularly relevant if `sys_config.gas_limit` is set low, or after the Denim gas scaling division reduces it). [9](#0-8)  This is a credible, reachable path from an unprivileged L1 transaction sender.

### Recommendation
- Enforce an aggregate gas budget while collecting `deposit_transactions` in `derive_deposits`/`prepare_payload_attributes`, rejecting or deferring/splitting deposits that would push the L2 payload over the block gas limit, rather than passing every derived deposit through unconditionally.
- In the driver's deposit-only retry path (`crates/proof/driver/src/core.rs`), do not treat a still-failing deposit-only block as an unconditional fatal error; consider explicit protocol-level handling (e.g., spanning deposits over multiple synthetic blocks, or clearly documenting and enforcing an L1-side cap on deposits-per-block that keeps worst case within the L2 gas limit) so a single malicious/heavy L1 block cannot deadlock derivation.
- Add a regression test with an L1 block containing enough deposit events (or deposits with high `gas_limit`) that the derived deposit-only payload alone exceeds the configured L2 `gas_limit`, and assert the pipeline degrades gracefully instead of returning an unrecoverable `DriverError::Executor`.

### Proof of Concept
1. On L1, submit enough deposit transactions to the configured deposit contract within a single L1 block such that the sum of each derived `TxDeposit.gas_limit` exceeds the L2 chain's configured block `gas_limit` (`sys_config.gas_limit`, further reduced post-Denim by `DENIM_GAS_PARAMETER_SCALING_FACTOR`). [9](#0-8) 
2. When the L2 node derives the epoch, `derive_deposits` collects all of them without limit and `prepare_payload_attributes` appends them all to the payload's transaction list. [10](#0-9) 
3. `execute_payload` fails with a block-gas-limit-exceeded error because post-Regolith deposits are checked against the available block gas. [6](#0-5) 
4. The driver retries with a deposit-only block (stripping only non-deposits, which does nothing here since the payload was already deposit-heavy), and execution fails again for the same reason, causing `DriverError::Executor(e)` to propagate and derivation to halt. [11](#0-10) 

Note: I was unable to fully trace whether any upstream L1-side cap (e.g., in the deposit/portal contract or in `receipts_by_hash`/mempool admission) bounds the number/gas of deposits per L1 block, since those contracts are out of this repo's indexed scope; if such a cap already keeps aggregate deposit gas well under the L2 block gas limit, this reduces to a low-likelihood/config-dependent issue rather than a directly exploitable one. I recommend verifying this L1-side constraint with a full Devin session with repository and L1 contract access before treating this as confirmed-exploitable.

### Citations

**File:** crates/consensus/derive/src/attributes/stateful.rs (L127-239)
```rust
            let receipts =
                self.receipts_fetcher.receipts_by_hash(epoch.hash).await.map_err(Into::into)?;
            let deposits =
                derive_deposits(epoch.hash, &receipts, self.rollup_cfg.deposit_contract_address)
                    .await
                    .map_err(|e| PipelineError::BadEncoding(e).crit())?;
            let (updates, errors) = sys_config.update_with_receipts(
                &receipts,
                self.rollup_cfg.l1_system_config_address,
                self.rollup_cfg.is_ecotone_active(header.timestamp),
            );
            for kind in &updates {
                info!(target: "attributes", epoch = epoch.number, %kind, "Applied system config update");
            }
            for err in &errors {
                warn!(target: "attributes", error = ?err, epoch = epoch.number, "Malformed system config update (skipped)");
            }
            l1_header = header;
            deposit_transactions = deposits;
            0
        } else if l2_parent.l1_origin.hash != epoch.hash {
            return Err(PipelineErrorKind::Reset(
                BuilderError::BlockMismatch(epoch, l2_parent.l1_origin).into(),
            ));
        } else {
            let header =
                self.receipts_fetcher.header_by_hash(epoch.hash).await.map_err(Into::into)?;
            l1_header = header;
            deposit_transactions = vec![];
            l2_parent.seq_num + 1
        };

        // Sanity check the L1 origin was correctly selected to maintain the time invariant
        // between L1 and L2.
        if target_l2_time < l1_header.timestamp {
            return Err(PipelineErrorKind::Reset(
                BuilderError::BrokenTimeInvariant(
                    l2_parent.l1_origin,
                    target_l2_time,
                    BlockNumHash { hash: l1_header.hash_slow(), number: l1_header.number },
                    l1_header.timestamp,
                )
                .into(),
            ));
        }

        if self.rollup_cfg.is_first_denim_block(target_l2_time, l2_parent.block_info.timestamp) {
            // Preserve gas throughput and base-fee responsiveness per unit of wall-clock time
            // when Denim increases the number of blocks in each legacy block interval tenfold.
            sys_config.gas_limit /= u64::from(RollupConfig::DENIM_GAS_PARAMETER_SCALING_FACTOR);
            sys_config.eip1559_denominator = sys_config.eip1559_denominator.map(|denominator| {
                denominator.saturating_mul(RollupConfig::DENIM_GAS_PARAMETER_SCALING_FACTOR)
            });
        }

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

**File:** crates/consensus/derive/src/attributes/stateful.rs (L286-311)
```rust
async fn derive_deposits(
    block_hash: B256,
    receipts: &[Receipt],
    deposit_contract: Address,
) -> Result<Vec<Bytes>, PipelineEncodingError> {
    let mut global_index = 0;
    let mut res = Vec::new();
    for r in receipts {
        if Eip658Value::Eip658(false) == r.status {
            continue;
        }
        for l in &r.logs {
            let curr_index = global_index;
            global_index += 1;
            if l.data.topics().first().is_none_or(|i| *i != Deposits::EVENT_ABI_HASH) {
                continue;
            }
            if l.address != deposit_contract {
                continue;
            }
            let decoded = Deposits::decode(block_hash, curr_index, l)?;
            res.push(decoded);
        }
    }
    Ok(res)
}
```

**File:** crates/proof/driver/src/core.rs (L118-146)
```rust
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
