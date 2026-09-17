Based on my research, I found a plausible analog to the CVE-2024-0240 bug class (memory exhaustion from per-client notification state that isn't reclaimed when a client disconnects) in the transaction-pool's per-hash listener bookkeeping.

### Title
Unbounded per-transaction-hash listener leak in `SidecarListeners::by_hash` causes txpool memory exhaustion - (File: crates/execution/txpool/src/pool.rs)

### Summary
`SidecarListeners` (crates/execution/txpool/src/pool.rs) tracks per-transaction-hash subscribers in `by_hash: HashMap<TxHash, Vec<mpsc::UnboundedSender<TransactionEvent>>>` [1](#0-0) . Entries are only pruned inside `broadcast_hash_event`, which removes a dead sender (or clears the map entry) solely when that specific hash receives another event and either the send fails or the event `is_final()` [2](#0-1) . If a client subscribes to a transaction hash via `subscribe_hash` and then disconnects (or the transaction never reaches a terminal state — e.g. it sits queued forever, is never mined/discarded/replaced), the map entry for that hash is never revisited and never pruned.

### Finding Description
`subscribe_hash` pushes a new `UnboundedSender` into `by_hash[tx_hash]` for every subscription [3](#0-2) . Cleanup (`unsubscribe_hash_listener`) exists but must be explicitly invoked by the caller [4](#0-3) ; otherwise the only path that reclaims memory is `broadcast_hash_event`, which is only triggered by pool-internal events (`Queued`, `Pending`, `Replaced`, `Mined`, `Discarded`) for that exact hash [5](#0-4) . A transaction that never advances past its initial queued state (e.g. stuck behind a nonce gap, insufficient fee, or simply abandoned by its sender) produces no further hash-specific events, so a dropped subscriber's dead channel for that hash is never observed and never removed from the `HashMap`. This is directly analogous to the reported Silicon Labs bug: notification bookkeeping for subscribers accumulates and is never released once fan-out to multiple "clients" (subscribed hashes) stops producing terminal events.

### Impact Explanation
Repeatedly submitting transactions that will never reach a terminal pool state (or reusing already-queued/never-mined hashes) and subscribing/disconnecting against each one grows `by_hash` without bound. Because every pool worker path (`on_inserted`, `on_mined`, `on_discarded`) locks `self.listeners.write()` [6](#0-5)  to update this same structure, sustained growth increases per-block/per-transaction processing cost and eventually memory pressure on the node process, consistent with the reported bug class (memory exhaustion that stops normal operation) — here that would materialize as txpool/node resource exhaustion, a Medium/High severity DoS depending on achievable growth rate.

### Likelihood Explanation
Reachability requires only that an unprivileged actor can (a) submit ordinary transactions to the pool and (b) subscribe to per-hash transaction events and disconnect before a terminal event fires. I was not able to fully verify, within the available tool budget, which RPC/audit surface (`crates/infra/audit/src/transaction_events.rs`, `crates/infra/audit/src/rpc.rs`) drives `subscribe_hash`/`unsubscribe_hash_listener`, nor whether a `Drop` implementation on the returned `TransactionEvents` guarantees `unsubscribe_hash_listener` is always called on disconnect. This is a material gap: if `TransactionEvents` has a `Drop` impl that reliably calls `unsubscribe_hash_listener`, the leak would only occur for transactions whose subscription outlives the pool object without being dropped (less likely), substantially reducing severity. I could not confirm or rule this out with confidence.

### Recommendation
- Verify whether `TransactionEvents` (returned by `subscribe_hash`) implements `Drop` to call `unsubscribe_hash_listener`; if not, add it so subscriber cleanup does not depend on a future hash-specific event.
- Add a periodic sweep (similar to `expire_due_buckets`/`expire_by_block`) that prunes `by_hash` entries whose senders are all closed, independent of new events for that hash.
- Add a metric/gauge for `by_hash.len()` and total listener count to detect unbounded growth in production, mirroring `GuardMetrics::tracked()` usage nearby [7](#0-6) .

### Proof of Concept
1. Repeatedly craft transactions that will remain queued indefinitely (e.g., nonce far ahead of the account's current nonce, or fee just below the pool's promotion threshold) and submit them to the pool.
2. For each transaction hash, open a subscription via the hash-specific transaction-events API (backed by `subscribe_hash`), then immediately close the connection without waiting for a terminal event.
3. Because the transaction never reaches `Mined`/`Discarded`/`Replaced`, `broadcast_hash_event` is never called again for that hash, so the dead `UnboundedSender` (and the `by_hash` map entry) is retained forever.
4. Repeat at scale (many distinct hashes) to grow `SidecarListeners::by_hash` unbounded, exhausting node memory over time — I was unable to execute this PoC end-to-end to confirm the exact growth rate or exposed RPC method name due to tool-call limits.

### Citations

**File:** crates/execution/txpool/src/pool.rs (L1516-1522)
```rust
            let mut listeners = self.listeners.write();
            if !pruned.removed.is_empty() {
                listeners.on_mined(&pruned.removed, block_hash);
            }
            if !expired.is_empty() {
                listeners.on_discarded(&expired);
            }
```

**File:** crates/execution/txpool/src/pool.rs (L1532-1536)
```rust
        self.expire_due_buckets(now);
        self.expire_by_block(block_number);
        self.reconcile_guard();
        GuardMetrics::tracked().set(self.guard.read().len() as f64);
    }
```

**File:** crates/execution/txpool/src/pool.rs (L1588-1596)
```rust
#[derive(Debug)]
struct SidecarListeners<T: BasePooledTx> {
    by_hash: HashMap<TxHash, Vec<mpsc::UnboundedSender<TransactionEvent>>>,
    all_events: Vec<mpsc::Sender<FullTransactionEvent<T>>>,
    pending_all: Vec<mpsc::Sender<TxHash>>,
    pending_propagate: Vec<mpsc::Sender<TxHash>>,
    new_all: Vec<mpsc::Sender<NewTransactionEvent<T>>>,
    new_propagate: Vec<mpsc::Sender<NewTransactionEvent<T>>>,
}
```

**File:** crates/execution/txpool/src/pool.rs (L1611-1619)
```rust
impl<T: BasePooledTx> SidecarListeners<T> {
    fn subscribe_hash(
        &mut self,
        tx_hash: TxHash,
    ) -> (TransactionEvents, mpsc::UnboundedSender<TransactionEvent>) {
        let (tx, rx) = mpsc::unbounded_channel();
        self.by_hash.entry(tx_hash).or_default().push(tx.clone());
        (TransactionEvents::new(tx_hash, rx), tx)
    }
```

**File:** crates/execution/txpool/src/pool.rs (L1621-1633)
```rust
    fn unsubscribe_hash_listener(
        &mut self,
        tx_hash: &TxHash,
        listener: &mpsc::UnboundedSender<TransactionEvent>,
    ) {
        let Some(listeners) = self.by_hash.get_mut(tx_hash) else {
            return;
        };
        listeners.retain(|candidate| !candidate.same_channel(listener));
        if listeners.is_empty() {
            self.by_hash.remove(tx_hash);
        }
    }
```

**File:** crates/execution/txpool/src/pool.rs (L1664-1708)
```rust
    fn on_inserted(&mut self, nonce_pool: &TwoDNoncePool<T>, outcome: &InsertOutcome<T>) {
        let hash = outcome.outcome.hash;
        let Some(transaction) = nonce_pool.get(&hash) else {
            return;
        };

        if let Some(replaced) = &outcome.replaced {
            self.broadcast_hash_event(replaced.hash(), TransactionEvent::Replaced(hash));
            self.broadcast_all(FullTransactionEvent::Replaced {
                transaction: Arc::clone(replaced),
                replaced_by: hash,
            });
        }

        match &outcome.outcome.state {
            AddedTransactionState::Pending => {
                self.broadcast_pending_transaction(&transaction);
            }
            AddedTransactionState::Queued(reason) => {
                self.broadcast_hash_event(&hash, TransactionEvent::Queued);
                self.broadcast_all(FullTransactionEvent::Queued(hash, Some(reason.clone())));
                self.broadcast_new(NewTransactionEvent { subpool: SubPool::Queued, transaction });
            }
        }

        for promoted in &outcome.promoted {
            self.broadcast_pending_transaction(promoted);
        }
    }

    fn on_mined(&mut self, transactions: &[Arc<ValidPoolTransaction<T>>], block_hash: B256) {
        for transaction in transactions {
            let hash = *transaction.hash();
            self.broadcast_hash_event(&hash, TransactionEvent::Mined(block_hash));
            self.broadcast_all(FullTransactionEvent::Mined { tx_hash: hash, block_hash });
        }
    }

    fn on_discarded(&mut self, transactions: &[Arc<ValidPoolTransaction<T>>]) {
        for transaction in transactions {
            let hash = *transaction.hash();
            self.broadcast_hash_event(&hash, TransactionEvent::Discarded);
            self.broadcast_all(FullTransactionEvent::Discarded(hash));
        }
    }
```

**File:** crates/execution/txpool/src/pool.rs (L1710-1718)
```rust
    fn broadcast_hash_event(&mut self, tx_hash: &TxHash, event: TransactionEvent) {
        let Some(listeners) = self.by_hash.get_mut(tx_hash) else {
            return;
        };
        listeners.retain(|listener| listener.send(event.clone()).is_ok() && !event.is_final());
        if listeners.is_empty() {
            self.by_hash.remove(tx_hash);
        }
    }
```
