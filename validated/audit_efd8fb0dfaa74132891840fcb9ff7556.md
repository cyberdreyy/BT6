### Title
Unbounded linear scan over global EIP-8130 nonce-lane map in per-sender txpool lookups enables mempool-wide DoS - (File: crates/execution/txpool/src/two_d_nonce_pool.rs)

### Summary
The `TwoDNoncePool` sidecar that backs EIP-8130 channelized-nonce transactions stores every sender's nonce lanes in one flat `HashMap<LaneId, NonceLane<T>>` (`lanes`), where `LaneId = (Address, U256)` [1](#0-0) . Any per-sender lookup — `transactions_by_sender`, `pending_transactions_by_sender`, `queued_transactions_by_sender` — does a linear scan over the *entire* `lanes` map (all senders, not just the queried one) to filter matching entries [2](#0-1) . These are exposed through the pool's `TransactionPool` trait implementation (`get_transactions_by_sender`, `get_pending_transactions_by_sender`, `get_queued_transactions_by_sender`) [3](#0-2) .

### Finding Description
This is structurally the same bug class as the RabbitHole `Quest.claim` DoS: a function whose cost is proportional to a shared, unboundedly-growing on-chain/in-memory collection, rather than to the caller's own data. In RabbitHole, `RabbitHoleReceipt.getOwnedTokenIdsOfQuest` scanned a caller's full token balance; here, `transactions_by_sender`/`pending_transactions_by_sender`/`queued_transactions_by_sender` scan the *global* `lanes` HashMap — populated by every unprivileged sender using EIP-8130 channelized nonces (each distinct `(sender, nonce_key)` pair creates a new lane entry) — even though the caller only wants entries for one address.

Base's own EIP-8130 admission policy is explicitly bounded per sender/payer (`mempool-sender-limit`, `mempool-payer-limit`, default caps referenced in `crates/execution/node/src/args.rs`) [4](#0-3) , implying these per-sender lookups are invoked during transaction admission/validation to enforce inflight-transaction caps. Because the scan cost is `O(total lanes across all senders)` rather than `O(lanes for this sender)`, any unprivileged transaction sender can cheaply grow the shared `lanes` map by submitting many low-cost transactions with distinct `nonce_key` values (EIP-8130 channelized nonces are permissionless to create), and every subsequent per-sender admission check for *any* sender pays the cost of scanning the whole inflated map.

### Impact Explanation
As the total number of open nonce lanes grows (driven by ordinary, permissionless EIP-8130 usage or by an attacker deliberately opening many idle lanes), the per-transaction admission cost for the entire mempool degrades from O(1)-ish to O(N) per lookup, and repeated lookups across concurrent tx validation make aggregate admission work effectively quadratic in the number of lanes. This raises CPU/latency costs for legitimate transaction admission for all senders — a resource-exhaustion/performance-degradation DoS on the txpool admission path, one of the explicitly in-scope reachable surfaces ("txpool admission", "EIP-8130 authorization", "nonce and fee accounting"). It does not directly cause fund loss but can degrade the node's ability to admit/serve transactions in a timely manner under load, satisfying the "node halt"/DoS-adjacent impact bar loosely, though it stops short of a hard node halt.

### Likelihood Explanation
Likelihood is moderate: exploitation requires no special privilege — any account can submit transactions with distinct EIP-8130 `nonce_key` values to create many lanes — but the actual severity depends on unverified details this analysis could not confirm from the index: (1) exactly which code path invokes `pending_transactions_by_sender`/`queued_transactions_by_sender` for `mempool-sender-limit`/`mempool-payer-limit` enforcement, and how frequently it runs relative to lane-map growth, and (2) whether the total `lanes` map size is otherwise capped (e.g., total pool size limits) that would bound the worst case. These call sites and any global cap were not located within the indexed portion of the codebase.

### Recommendation
Index nonce lanes by sender (e.g., `HashMap<Address, HashMap<U256, NonceLane<T>>>` or a secondary `Address -> Vec<LaneId>` index) so per-sender lookups are O(lanes for that sender) instead of O(total lanes). Alternatively, maintain a running per-sender lane count/set as an auxiliary index used specifically by the admission-limit checks, avoiding any full-map scan for enforcing `mempool-sender-limit`/`mempool-payer-limit`.

### Proof of Concept
Not independently reproducible from the indexed code alone — a concrete PoC would need to (1) confirm the exact call site enforcing `mempool-sender-limit`/`mempool-payer-limit` invokes one of the affected `*_by_sender` methods, and (2) benchmark admission latency as `lanes.len()` grows by submitting transactions with many distinct EIP-8130 `nonce_key` values, since a Devin session with full repo/build access would be required to confirm and measure this end-to-end.

### Citations

**File:** crates/execution/txpool/src/two_d_nonce_pool.rs (L24-30)
```rust
type LaneId = (Address, U256);

#[derive(Debug)]
struct NonceLane<T: BasePooledTx> {
    next_nonce: u64,
    transactions: BTreeMap<u64, Arc<ValidPoolTransaction<T>>>,
}
```

**File:** crates/execution/txpool/src/two_d_nonce_pool.rs (L172-205)
```rust
    /// Returns transactions for the given sender.
    pub(crate) fn transactions_by_sender(
        &self,
        sender: Address,
    ) -> Vec<Arc<ValidPoolTransaction<T>>> {
        let mut transactions = Vec::new();
        for ((lane_sender, _), lane) in &self.lanes {
            if *lane_sender == sender {
                transactions.extend(lane.live_transactions().cloned());
            }
        }
        transactions.extend(
            self.nonce_free.values().filter(|transaction| transaction.sender() == sender).cloned(),
        );
        transactions
    }

    /// Returns pending transactions for the given sender.
    pub(crate) fn pending_transactions_by_sender(
        &self,
        sender: Address,
    ) -> Vec<Arc<ValidPoolTransaction<T>>> {
        let mut transactions: Vec<_> = self
            .lanes
            .iter()
            .filter(|((lane_sender, _), _)| *lane_sender == sender)
            .flat_map(|(_, lane)| lane.consecutive_pending_transactions())
            .cloned()
            .collect();
        transactions.extend(
            self.nonce_free.values().filter(|transaction| transaction.sender() == sender).cloned(),
        );
        transactions
    }
```

**File:** crates/execution/txpool/src/pool.rs (L1282-1323)
```rust
    fn get_transactions_by_sender(
        &self,
        sender: Address,
    ) -> Vec<Arc<ValidPoolTransaction<Self::Transaction>>> {
        let mut transactions = self.protocol_pool.get_transactions_by_sender(sender);
        transactions.extend(self.nonce_pool.read().transactions_by_sender(sender));
        transactions
    }

    fn get_pending_transactions_with_predicate(
        &self,
        mut predicate: impl FnMut(&ValidPoolTransaction<Self::Transaction>) -> bool,
    ) -> Vec<Arc<ValidPoolTransaction<Self::Transaction>>> {
        let mut transactions =
            self.protocol_pool.get_pending_transactions_with_predicate(&mut predicate);
        transactions.extend(
            self.nonce_pool
                .read()
                .pending_transactions()
                .into_iter()
                .filter(|transaction| predicate(transaction)),
        );
        transactions
    }

    fn get_pending_transactions_by_sender(
        &self,
        sender: Address,
    ) -> Vec<Arc<ValidPoolTransaction<Self::Transaction>>> {
        let mut transactions = self.protocol_pool.get_pending_transactions_by_sender(sender);
        transactions.extend(self.nonce_pool.read().pending_transactions_by_sender(sender));
        transactions
    }

    fn get_queued_transactions_by_sender(
        &self,
        sender: Address,
    ) -> Vec<Arc<ValidPoolTransaction<Self::Transaction>>> {
        let mut transactions = self.protocol_pool.get_queued_transactions_by_sender(sender);
        transactions.extend(self.nonce_pool.read().queued_transactions_by_sender(sender));
        transactions
    }
```

**File:** crates/execution/node/src/args.rs (L381-387)
```rust
    /// Maximum inflight EIP-8130 transactions per non-locked sender account.
    #[arg(long = "rollup.mempool-sender-limit", default_value_t = DEFAULT_SIGNATURE_LIMIT)]
    pub mempool_sender_limit: u32,

    /// Maximum inflight EIP-8130 transactions per count-limited payer account.
    #[arg(long = "rollup.mempool-payer-limit", default_value_t = DEFAULT_PAYMENT_LIMIT)]
    pub mempool_payer_limit: u32,
```
