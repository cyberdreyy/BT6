### Title
Unbounded per-log/per-transaction WebSocket message amplification in flashblocks `eth_subscribe` streams enables attacker-triggered event-flood DoS - (File: crates/execution/flashblocks/src/rpc/pubsub.rs)

### Summary
The Xen CVE-2021-28712 class of bug is "a rogue actor can force the victim to process events at an attacker-controlled high frequency, exhausting CPU time servicing them and starving other work." Base's flashblocks pub-sub RPC (`pendingLogs` and `newFlashblockTransactions`) has an analogous amplification path: every log/transaction produced by a single sequenced transaction is fanned out into one discrete WebSocket message per item, with no batching, coalescing, or per-connection rate limiting, so a transaction sender fully controls the number of messages the node must serialize and push to every subscribed client.

### Finding Description
`EthPubSub::pending_logs_stream` and `EthPubSub::new_flashblock_transactions_hash_stream` / `..._full_stream` / `..._filtered_stream` take the logs/transactions produced by the latest flashblock and `flat_map` them through `stream::iter`, explicitly turning each log or transaction into its own separate stream item ("one log per WebSocket message", "one hash per WebSocket message"): [1](#0-0) [2](#0-1) 

`pipe_from_stream` then loops, and for every yielded item it serializes a `SubscriptionMessage` and performs an async `sink.send(msg).await` before pulling the next item: [3](#0-2) 

The upstream data source, `PendingBlocks::get_latest_flashblock_logs`, imposes no cap on the number of logs returned per flashblock — it simply iterates every transaction/receipt in the latest flashblock and every log in each receipt that matches the filter: [4](#0-3) 

A standard EVM transaction can emit an arbitrarily large number of `LOG0`-`LOG4` events, bounded only by the block gas limit (tens of thousands of logs are feasible within a single 30M-gas transaction). Because the sequencer includes ordinary user transactions into flashblocks every ~250ms (`flashblocks_block_time`, default 250ms) and there is no per-message batching, cap, or per-connection throttling in the pub-sub pipeline, an unprivileged transaction sender can force the node to serialize and push tens of thousands of individual WebSocket frames to every `pendingLogs`/`newFlashblockTransactions` subscriber from a single transaction.

### Impact Explanation
This is directly analogous to the Xen advisory's "malicious backend forces the victim to service an unbounded volume of events, elongating processing time and denying service." Here, any transaction sender (not a privileged actor) can force the flashblocks RPC service to spend outsized CPU/bandwidth serializing and flushing per-item WebSocket messages for every subscribed client, potentially stalling the async runtime tasks that service other RPC/WebSocket clients and delaying delivery of legitimate pending-state/log data — a node-level Medium-severity DoS on the public WebSocket subscription path, consistent with the CVSS 6.5 (availability-impact) rating of the analog.

### Likelihood Explanation
High likelihood: any unprivileged account can deploy a contract that emits a very large number of events and submit a single transaction invoking it; no special privileges, precompile access, or protocol-level gating is required, and the amplification is deterministic given the existing per-item fan-out design (`stream::iter` + `pipe_from_stream`).

### Recommendation
Batch/aggregate logs and transactions from a single flashblock into fewer WebSocket messages (e.g., one message per flashblock containing an array, matching how `newFlashblocks` already ships whole blocks rather than per-tx items), and/or apply a per-subscription cap and backpressure/rate limiting on the number of items emitted per flashblock interval so that a single heavy transaction cannot force unbounded per-item serialization/send work against every connected subscriber.

### Proof of Concept
1. Deploy a contract whose function emits a large number of `LOG` events in a loop, sized to consume close to the block gas limit (tens of thousands of `LOG0` emissions are feasible).
2. Connect a WebSocket client and issue `eth_subscribe(["pendingLogs", {}])` (or `newFlashblockTransactions`).
3. Submit the log-heavy transaction; as the sequencer includes it in the next flashblock, observe the node emit one WebSocket message per matching log (per `pending_logs_stream`/`pipe_from_stream` semantics) rather than a single batched update, and measure the serialization/send latency and CPU spent servicing this one flashblock versus a baseline block with few logs — repeated submissions at the flashblock cadence can sustain this cost.

### Citations

**File:** crates/execution/flashblocks/src/rpc/pubsub.rs (L99-128)
```rust
    /// Returns a stream that yields individual logs from only the latest flashblock matching the
    /// filter.
    ///
    /// Each matching log is emitted as a separate stream item (one log per WebSocket message).
    /// Only logs from the most recent flashblock are emitted to avoid duplicates.
    fn pending_logs_stream(flashblocks_state: Arc<FB>, filter: Filter) -> impl Stream<Item = Log>
    where
        FB: FlashblocksAPI + Send + Sync + 'static,
    {
        futures::StreamExt::flat_map(
            StreamExt::filter_map(
                BroadcastStream::new(flashblocks_state.subscribe_to_flashblocks()),
                move |result| {
                    let pending_blocks = match result {
                        Ok(blocks) => blocks,
                        Err(err) => {
                            error!(
                                message = "Error in flashblocks stream for pending logs",
                                error = %err
                            );
                            return None;
                        }
                    };
                    let logs = pending_blocks.get_latest_flashblock_logs(&filter);
                    if logs.is_empty() { None } else { Some(logs) }
                },
            ),
            stream::iter,
        )
    }
```

**File:** crates/execution/flashblocks/src/rpc/pubsub.rs (L196-226)
```rust
    /// Returns a stream that yields individual transaction hashes from only the latest flashblock.
    ///
    /// Each hash is emitted as a separate stream item (one hash per WebSocket message).
    /// Only hashes from the most recent flashblock are emitted to avoid duplicates.
    fn new_flashblock_transactions_hash_stream(
        flashblocks_state: Arc<FB>,
    ) -> impl Stream<Item = B256>
    where
        FB: FlashblocksAPI + Send + Sync + 'static,
    {
        futures::StreamExt::flat_map(
            StreamExt::filter_map(
                BroadcastStream::new(flashblocks_state.subscribe_to_flashblocks()),
                |result| {
                    let pending_blocks = match result {
                        Ok(blocks) => blocks,
                        Err(err) => {
                            error!(
                                message = "Error in flashblocks stream for transaction hashes",
                                error = %err
                            );
                            return None;
                        }
                    };
                    let hashes = pending_blocks.get_latest_flashblock_transaction_hashes();
                    if hashes.is_empty() { None } else { Some(hashes) }
                },
            ),
            stream::iter,
        )
    }
```

**File:** crates/execution/flashblocks/src/rpc/pubsub.rs (L317-356)
```rust
async fn pipe_from_stream<T, St>(sink: SubscriptionSink, mut stream: St)
where
    St: Stream<Item = T> + Unpin,
    T: Serialize,
{
    loop {
        tokio::select! {
            // dropped by client
            _ = sink.closed() => return,

            maybe_item = stream.next() => {
                // stream ended
                let Some(item) = maybe_item else {
                    return;
                };

                let msg = match SubscriptionMessage::new(
                    sink.method_name(),
                    sink.subscription_id(),
                    &item
                ) {
                    Ok(msg) => msg,
                    Err(err) => {
                        error!(
                            target: "flashblocks_rpc::pubsub",
                            %err,
                            "Failed to serialize subscription message"
                        );
                        return;
                    }
                };

                // if it fails, client disconnected
                if sink.send(msg).await.is_err() {
                    return;
                }
            }
        }
    }
}
```

**File:** crates/execution/flashblocks/src/pending_blocks.rs (L706-726)
```rust
    /// Returns logs matching the filter from only the latest flashblock (delta).
    ///
    /// Unlike `get_pending_logs`, this returns only logs from transactions
    /// that were added in the most recent flashblock, avoiding duplicates
    /// when streaming via WebSocket subscriptions.
    pub fn get_latest_flashblock_logs(&self, filter: &Filter) -> Vec<Log> {
        let prev_count = self.previous_flashblocks_tx_count();
        let mut logs = Vec::new();

        for tx in self.transactions.iter().skip(prev_count) {
            if let Some(receipt) = self.transaction_receipts.get(&tx.tx_hash()) {
                for log in receipt.inner.logs() {
                    if filter.matches(log.as_ref()) {
                        logs.push(log.inner.clone());
                    }
                }
            }
        }

        logs
    }
```
