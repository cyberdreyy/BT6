### Title
`getSignatureStatuses` re-samples the mutable commitment/confirmation state independently for every signature in a batch, producing internally inconsistent confirmation results within a single RPC response - (File: rpc/src/rpc.rs)

### Summary
This is the closest agave analog to the `PartyGovernance` bug class: a decision-critical value that changes over time (there, `passThresholdBps`; here, `BlockCommitmentCache` and the "confirmed" bank) is read live/global at evaluation time instead of being snapshotted once for the whole logical operation, letting an in-flight external update change the outcome mid-operation.

### Finding Description
`JsonRpcRequestProcessor::get_signature_statuses` fetches a single `processed` bank once for the whole request and then loops over the requested signatures, computing each one's status via `get_transaction_status`: [1](#0-0) 

Inside `get_transaction_status`, for **every** signature in the batch the code independently re-acquires the live "confirmed" bank and independently re-reads the live `block_commitment_cache`: [2](#0-1) 

`block_commitment_cache` and the `OptimisticallyConfirmedBank` are both mutated concurrently and asynchronously by the `AggregateCommitmentService`/commitment update paths, completely independent of the RPC thread: [3](#0-2) [4](#0-3) 

Because `root()`, `highest_super_majority_root()`, `get_confirmation_count()`, and the optimistically-confirmed bank lookup are all re-read fresh on each loop iteration rather than being captured once (the way `totalVotingPower` is cached per-proposal in the referenced Party pattern, or the way the initial `processed` bank *is* correctly cached once for the whole call), a background root/commitment update that lands between two iterations of the `for signature in signatures` loop causes different signatures in the *same* JSON-RPC response to be evaluated against different, mutually inconsistent commitment snapshots.

### Impact Explanation
A single unprivileged `getSignatureStatuses` call can return a response where the top-level `context.slot` (derived from the one bank fetched at the start of the call) is inconsistent with the per-signature `confirmationStatus`/`confirmations` fields, and where two signatures included in the very same array can be reported under different, non-atomic views of chain finality (e.g., one evaluated before a root advance is applied, the other after). This is a wrong/inconsistent-data-returned bug from a single low-cost, unprivileged JSON-RPC query — it can mislead callers (wallets, exchanges, bridges) that rely on this call to make finality decisions within one request/response cycle, even though no single field is individually "wrong" relative to the exact instant it was read.

### Likelihood Explanation
`getSignatureStatuses` is a very commonly used, unprivileged, single-call JSON-RPC method that frequently receives large batches of signatures. `AggregateCommitmentService` continuously updates `block_commitment_cache` in the background at a high rate on any active validator, so races between an in-progress `getSignatureStatuses` loop and a background commitment update are routine on a live cluster; this does not require malicious input, multiple RPC calls, or a privileged actor to trigger — it only requires normal batch usage in the ordinary course of validator operation.

### Recommendation
Snapshot the commitment-relevant state (`block_commitment_cache` read guard, and the "confirmed" bank reference) once at the top of `get_signature_statuses`/`get_transaction_status`, and pass that single cached snapshot through the entire per-signature loop, analogous to how `passThresholdBps` should be cached once at proposal-check time rather than re-read from mutable global state on every evaluation. This ensures all signatures in a single response are evaluated against one consistent, atomic view of chain commitment.

### Proof of Concept
1. Client issues `getSignatureStatuses` with a large list of signatures (some old/rooted, some very recent).
2. While the request-processor loop in `get_transaction_status` ( [5](#0-4) ) is midway through iterating signatures, `AggregateCommitmentService::run` completes a root/commitment update on a background thread and swaps in a new `BlockCommitmentCache` ( [6](#0-5) ).
3. Signatures processed before the swap see the old `root()`/`highest_super_majority_root()`/optimistically-confirmed-bank view; signatures processed after the swap see the new one.
4. The client receives one JSON-RPC response containing, for two transactions confirmed at essentially the same time, differing `confirmationStatus` classifications (e.g., one `"confirmed"`, the other `"finalized"`) that do not correspond to a single, well-defined point-in-time view of the ledger, despite the batch being issued as one atomic-looking call.

### Citations

**File:** rpc/src/rpc.rs (L1672-1729)
```rust
    pub async fn get_signature_statuses(
        &self,
        signatures: Vec<Signature>,
        config: Option<RpcSignatureStatusConfig>,
    ) -> Result<RpcResponse<Vec<Option<TransactionStatus>>>> {
        let search_transaction_history = config
            .map(|x| x.search_transaction_history)
            .unwrap_or(false);
        if search_transaction_history {
            self.check_if_transaction_history_enabled()?;
        }

        let bank = self.bank(Some(CommitmentConfig::processed()));
        let mut statuses: Vec<Option<TransactionStatus>> = vec![];

        for signature in signatures {
            let status = if let Some(status) = self.get_transaction_status(signature, &bank) {
                Some(status)
            } else if search_transaction_history {
                if let Some(status) = self
                    .blockstore
                    .get_rooted_transaction_status(signature)
                    .map_err(|_| Error::internal_error())?
                    .filter(|(slot, _status_meta)| {
                        slot <= &self
                            .block_commitment_cache
                            .read()
                            .unwrap()
                            .highest_super_majority_root()
                    })
                    .map(|(slot, status_meta)| {
                        let err = status_meta.status.clone().err();
                        TransactionStatus {
                            slot,
                            status: status_meta.status,
                            confirmations: None,
                            err,
                            confirmation_status: Some(TransactionConfirmationStatus::Finalized),
                        }
                    })
                {
                    Some(status)
                } else if let Some(bigtable_ledger_storage) = &self.bigtable_ledger_storage {
                    bigtable_ledger_storage
                        .get_signature_status(&signature)
                        .await
                        .map(Some)
                        .unwrap_or(None)
                } else {
                    None
                }
            } else {
                None
            };
            statuses.push(status);
        }
        Ok(new_response(&bank, statuses))
    }
```

**File:** rpc/src/rpc.rs (L1731-1766)
```rust
    fn get_transaction_status(
        &self,
        signature: Signature,
        bank: &Bank,
    ) -> Option<TransactionStatus> {
        let (slot, status) = bank.get_signature_status_slot(&signature)?;

        let optimistically_confirmed_bank = self.bank(Some(CommitmentConfig::confirmed()));
        let optimistically_confirmed =
            optimistically_confirmed_bank.get_signature_status_slot(&signature);

        let r_block_commitment_cache = self.block_commitment_cache.read().unwrap();
        let confirmations = if r_block_commitment_cache.root() >= slot
            && is_finalized(&r_block_commitment_cache, bank, &self.blockstore, slot)
        {
            None
        } else {
            r_block_commitment_cache
                .get_confirmation_count(slot)
                .or(Some(0))
        };
        let err = status.clone().err();
        Some(TransactionStatus {
            slot,
            status,
            confirmations,
            err,
            confirmation_status: if confirmations.is_none() {
                Some(TransactionConfirmationStatus::Finalized)
            } else if optimistically_confirmed.is_some() {
                Some(TransactionConfirmationStatus::Confirmed)
            } else {
                Some(TransactionConfirmationStatus::Processed)
            },
        })
    }
```

**File:** core/src/commitment_service.rs (L120-153)
```rust
    fn run(
        receiver: &Receiver<TowerCommitmentAggregationData>,
        ag_receiver: &Receiver<AlpenglowCommitmentAggregationData>,
        block_commitment_cache: &RwLock<BlockCommitmentCache>,
        rpc_subscriptions: Option<&RpcSubscriptions>,
        exit: &AtomicBool,
    ) -> Result<(), RecvTimeoutError> {
        loop {
            if exit.load(Ordering::Relaxed) {
                return Ok(());
            }

            let mut aggregate_commitment_time = Measure::start("aggregate-commitment-ms");
            let commitment_slots = select! {
                recv(receiver) -> msg => {
                    let data = msg?;
                    let data = receiver.try_iter().last().unwrap_or(data);
                    let ancestors = data.bank.status_cache_ancestors();
                    if ancestors.is_empty() {
                        continue;
                    }
                    Self::update_commitment_cache(block_commitment_cache, data, ancestors)
                }
                recv(ag_receiver) -> msg => {
                    let data = msg?;
                    let data = ag_receiver.try_iter().last().unwrap_or(data);
                    Self::alpenglow_update_commitment_cache(
                        block_commitment_cache,
                        data.commitment_type,
                        data.slot,
                    )
                }
                default(Duration::from_secs(1)) => continue
            };
```

**File:** core/src/commitment_service.rs (L207-244)
```rust
    fn update_commitment_cache(
        block_commitment_cache: &RwLock<BlockCommitmentCache>,
        aggregation_data: TowerCommitmentAggregationData,
        ancestors: Vec<u64>,
    ) -> CommitmentSlots {
        let (block_commitment, rooted_stake) = Self::aggregate_commitment(
            &ancestors,
            &aggregation_data.bank,
            &aggregation_data.node_vote_state,
        );

        let highest_super_majority_root =
            get_highest_super_majority_root(rooted_stake, aggregation_data.total_stake);

        let mut new_block_commitment = BlockCommitmentCache::new(
            block_commitment,
            aggregation_data.total_stake,
            CommitmentSlots {
                slot: aggregation_data.bank.slot(),
                root: aggregation_data.root,
                highest_confirmed_slot: aggregation_data.root,
                highest_super_majority_root,
            },
        );
        let highest_confirmed_slot = new_block_commitment.calculate_highest_confirmed_slot();
        new_block_commitment.set_highest_confirmed_slot(highest_confirmed_slot);

        let mut w_block_commitment_cache = block_commitment_cache.write().unwrap();

        let highest_super_majority_root = max(
            new_block_commitment.highest_super_majority_root(),
            w_block_commitment_cache.highest_super_majority_root(),
        );
        new_block_commitment.set_highest_super_majority_root(highest_super_majority_root);

        *w_block_commitment_cache = new_block_commitment;
        w_block_commitment_cache.commitment_slots()
    }
```
