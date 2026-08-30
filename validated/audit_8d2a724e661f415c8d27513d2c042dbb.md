Based on my investigation, the code confirms the described invariant holds correctly, so there is no vulnerability here.

`current_protocol_version` is derived from `epoch_id` — the chunk's own epoch, obtained via `self.epoch_manager.get_epoch_id_from_prev_block(prev_block_hash)` and then `self.epoch_manager.get_epoch_protocol_version(&epoch_id)` — not `prev_block_epoch_id` and not `PROTOCOL_VERSION`. [1](#0-0) 

`apply_state.config` is captured exactly once as `config.clone()` from `self.runtime_config_store.get_config(current_protocol_version)` before constructing `ApplyState`, and `RuntimeConfigStore::get_config` is a pure floor-lookup with no side effects. [2](#0-1) [3](#0-2) 

Downstream, `Runtime::apply` never re-queries the `RuntimeConfigStore`; `process_transactions` reads `processing_state.apply_state.config` for `tx_cost`, and `apply_action_receipt`/`refund_unspent_gas_and_deposits` take `&apply_state.config` as a parameter rather than re-fetching. [4](#0-3) [5](#0-4) [6](#0-5)  The single `ApplyState` (and its one captured `Arc<RuntimeConfig>`) is passed through the whole `apply` call — validator accounts update, `process_transactions`, `process_receipts`, and finalization all share the same `processing_state.apply_state` — so a chunk cannot apply some receipts under one config and others under a different one. [7](#0-6) 

The `next_wasm_config` lookup at line 321 does query the store for the *next* epoch's version, but this is only used to detect and pre-surface wasm-cache invalidation (`cache_keys_differ`) — it is stored separately in `ApplyState.next_wasm_config` and is not substituted into `apply_state.config`, so it does not affect fee/gas computation for the current chunk. [8](#0-7) 

### No vulnerability found for this question.

### Citations

**File:** chain/chain/src/runtime/mod.rs (L232-286)
```rust
        let epoch_id = self.epoch_manager.get_epoch_id_from_prev_block(prev_block_hash)?;
        let validator_accounts_update = {
            let epoch_manager = self.epoch_manager.read();
            let shard_layout = epoch_manager.get_shard_layout(&epoch_id)?;
            tracing::debug!(
                target: "runtime",
                next_block_epoch_start = epoch_manager.is_next_block_epoch_start(prev_block_hash).unwrap()
            );

            if epoch_manager.is_next_block_epoch_start(prev_block_hash)? {
                let (stake_info, validator_reward) =
                    epoch_manager.compute_stake_return_info(prev_block_hash)?;
                let stake_info = stake_info
                    .into_iter()
                    .filter(|(account_id, _)| {
                        shard_layout.account_id_to_shard_id(account_id) == shard_id
                    })
                    .collect();
                let validator_rewards = validator_reward
                    .into_iter()
                    .filter(|(account_id, _)| {
                        shard_layout.account_id_to_shard_id(account_id) == shard_id
                    })
                    .collect();
                let last_proposals = last_validator_proposals
                    .filter(|v| shard_layout.account_id_to_shard_id(v.account_id()) == shard_id)
                    .fold(HashMap::new(), |mut acc, v| {
                        let (account_id, stake) = v.account_and_stake();
                        acc.insert(account_id, stake);
                        acc
                    });
                Some(ValidatorAccountsUpdate {
                    stake_info,
                    validator_rewards,
                    last_proposals,
                    protocol_treasury_account_id: Some(
                        self.genesis_config.protocol_treasury_account.clone(),
                    )
                    .filter(|account_id| {
                        shard_layout.account_id_to_shard_id(account_id) == shard_id
                    }),
                })
            } else {
                None
            }
        };

        let epoch_height = self.epoch_manager.get_epoch_height_from_prev_block(prev_block_hash)?;
        let prev_block_epoch_id = self.epoch_manager.get_epoch_id(prev_block_hash)?;
        let current_protocol_version = self.epoch_manager.get_epoch_protocol_version(&epoch_id)?;
        let prev_block_protocol_version =
            self.epoch_manager.get_epoch_protocol_version(&prev_block_epoch_id)?;
        let is_first_block_of_version = current_protocol_version != prev_block_protocol_version;

        let config = self.runtime_config_store.get_config(current_protocol_version);
```

**File:** chain/chain/src/runtime/mod.rs (L313-338)
```rust
        // Detect an upcoming protocol upgrade that would invalidate the
        // compiled-contract cache, and surface the next epoch's wasm_config.
        let next_wasm_config = self
            .epoch_manager
            .get_next_epoch_protocol_version_from_prev_block(prev_block_hash)
            .ok()
            .filter(|next_pv| *next_pv != current_protocol_version)
            .and_then(|next_pv| {
                let next = Arc::clone(&self.runtime_config_store.get_config(next_pv).wasm_config);
                cache_keys_differ(Arc::clone(&config.wasm_config), Arc::clone(&next))
                    .then_some(next)
            });
        let apply_state = ApplyState {
            apply_reason,
            block_height,
            prev_block_hash: *prev_block_hash,
            shard_id,
            epoch_id,
            epoch_height,
            gas_price,
            block_timestamp,
            gas_limit: Some(gas_limit),
            random_seed,
            current_protocol_version,
            config: config.clone(),
            next_wasm_config,
```

**File:** core/parameters/src/config_store.rs (L242-250)
```rust
    pub fn get_config(&self, protocol_version: ProtocolVersion) -> &Arc<RuntimeConfig> {
        self.store
            .range((Bound::Unbounded, Bound::Included(protocol_version)))
            .next_back()
            .unwrap_or_else(|| {
                panic!("Not found RuntimeConfig for protocol version {}", protocol_version)
            })
            .1
    }
```

**File:** runtime/runtime/src/lib.rs (L1007-1016)
```rust
            self.refund_unspent_gas_and_deposits(
                gas_burn_price,
                gas_purchase_price,
                receipt,
                &action_receipt,
                &mut result,
                &apply_state.config,
                created_new_account,
                apply_state.current_protocol_version,
            )?
```

**File:** runtime/runtime/src/lib.rs (L1230-1248)
```rust
    fn refund_unspent_gas_and_deposits(
        &self,
        gas_burn_price: Balance,
        gas_purchase_price: Balance,
        receipt: &Receipt,
        action_receipt: &VersionedActionReceipt,
        result: &mut ActionReceiptResult,
        config: &RuntimeConfig,
        created_account: bool,
        protocol_version: ProtocolVersion,
    ) -> Result<GasRefundResult, RuntimeError> {
        let total_deposit = total_deposit(&action_receipt.actions())?;
        let prepaid_gas = total_prepaid_gas(&action_receipt.actions())?
            .checked_add(total_prepaid_send_fees(config, &action_receipt.actions())?.gas)
            .ok_or(IntegerOverflowError)?;
        let prepaid_exec_gas =
            total_prepaid_exec_fees(config, &action_receipt.actions(), receipt.receiver_id())?
                .checked_add(config.fees.fee(ActionCosts::new_action_receipt).exec_fee())
                .ok_or(IntegerOverflowError)?;
```

**File:** runtime/runtime/src/lib.rs (L1804-1866)
```rust
        let mut processing_state =
            ApplyProcessingState::new(&apply_state, trie, epoch_info_provider);
        processing_state.stats.transactions_num = signed_txs.len().try_into().unwrap();
        processing_state.stats.incoming_receipts_num = incoming_receipts.len().try_into().unwrap();
        processing_state.stats.is_new_chunk = !apply_state.is_new_chunk;

        if let Some(prefetcher) = &mut processing_state.prefetcher {
            // Prefetcher is allowed to fail
            _ = prefetcher.prefetch_transactions_data(&signed_txs);
        }

        // Step 1: update validator accounts.
        if let Some(validator_accounts_update) = validator_accounts_update {
            self.update_validator_accounts(
                &mut processing_state.state_update,
                validator_accounts_update,
            )?;
        }

        let delayed_receipts = DelayedReceiptQueueWrapper::new(
            DelayedReceiptQueue::load(&processing_state.state_update)?,
            epoch_info_provider,
            apply_state.shard_id,
            apply_state.epoch_id,
        );

        // Bandwidth scheduler should be run for every chunk, including the missing ones.
        let bandwidth_scheduler_output = run_bandwidth_scheduler(
            apply_state,
            &mut processing_state.state_update,
            epoch_info_provider,
            &mut processing_state.stats.bandwidth_scheduler,
        )?;

        // If the chunk is missing, exit early and don't process any receipts.
        if !apply_state.is_new_chunk {
            return missing_chunk_apply_result(
                &delayed_receipts,
                processing_state,
                &bandwidth_scheduler_output,
            );
        }

        let mut processing_state =
            processing_state.into_processing_receipt_state(incoming_receipts, delayed_receipts);
        let own_congestion_info =
            apply_state.own_congestion_info(&processing_state.state_update)?;
        let mut receipt_sink = ReceiptSink::new(
            &processing_state.state_update.trie,
            apply_state,
            own_congestion_info,
            bandwidth_scheduler_output,
            processing_state.epoch_info_provider,
        )?;
        // Forward buffered receipts from previous chunks.
        receipt_sink.forward_from_buffer(&mut processing_state.state_update, apply_state)?;

        // Step 2: process transactions.
        self.process_transactions(&mut processing_state, signed_txs, &mut receipt_sink)?;

        // Step 3: process receipts.
        let process_receipts_result =
            self.process_receipts(&mut processing_state, &mut receipt_sink)?;
```

**File:** runtime/runtime/src/lib.rs (L2056-2058)
```rust
            let cost =
                match tx_cost(&processing_state.apply_state.config, &tx.transaction, gas_price) {
                    Ok(c) => c,
```
