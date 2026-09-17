### Title
Lane head cursor never advances on non-mined removal of the head transaction, permanently stalling an EIP-8130 nonce channel - (File: crates/execution/txpool/src/two_d_nonce_pool.rs)

### Summary
`TwoDNoncePool::remove_hash` mirrors the reported `queue::remove` bug class: when a node (here, a channelized EIP-8130 transaction) is removed from the lane's `transactions` map, the lane's head cursor (`next_nonce`) is only advanced if the caller explicitly passes `advance_lane = true`. That flag is only set by `prune_mined`. Every other removal path (`remove_transactions`, `remove_transactions_and_descendants`) calls `remove_hash(hash, false)`, so if the transaction removed happens to be exactly the lane's current head (`nonce == lane.next_nonce`), the entry disappears from `transactions` but `next_nonce` is left pointing at that now-nonexistent nonce.

### Finding Description
`NonceLane` tracks per-`(sender, nonce_key)` "lane" state with a `next_nonce` head cursor and a `BTreeMap<u64, Tx>` of buffered transactions [1](#0-0) . Pending/queued classification is derived purely from whether the live transactions form a consecutive run starting at `next_nonce`: [2](#0-1) 

`remove_hash` is the single removal primitive for lane entries. It removes the map entry unconditionally, but only bumps `next_nonce` when `advance_lane` is true *and* the removed nonce equals the current head: [3](#0-2) 

`prune_mined` (the only caller that passes `advance_lane = true`) is meant for transactions that were actually included in a block, so advancing the head there is correct [4](#0-3) . However, `remove_transactions` and `remove_transactions_and_descendants` — used for ordinary pool maintenance such as discarding invalid/underpriced/evicted transactions, or explicit removal by hash/sender — always call `remove_hash(hash, false)`: [5](#0-4) 

If the transaction being discarded through one of these non-mined paths is exactly the current lane head (`nonce == next_nonce`) — for example a transaction invalidated during revalidation, evicted for being underpriced, or removed via `remove_transactions_by_sender` — the map entry is deleted but `next_nonce` is not advanced. The lane is left permanently expecting a nonce that will never again be inserted at that position (the sender would have to resubmit the exact same nonce, which the validator will treat as a fresh, disconnected entry). `consecutive_pending_transactions`/`live_transactions` filter strictly on `next_nonce`, so every transaction already buffered above the stale head, and every future transaction submitted for that channel, is permanently misclassified as "queued" and never promoted to "pending", exactly analogous to the reported `queue::remove` defect where the tail pointer is left dangling after a node removal and subsequent iteration/operations on the structure become incorrect.

### Impact Explanation
This is reachable from ordinary, unprivileged txpool admission activity: an EIP-8130 channel sender's own head-of-lane transaction can be discarded through a non-mined path (e.g., becoming underpriced/invalidated during revalidation, or explicit/administrative removal by hash or by sender) without going through `prune_mined`. Once that happens, `lane.next_nonce` desyncs from reality and the affected nonce-channel lane is permanently stuck: none of the sender's subsequently submitted, otherwise-valid channel transactions for that lane can ever be classified as pending again, since the "pending" gate requires an unbroken run starting at the stale `next_nonce`. This is a permanent freezing of the sender's ability to use that EIP-8130 channel — a concrete resource/availability impact on the txpool's core sequencing invariant for the affected lane, not a merely cosmetic issue, since it silently and irreversibly blocks transaction inclusion for that channel.

### Likelihood Explanation
Triggering requires only ordinary pool activity that any unprivileged sender can cause on their own channel: submitting a transaction that is later found invalid/underpriced/evicted while it is still the lane head, or an operator/maintenance flow issuing `remove_transactions`/`remove_transactions_by_sender` on that hash. No privileged access or coordination with block builders/sequencers is required — a sender can trigger this against their own lane through normal validation-failure or eviction paths.

### Recommendation
In `remove_hash`, advance (or otherwise repair) `lane.next_nonce` whenever the removed transaction's nonce equals the current `next_nonce`, regardless of the `advance_lane` flag's original mined/non-mined distinction — or, if non-mined removal of the head must instead re-open the slot rather than skip it, explicitly document and enforce that invariant so `consecutive_pending_transactions` cannot desync from the map's actual contents. At minimum, add a lane-consistency invariant check/test ensuring `next_nonce` always corresponds to either an existing entry or an empty lane after any removal path.

### Proof of Concept
1. Create a channel lane for `sender` at `nonce_key = k`; insert nonce `0` (head) and nonce `1`, both admitted via `insert_validated` (as in `pruning_mined_head_promotes_next_sequence_in_lane` test setup) [6](#0-5) .
2. Instead of the nonce-0 tx being mined (which would go through `prune_mined`), have it be discarded through a non-mined path, e.g. call `pool.remove_transactions(&[nonce0_hash])`, which internally calls `remove_hash(hash, false)` [7](#0-6) .
3. Observe `lane.next_nonce` remains `0` even though nonce `0` no longer exists in `lane.transactions`.
4. Query `pending_transactions()`/`pending_and_queued_txn_count()`: the previously-pending nonce-1 transaction is now reported as queued forever (since `live_transactions()` starts ranging from `next_nonce = 0`, which is absent, so the "expected == actual" check in `consecutive_pending_transactions` immediately fails at offset 0) [2](#0-1) , and no future nonce submitted to this lane can become pending again short of resubmitting nonce `0`.

### Citations

**File:** crates/execution/txpool/src/two_d_nonce_pool.rs (L26-36)
```rust
#[derive(Debug)]
struct NonceLane<T: BasePooledTx> {
    next_nonce: u64,
    transactions: BTreeMap<u64, Arc<ValidPoolTransaction<T>>>,
}

impl<T: BasePooledTx> Default for NonceLane<T> {
    fn default() -> Self {
        Self { next_nonce: 0, transactions: BTreeMap::new() }
    }
}
```

**File:** crates/execution/txpool/src/two_d_nonce_pool.rs (L39-62)
```rust
    fn live_transactions(&self) -> impl Iterator<Item = &Arc<ValidPoolTransaction<T>>> {
        self.transactions.range(self.next_nonce..).map(|(_, transaction)| transaction)
    }

    fn consecutive_pending_transactions(
        &self,
    ) -> impl Iterator<Item = &Arc<ValidPoolTransaction<T>>> {
        self.live_transactions()
            .enumerate()
            .take_while(|(offset, transaction)| {
                self.next_nonce
                    .checked_add(*offset as u64)
                    .is_some_and(|expected| transaction.nonce() == expected)
            })
            .map(|(_, transaction)| transaction)
    }

    fn consecutive_pending_len(&self) -> usize {
        self.consecutive_pending_transactions().count()
    }

    fn queued_transactions(&self) -> impl Iterator<Item = &Arc<ValidPoolTransaction<T>>> {
        self.live_transactions().skip(self.consecutive_pending_len())
    }
```

**File:** crates/execution/txpool/src/two_d_nonce_pool.rs (L353-404)
```rust
    /// Removes the exact transactions by hash without advancing lane state.
    pub(crate) fn remove_transactions(
        &mut self,
        hashes: &[TxHash],
    ) -> Vec<Arc<ValidPoolTransaction<T>>> {
        let mut removed = Vec::new();
        for hash in hashes {
            if let Some(transaction) = self.remove_hash(*hash, false) {
                removed.push(transaction);
            }
        }
        removed
    }

    /// Removes transactions and their descendants for each hash.
    pub(crate) fn remove_transactions_and_descendants(
        &mut self,
        hashes: &[TxHash],
    ) -> Vec<Arc<ValidPoolTransaction<T>>> {
        let mut removed = Vec::new();
        for hash in hashes {
            if self
                .hashes
                .get(hash)
                .is_some_and(|transaction| transaction.transaction.eip8130_replay_id().is_some())
            {
                if let Some(transaction) = self.remove_hash(*hash, false) {
                    removed.push(transaction);
                }
                continue;
            }
            let Some(transaction) = self.hashes.get(hash) else {
                continue;
            };
            let Some(nonce_key) = transaction.transaction.eip8130_nonce_channel_key() else {
                continue;
            };
            let lane_id = (transaction.sender(), nonce_key);
            let nonce = transaction.nonce();
            let Some(lane) = self.lanes.get(&lane_id) else {
                continue;
            };

            let descendant_hashes: Vec<_> = lane
                .transactions
                .range(nonce..)
                .map(|(_, transaction)| *transaction.hash())
                .collect();
            removed.extend(self.remove_transactions(&descendant_hashes));
        }
        removed
    }
```

**File:** crates/execution/txpool/src/two_d_nonce_pool.rs (L406-436)
```rust
    /// Prunes mined transactions and advances the matching lane heads.
    pub(crate) fn prune_mined(&mut self, hashes: &[TxHash]) -> PruneMinedOutcome<T> {
        let mut removed = Vec::new();
        for hash in hashes {
            if self
                .hashes
                .get(hash)
                .is_some_and(|transaction| transaction.transaction.eip8130_replay_id().is_some())
                && let Some(transaction) = self.remove_hash(*hash, false)
            {
                removed.push(transaction);
            }
        }
        let mut ordered_hashes: Vec<_> = hashes
            .iter()
            .filter_map(|hash| {
                let transaction = self.hashes.get(hash)?;
                let nonce_key = transaction.transaction.eip8130_nonce_channel_key()?;
                Some((transaction.sender(), nonce_key, transaction.nonce(), *hash))
            })
            .collect();
        ordered_hashes.sort_unstable();

        for (_, _, _, hash) in ordered_hashes {
            if let Some(transaction) = self.remove_hash(hash, true) {
                removed.push(transaction);
            }
        }

        PruneMinedOutcome { removed }
    }
```

**File:** crates/execution/txpool/src/two_d_nonce_pool.rs (L482-515)
```rust
    fn remove_hash(
        &mut self,
        hash: TxHash,
        advance_lane: bool,
    ) -> Option<Arc<ValidPoolTransaction<T>>> {
        if let Some(transaction) = self.hashes.get(&hash)
            && let Some(replay_id) = transaction.transaction.eip8130_replay_id()
        {
            let transaction = self.nonce_free.remove(&replay_id)?;
            self.hashes.remove(&hash);
            return Some(transaction);
        }
        let transaction = self.hashes.get(&hash)?;
        let nonce_key = transaction.transaction.eip8130_nonce_channel_key()?;
        let lane_id = (transaction.sender(), nonce_key);
        let nonce = transaction.nonce();
        let transaction = {
            let lane = self.lanes.get_mut(&lane_id)?;
            let transaction = lane.transactions.remove(&nonce)?;
            if advance_lane
                && nonce == lane.next_nonce
                && let Some(next_nonce) = lane.next_nonce.checked_add(1)
            {
                lane.next_nonce = next_nonce;
            }
            transaction
        };

        if self.lanes.get(&lane_id).is_some_and(|lane| lane.transactions.is_empty()) {
            self.lanes.remove(&lane_id);
        }
        self.hashes.remove(&hash);
        Some(transaction)
    }
```

**File:** crates/execution/txpool/src/two_d_nonce_pool.rs (L1001-1019)
```rust
    #[test]
    fn pruning_mined_head_promotes_next_sequence_in_lane() {
        let mut pool = TwoDNoncePool::new(PriceBumpConfig::default());
        let signer = signer();

        let head = valid_pool_transaction(signed_channel_tx(&signer, U256::from(3), 0, 1_000));
        let head_hash = *head.hash();
        let queued = valid_pool_transaction(signed_channel_tx(&signer, U256::from(3), 1, 900));
        let queued_hash = *queued.hash();

        pool.insert_validated(head, 0).unwrap();
        pool.insert_validated(queued, 0).unwrap();

        let (pending, queued_count) = pool.pending_and_queued_txn_count();
        assert_eq!((pending, queued_count), (2, 0));
        assert_eq!(
            pool.pending_transactions().into_iter().map(|tx| *tx.hash()).collect::<Vec<_>>(),
            vec![head_hash, queued_hash]
        );
```
