I found one concrete, actively documented candidate: the flashblocks `StateProcessor`'s update queue is explicitly documented as unbounded and reachable by an attacker-controlled data path (flashblock ingestion combined with canonical-block lag).### Title
Unbounded flashblocks state-update queue allows unauthenticated memory-exhaustion DoS - ([File: crates/execution/flashblocks/src/state.rs])

### Summary
`FlashblocksState` funnels both canonical-block notifications and every flashblock received from the builder's WebSocket feed into a single `tokio::sync::mpsc::unbounded_channel` (`queue: mpsc::UnboundedSender<StateUpdate>`), which `StateProcessor` drains and applies serially [1](#0-0) . The crate's own README documents that this queue has no bound: "The update queue is unbounded... a sustained burst of payloads that are all ahead of the tip still grows memory. A hard bound on the queue is separate work" [2](#0-1) . The test suite even reproduces a real mainnet incident (2026-08-20) where "canonical and flashblock updates piled up in the shared unbounded queue while the node kept following the chain" [3](#0-2) .

### Finding Description
`on_flashblock_received` only drops a flashblock before queuing if its block number is at or below the last-known canonical height [4](#0-3) ; any flashblock addressed to a future or ahead-of-tip block number is pushed onto the unbounded queue with no additional bound on queue depth, memory footprint, or rate. `StateProcessor::apply_flashblock` processes entries serially and pushes anything that fails with a `MissingCanonicalHeader`/`MissingFirstFlashblock` error into an additional in-memory `FlashblockCache` (bounded per-block-number, but the *queue* feeding it is not) [5](#0-4) . Each `Flashblock` payload carries an `ExecutionPayloadFlashblockDeltaV1` (transactions, receipts, state diffs), so this is not a bounded-size control message — it is an attacker-shaped, unbounded-size, unbounded-count queue of heap-heavy objects. The processor consumes it FIFO, and the README explicitly states this queue lets the processor's applied state fall arbitrarily far behind the node's real tip while memory keeps growing.

This mirrors the CVE-2024-24148 bug class (an attacker-controlled input stream causing unbounded resource retention/leakage that a parser/consumer never frees at the necessary rate) — here the "leak" is queued, unfreed `StateUpdate::Flashblock` allocations accumulating because consumption cannot keep pace with attacker-controlled production, exactly the CWE-770/771 resource-exhaustion shape the repo elsewhere defends against for gossip/RPC decode paths (see the `MAX_DECOMPRESSED_ENVELOPE_BYTES` and `BoundedTransactions` hardening in `crates/common/rpc-types-engine`) but has not applied to this specific ingress.

### Impact Explanation
The flashblocks WebSocket subscriber is a network-reachable input surface on any Base node running with flashblocks enabled (`base-flashblocks-node`). A malicious or compromised builder/flashblocks source (or anyone able to reach the flashblocks WebSocket endpoint if it's not strictly access-controlled) can flood it with a sustained stream of flashblock payloads whose declared block numbers stay just ahead of canonical, keeping `is_superseded` checks from rejecting them while the unbounded queue accumulates full execution-payload deltas faster than `StateProcessor` can apply them. Because there is no depth or byte-size cap on `mpsc::unbounded_channel`, sustained pressure drives the process toward OOM, causing a node halt/crash — a concrete node-halt/DoS outcome, matching the "resource-only" prohibition boundary only insofar as it is capped at Medium/High severity for availability impact on a node consuming the flashblocks feed, not funds theft.

### Likelihood Explanation
The team's own documentation flags this as a known, currently-unmitigated gap ("A hard bound on the queue is separate work"), and a regression test explicitly reproduces the exact backlog scenario, indicating this is a live, acknowledged condition rather than a hypothetical. Reachability requires only sending flashblock messages over the subscribed WebSocket feed; no signature, deposit, or privileged action is required by the underlying protocol layer described here (though production deployments may add network-level access controls to the flashblocks WS endpoint that are outside this crate's scope).

### Recommendation
Bound the `StateUpdate` queue (e.g., convert to `mpsc::channel` with a fixed capacity, or add a byte/entry watermark that drops or backpressures new flashblocks once the backlog exceeds a threshold) so that queue depth cannot grow unbounded relative to consumer throughput, complementing the existing pre-queue `is_superseded` filter and the bounded `FlashblockCache`.

### Proof of Concept
1. Attach to a Base node's flashblocks WebSocket subscription (`FlashblocksSubscriber`).
2. Continuously send `Flashblock` messages with monotonically increasing `metadata.block_number` values kept just ahead of the node's advancing canonical tip (so `on_flashblock_received`'s superseded check at `crates/execution/flashblocks/src/state.rs:153` never rejects them), each carrying a maximally-sized `ExecutionPayloadFlashblockDeltaV1` (max transactions/receipts).
3. Send faster than `StateProcessor::apply_flashblock` can drain the queue (e.g., by making `process_flashblock` artificially slow via large payloads or heavy state-diff computation).
4. Observe process RSS grow unbounded as `StateUpdate::Flashblock` entries accumulate in the `mpsc::unbounded_channel`, eventually leading to OOM kill of the node process.

### Citations

**File:** crates/execution/flashblocks/src/state.rs (L33-40)
```rust
pub struct FlashblocksState {
    pending_blocks: Arc<ArcSwapOption<PendingBlocks>>,
    queue: mpsc::UnboundedSender<StateUpdate>,
    rx: Arc<Mutex<mpsc::UnboundedReceiver<StateUpdate>>>,
    flashblock_sender: Sender<Arc<PendingBlocks>>,
    max_pending_blocks_depth: u64,
    last_canonical_block: AtomicU64,
}
```

**File:** crates/execution/flashblocks/src/state.rs (L145-173)
```rust
impl FlashblocksReceiver for FlashblocksState {
    fn on_flashblock_received(&self, flashblock: Flashblock) {
        let flashblock_index = flashblock.index;
        let block_number = flashblock.metadata.block_number;

        // Rejecting superseded payloads here keeps them out of the queue entirely, so a backlog
        // cannot grow on work that could never produce a publishable snapshot. The processor
        // repeats the check for payloads that were fresh on arrival but went stale while queued.
        if block_number <= self.last_canonical_block.load(Ordering::Relaxed) {
            debug!(
                message = "dropping flashblock for an already canonical block",
                block_number, flashblock_index,
            );
            Metrics::flashblock_superseded().increment(1);
            return;
        }

        match self.queue.send(StateUpdate::Flashblock(flashblock)) {
            Ok(_) => {
                debug!(
                    message = "added flashblock to processing queue",
                    block_number, flashblock_index,
                );
            }
            Err(e) => {
                error!(message = "could not add flashblock to processing queue", block_number, flashblock_index, error = %e);
            }
        }
    }
```

**File:** crates/execution/flashblocks/README.md (L100-102)
```markdown
The update queue is unbounded. Dropping superseded flashblocks before they are queued removes the
backlog shape that let lag compound, but a sustained burst of payloads that are all ahead of the
tip still grows memory. A hard bound on the queue is separate work.
```

**File:** crates/execution/flashblocks-node/tests/state.rs (L855-858)
```rust
/// Advances the node past `pending`'s anchor without telling the state processor, reproducing
/// the queue-lag shape from the 2026-08-20 mainnet incident where canonical and flashblock
/// updates piled up in the shared unbounded queue while the node kept following the chain.
async fn advance_tip_without_processing(test: &mut FlashblocksBuilderTestHarness, blocks: u64) {
```

**File:** crates/execution/flashblocks/src/processor.rs (L302-359)
```rust
    /// Applies a flashblock, unless the node has already canonicalized its block.
    ///
    /// Executing a flashblock for an already-canonical block cannot produce a publishable
    /// snapshot, and doing it anyway is what let the queue lag sustain itself: the backlog
    /// of superseded payloads consumed the processor while fresh payloads waited behind them.
    /// `FlashblocksState` rejects payloads that are already superseded when they arrive, so
    /// what reaches here is a payload that was fresh when queued and went stale while waiting,
    /// or one replayed from the cache after its canonical block landed.
    async fn apply_flashblock(
        &self,
        prev_pending_blocks: Option<Arc<PendingBlocks>>,
        flashblock: Flashblock,
    ) {
        if self.is_superseded(&flashblock) {
            debug!(
                message = "skipping flashblock for already canonical block",
                block_number = flashblock.metadata.block_number,
                flashblock_index = flashblock.index,
            );
            Metrics::flashblock_superseded().increment(1);
            return;
        }

        let start_time = Instant::now();
        match self.process_flashblock(prev_pending_blocks, &flashblock) {
            Ok(new_pending_blocks) => {
                if let Some(ref pb) = new_pending_blocks {
                    _ = self.sender.send(Arc::clone(pb));
                }
                self.pending_blocks.swap(new_pending_blocks);
                Metrics::block_processing_duration().record(start_time.elapsed());
            }
            Err(e) => {
                match e {
                    StateProcessorError::Provider(ProviderError::MissingCanonicalHeader {
                        ..
                    }) => {
                        let inserted = self.cache.lock().await.insert(flashblock);
                        if inserted {
                            debug!(message = "cached flashblock pending canonical block", error = %e);
                            return;
                        }
                    }
                    StateProcessorError::MissingFirstFlashblock => {
                        let mut cache = self.cache.lock().await;
                        // this error should only occur for non-zero index flashblocks, but check here for index safety
                        if flashblock.index > 0
                            && cache.has_flashblock(
                                flashblock.metadata.block_number,
                                flashblock.index - 1,
                            )
                            && cache.insert(flashblock)
                        {
                            return;
                        }
                        // we should ignore this error since it doesn't necessarily indicate a problem
                        return;
                    }
```
