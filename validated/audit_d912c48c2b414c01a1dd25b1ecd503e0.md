Confirmed: `derive_deposits` in `crates/consensus/derive/src/attributes/stateful.rs` collects every `TransactionDeposited` log from an L1 epoch's receipts into `deposit_transactions: Vec<Bytes>` with no count or cumulative-gas cap, and all of them are placed into a *single* L2 block (`prepare_payload_attributes`, lines 111–157: deposits are only gathered/assigned when `l2_parent.l1_origin.number != epoch.number`, i.e. on the first L2 block of the epoch; every subsequent L2 block in that epoch gets `deposit_transactions = vec![]`). [1](#0-0) [2](#0-1) 

### Title
Unbounded per-epoch deposit aggregation lets an attacker force an unbuildable/fatally-erroring L2 block, halting derivation - (File: crates/consensus/derive/src/attributes/stateful.rs)

### Summary
`StatefulAttributesBuilder::prepare_payload_attributes` collects *all* `TransactionDeposited` logs from every receipt in an L1 epoch block via `derive_deposits` with no limit on count or aggregate gas, and forces every one of them into the single L2 block that opens that epoch. Because this block is built with `no_tx_pool: true`, any transaction-level execution failure in it (e.g. exceeding the L2 block gas limit) is treated as fatal, not skippable.

### Finding Description
`derive_deposits` iterates every receipt/log in the L1 origin block and pushes a decoded deposit for each matching `TransactionDeposited` event with no cap on the number of deposits or their aggregate `gas_limit`: [1](#0-0) 

All of these are concatenated into `txs` for the *single* L2 block that starts the epoch (`deposit_transactions` is only non-empty on the epoch-opening block; later blocks in the same epoch get an empty vec), so an attacker who emits many cheap `TransactionDeposited` logs from L1 in one L1 block cannot have that batch spread across several L2 blocks — they all land in one L2 payload: [2](#0-1) [3](#0-2) 

Deposit transactions are exempt from the "invalid tx skip" path used for pool-derived transactions. When executing attribute-derived transactions with `no_tx_pool = true`, any EVM/validation error (including `TransactionGasLimitMoreThanAvailableBlockGas`, enforced for post-Regolith deposits) is escalated to a fatal `PayloadBuilderError`, matching the proof executor's strict behavior: [4](#0-3) [5](#0-4) 

Post-Regolith deposits are checked against the block gas limit the same as ordinary transactions: [6](#0-5) 

Since a single L1 block/epoch can contain enough `TransactionDeposited` logs (each carrying an attacker-chosen `gas_limit`) to push the sum of deposit gas limits above the L2 block's gas limit, and all of them must be bundled into one un-splittable L2 block, the sequencer/verifier cannot build a valid block for that epoch: it either must fatally error while trying to execute the forced deposit set, or (if construction is attempted regardless) never produce a payload that satisfies the block gas constraint.

### Impact Explanation
This is the direct analog of the referenced Hubble finding: the attacker can cheaply flood the "processing" queue (deposit logs in one L1 block/epoch) with more work than the "governance"-equivalent (the sequencer/verifier building one L2 block) can absorb in a single, non-splittable unit of work, because there is no per-deposit-batch cap independent of the attacker's L1 gas spend. If the aggregate reserved gas of an epoch's deposits exceeds the L2 block gas limit, the epoch-opening L2 block cannot be safely constructed, which can halt block production/derivation for that L2 chain instance (denial of service on the sequencer/verifier, i.e. a node-halt-class issue) until an out-of-band fix (e.g., config/gas-limit change) is applied.

### Likelihood Explanation
Reachable by any unprivileged L1 depositor: submitting many minimal-cost deposit transactions to the deposit contract in a single L1 block is a standard, permissionless action, and the cost asymmetry mirrors the original report — the attacker pays L1 gas once per deposit event, while the resulting forced-inclusion set is deterministic and unavoidable once it lands in an epoch. The severity depends on how large an aggregate deposit gas footprint is achievable within one L1 block's gas budget relative to the L2 block gas limit, which is uncertain without knowing exact configured L2 `gas_limit`/L1 gas costs per `TransactionDeposited` emission — this is a real, but not fully quantified in this review, constraint.

### Recommendation
Cap the aggregate gas reserved by deposits derived from a single L1 epoch (e.g., reject/defer epochs whose summed deposit `gas_limit` exceeds a fraction of the L2 block gas limit, or spread deposits for one epoch across multiple L2 blocks instead of forcing them all into the epoch-opening block), and/or bound the number of deposits processed per epoch, analogous to capping `withdraw` amounts in the referenced finding to prevent unbounded cheap entries from overwhelming a bounded processing step.

### Proof of Concept
1. Attacker submits N `TransactionDeposited`-emitting L1 transactions in a single L1 block, each with `gas_limit` chosen so that the sum of all N gas limits exceeds the target L2 chain's per-block gas limit (e.g., N × 100,000 > 30,000,000).
2. `derive_deposits` (crates/consensus/derive/src/attributes/stateful.rs:286-311) collects all N deposits unconditionally.
3. `prepare_payload_attributes` places all N deposit transactions into the single epoch-opening L2 block's `txs` (lines 220-239), with `no_tx_pool: true`.
4. When the sequencer/verifier executes this block, the post-Regolith deposit gas-limit check (crates/common/evm2/src/executor.rs:252-277) fails once cumulative gas exceeds the block gas limit, and because `no_tx_pool` is true this error is fatal rather than skippable (crates/builder/core/src/flashblocks/context.rs:621-634), preventing valid block production for that epoch.

### Citations

**File:** crates/consensus/derive/src/attributes/stateful.rs (L111-157)
```rust
        // If the L1 origin changed in this block, then we are in the first block of the epoch.
        // In this case we need to fetch all transaction receipts from the L1 origin block so
        // we can scan for user deposits.
        let sequence_number = if l2_parent.l1_origin.number != epoch.number {
            let header =
                self.receipts_fetcher.header_by_hash(epoch.hash).await.map_err(Into::into)?;
            if l2_parent.l1_origin.hash != header.parent_hash {
                return Err(PipelineErrorKind::Reset(
                    BuilderError::BlockMismatchEpochReset(
                        epoch,
                        l2_parent.l1_origin,
                        header.parent_hash,
                    )
                    .into(),
                ));
            }
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
```

**File:** crates/consensus/derive/src/attributes/stateful.rs (L220-239)
```rust
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

**File:** crates/builder/core/src/flashblocks/context.rs (L621-634)
```rust
            let ResultAndState { result, state } = match evm.transact(&sequencer_tx) {
                Ok(res) => res,
                Err(err) => {
                    if err.is_invalid_tx_err() && !no_tx_pool {
                        trace!(target: "payload_builder", %err, ?sequencer_tx, "Error in sequencer transaction, skipping.");
                        continue;
                    }
                    // Either a fatal execution error, or an invalid-tx error from an
                    // attribute-derived (`no_tx_pool=true`) transaction list. The latter must
                    // be fatal so the EL rejects the payload exactly like the proof executor
                    // does.
                    return Err(PayloadBuilderError::EvmExecutionError(Box::new(err)));
                }
            };
```

**File:** crates/common/evm2/src/executor.rs (L252-277)
```rust
    pub fn execute_transaction(&mut self, tx: &Recovered<BaseTxEnvelope>) -> HandlerResult<()> {
        let ty = tx.ty();
        let is_deposit = tx.is_deposit();
        let signer = tx.signer();

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
