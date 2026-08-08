### Title
`getInflationReward` disproportionately amplifies a single RPC call into many expensive full-block reads - (File: rpc/src/rpc.rs)

### Summary
`JsonRpcRequestProcessor::get_inflation_reward` accepts a bounded list of addresses but, when partitioned epoch rewards are active, fans that single call out into one full `get_block` fetch **per distinct reward partition** that the caller's addresses hash into. Each `get_block` call is a heavyweight blockstore read/decode of an entire confirmed block, not a cheap per-address lookup — mirroring the code-423n4 bug class where a bounded, attacker-influenced loop performs disproportionately expensive per-item work relative to what the caller "paid" for.

### Finding Description
The externally-reachable RPC method is capped only in the number of *addresses*: [1](#0-0) 

Inside `get_inflation_reward`, when the epoch uses partitioned rewards, addresses not already resolved from the epoch-boundary block are bucketed by hashed partition index: [2](#0-1) 

The code then iterates over the resulting `partition_index_addresses` map and, for **each distinct partition**, performs a full `self.get_block(...)` call to fetch and decode an entire confirmed block from the blockstore/bigtable, purely to extract the reward lines relevant to a handful of addresses: [3](#0-2) 

`get_block` itself is a heavy operation — it spawns a blocking task that reads the full slot from RocksDB, reconstructs entries/transactions, and decodes every transaction's status metadata for the whole block: [4](#0-3) [5](#0-4) [6](#0-5) 

Because `EpochRewardsHasher` distributes addresses pseudo-randomly across `num_partitions` buckets, a caller can pick up to `MAX_GET_INFLATION_REWARD_ADDRESSES` addresses (list size bounded, but the *hash outcome* is attacker-influenceable by trying many candidate addresses/keys off-chain until enough distinct partitions are hit) so that each address lands in a different partition, forcing the RPC node to perform up to that many separate full-block reads and full-block transaction-status decodes for a single incoming JSON-RPC request — exactly the "N items, each doing far more work than the minimal case" pattern described in the referenced report, except here the "gas" is CPU/disk I/O on the validator's JSON-RPC/blockstore path instead of EVM gas.

### Impact Explanation
A single `getInflationReward` call with a modest, allowed-size address list can be crafted (by picking addresses that hash to distinct partitions) to trigger dozens of full confirmed-block fetches and decodes from the blockstore, each of which is orders of magnitude more expensive than the reward lookup the caller ostensibly requested. This wastes validator RPC-node CPU/disk resources disproportionately to the cost the caller incurs (one request), a resource-exhaustion pattern analogous to forwarding excess gas per recipient in the original finding — "unbounded cost for a single low-rate call" from an unprivileged JSON-RPC caller.

### Likelihood Explanation
Requires only a single legitimate `getInflationReward` RPC call (no special privileges) once the target epoch has partitioned rewards enabled and the RPC node has `--enable-rpc-transaction-history` (required by `check_if_transaction_history_enabled`, gating `get_block`). Finding addresses that hash to distinct partitions is a matter of offline computation using the public `EpochRewardsHasher` algorithm, so this is straightforward for an attacker to construct without needing multiple RPC calls per `CLUSTER_SLOT_TIME_TARGET / 2` or unfiltered `getProgramAccounts`.

### Recommendation
Cap the number of distinct partitions/blocks fetched per `getInflationReward` call independent of address-list size (e.g., limit `partition_index_addresses.len()` or deduplicate/batch partition lookups), or fetch partition reward data via a lighter-weight, purpose-built accessor rather than a full `get_block` decode per partition.

### Proof of Concept
1. Enable `--enable-rpc-transaction-history` on a target node and wait for an epoch boundary with partitioned rewards enabled (`epoch_boundary_block.num_reward_partitions.is_some()`).
2. Offline, compute `EpochRewardsHasher::hash_address_to_partition` for many candidate pubkeys to find `MAX_GET_INFLATION_REWARD_ADDRESSES` addresses that map to distinct partitions and are not present in the epoch-boundary block's reward list.
3. Issue one `getInflationReward` JSON-RPC call with that address list. The node executes `get_inflation_reward` at [3](#0-2) , invoking `get_block` once per distinct partition — each a full blockstore read/decode as shown at [5](#0-4)  — from a single client request.

### Citations

**File:** rpc/src/rpc.rs (L797-824)
```rust
        // Append stake account rewards from partitions if partitions epoch
        // rewards is enabled
        if epoch_has_partitioned_rewards {
            let num_partitions = epoch_boundary_block.num_reward_partitions.expect(
                "epoch-boundary block should have num_reward_partitions for epochs with \
                 partitioned rewards enabled",
            );

            let num_partitions = usize::try_from(num_partitions)
                .expect("num_partitions should never exceed usize::MAX");
            let hasher = EpochRewardsHasher::new(
                num_partitions,
                &Hash::from_str(&epoch_boundary_block.previous_blockhash)
                    .expect("UiConfirmedBlock::previous_blockhash should be properly formed"),
            );
            let mut partition_index_addresses: HashMap<usize, Vec<String>> = HashMap::new();
            for address in addresses.iter() {
                let address_string = address.to_string();
                // Skip this address if (Voting) rewards were already found in
                // the first block of the epoch
                if !reward_map.contains_key(&address_string) {
                    let partition_index = hasher.clone().hash_address_to_partition(address);
                    partition_index_addresses
                        .entry(partition_index)
                        .and_modify(|list| list.push(address_string.clone()))
                        .or_insert(vec![address_string]);
                }
            }
```

**File:** rpc/src/rpc.rs (L826-878)
```rust
            let block_list = self
                .get_blocks_with_limit(
                    first_confirmed_block_in_epoch + 1,
                    num_partitions,
                    Some(context_config),
                )
                .await?;

            for (partition_index, addresses) in partition_index_addresses.iter() {
                let slot = *block_list.get(*partition_index).ok_or_else(|| {
                    // If block_list.len() too short to contain
                    // partition_index, the epoch rewards period must be
                    // currently active.
                    let rewards_complete_block_height = epoch_boundary_block
                        .block_height
                        .map(|block_height| {
                            block_height
                                .saturating_add(num_partitions as u64)
                                .saturating_add(1)
                        })
                        .expect(
                            "every block after partitioned_epoch_reward_enabled should have a \
                             populated block_height",
                        );
                    RpcCustomError::EpochRewardsPeriodActive {
                        slot: bank.slot(),
                        current_block_height: bank.block_height(),
                        rewards_complete_block_height,
                    }
                })?;

                let Ok(Some(block)) = self
                    .get_block(
                        slot,
                        Some(RpcBlockConfig::rewards_with_commitment(config.commitment).into()),
                    )
                    .await
                else {
                    return Err(RpcCustomError::BlockNotAvailable { slot }.into());
                };

                let index_reward_map = Self::filter_map_rewards(
                    block.rewards,
                    slot,
                    addresses,
                    &|reward_type| -> bool {
                        reward_type == RewardType::Staking
                            || reward_type == RewardType::DeactivatedStake
                    },
                );
                reward_map.extend(index_reward_map);
            }
        }
```

**File:** rpc/src/rpc.rs (L1315-1352)
```rust
    #[allow(clippy::result_large_err)]
    pub async fn get_block(
        &self,
        slot: Slot,
        config: Option<RpcEncodingConfigWrapper<RpcBlockConfig>>,
    ) -> Result<Option<UiConfirmedBlock>> {
        self.check_if_transaction_history_enabled()?;

        let config = config
            .map(|config| config.convert_to_current())
            .unwrap_or_default();
        let encoding = config.encoding.unwrap_or(UiTransactionEncoding::Json);
        let encoding_options = BlockEncodingOptions {
            transaction_details: config.transaction_details.unwrap_or_default(),
            show_rewards: config.rewards.unwrap_or(true),
            max_supported_transaction_version: config.max_supported_transaction_version,
        };
        let commitment = config.commitment.unwrap_or_default();
        check_is_at_least_confirmed(commitment)?;

        // Block is old enough to be finalized
        if slot
            <= self
                .block_commitment_cache
                .read()
                .unwrap()
                .highest_super_majority_root()
        {
            self.check_blockstore_writes_complete(slot)?;
            let result = self
                .runtime
                .spawn_blocking({
                    let blockstore = Arc::clone(&self.blockstore);
                    move || blockstore.get_rooted_block(slot, true)
                })
                .await
                .expect("Failed to spawn blocking task");
            self.check_blockstore_root(&result, slot)?;
```

**File:** rpc/src/rpc.rs (L4272-4286)
```rust
        fn get_inflation_reward(
            &self,
            meta: Self::Metadata,
            address_strs: Vec<String>,
            config: Option<RpcEpochConfig>,
        ) -> BoxFuture<Result<Vec<Option<RpcInflationReward>>>> {
            debug!(
                "get_inflation_reward rpc request received: {:?}",
                address_strs.len()
            );
            if address_strs.len() > MAX_GET_INFLATION_REWARD_ADDRESSES {
                return Box::pin(future::err(Error::invalid_params(format!(
                    "Too many inputs provided; max {MAX_GET_INFLATION_REWARD_ADDRESSES}"
                ))));
            }
```

**File:** ledger/src/blockstore.rs (L4058-4155)
```rust
    fn do_get_complete_block_with_components(
        &self,
        slot: Slot,
        require_previous_blockhash: bool,
        populate_components: bool,
        allow_dead_slots: bool,
    ) -> Result<VersionedConfirmedBlockWithComponents> {
        let Some(slot_meta) = self.meta_cf.get(slot)? else {
            trace!("do_get_complete_block_with_components() failed for {slot} (missing SlotMeta)");
            return Err(BlockstoreError::SlotUnavailable);
        };

        if !slot_meta.is_full() {
            trace!("do_get_complete_block_with_components() failed for {slot} (slot not full)");
            return Err(BlockstoreError::SlotUnavailable);
        }

        let (slot_components, _, _) = self.get_slot_components_with_shred_info(
            slot,
            /*start_index:*/ 0,
            allow_dead_slots,
        )?;

        if slot_components.is_empty() {
            trace!(
                "do_get_complete_block_with_components() failed for {slot} (no components found)"
            );
            return Err(BlockstoreError::SlotUnavailable);
        }

        let blockhash = slot_components
            .iter()
            .rev()
            .find_map(|component| match component {
                BlockComponent::EntryBatch(entries) => entries.last().map(|entry| entry.hash),
                BlockComponent::BlockMarker(_) => None,
            })
            .unwrap_or_else(|| panic!("Rooted slot {slot:?} must have blockhash"));

        let mut starting_transaction_index = 0;
        let mut components = if populate_components {
            Vec::with_capacity(slot_components.len())
        } else {
            Vec::new()
        };

        let slot_transaction_iterator = slot_components
            .into_iter()
            .filter_map(|component| match component {
                BlockComponent::EntryBatch(entries) => {
                    if populate_components {
                        let entry_summaries = entries
                            .iter()
                            .map(|entry| {
                                let entry_summary = EntrySummary {
                                    num_hashes: entry.num_hashes,
                                    hash: entry.hash,
                                    num_transactions: entry.transactions.len() as u64,
                                    starting_transaction_index,
                                };
                                starting_transaction_index += entry.transactions.len();
                                entry_summary
                            })
                            .collect();
                        components.push(ConfirmedBlockComponent::EntryBatch(entry_summaries));
                    }
                    Some(entries)
                }
                BlockComponent::BlockMarker(marker) => {
                    if populate_components {
                        components.push(ConfirmedBlockComponent::BlockMarker(marker));
                    }
                    None
                }
            })
            .flatten()
            .flat_map(|entry| entry.transactions)
            .map(|transaction| {
                if let Err(err) = transaction.sanitize() {
                    warn!(
                        "Blockstore::get_complete_block_with_components sanitize failed: {err:?}, \
                         slot: {slot:?}, {transaction:?}",
                    );
                }
                transaction
            });

        let block = self.build_versioned_confirmed_block(
            slot,
            require_previous_blockhash,
            allow_dead_slots,
            &slot_meta,
            &blockhash,
            slot_transaction_iterator,
        )?;

        Ok(VersionedConfirmedBlockWithComponents { block, components })
    }
```

**File:** ledger/src/blockstore.rs (L4227-4243)
```rust
    pub fn map_transactions_to_statuses(
        &self,
        slot: Slot,
        iterator: impl Iterator<Item = VersionedTransaction>,
    ) -> Result<Vec<VersionedTransactionWithStatusMeta>> {
        iterator
            .map(|transaction| {
                let signature = transaction.signatures[0];
                Ok(VersionedTransactionWithStatusMeta {
                    transaction,
                    meta: self
                        .read_transaction_status((signature, slot))?
                        .ok_or(BlockstoreError::MissingTransactionMetadata)?,
                })
            })
            .collect()
    }
```
