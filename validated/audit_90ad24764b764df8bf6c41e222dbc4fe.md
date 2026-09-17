## Analysis

The Solana incident describes bot-generated transaction spam overwhelming validator memory (unbounded resource accumulation) until nodes crashed and the network halted. The closest analog in Base's production code is the flashblocks pending-state pipeline, whose update queue is explicitly documented and implemented as unbounded, with a documented but unresolved risk of memory growth under sustained transaction/flashblock bursts.

### Title
Unbounded flashblocks state-update queue permits sustained memory growth and node halt under transaction bursts - ([File: crates/execution/flashblocks/src/state.rs])

### Summary
`FlashblocksState` funnels both canonical-block notifications and builder-streamed flashblocks into a single `tokio::mpsc::unbounded_channel`. [1](#0-0)  The receiving tasks drop only *superseded* flashblocks (those for already-canonicalized blocks) before enqueue; anything still ahead of the tip is queued unconditionally. [2](#0-1)  The crate's own README documents that this queue has no hard size bound and that a sustained burst of payloads ahead of the tip "still grows memory," calling a hard bound on the queue "separate work." [3](#0-2) 

### Finding Description
`StateProcessor` applies queued `StateUpdate::Canonical`/`StateUpdate::Flashblock` entries strictly in FIFO order. [4](#0-3)  Both `on_canonical_block_received` and `on_flashblock_received` push into the same unbounded `mpsc::UnboundedSender<StateUpdate>` with no backpressure. [5](#0-4)  A prior real-world incident (referenced directly in the test file's comment for the 2026-08-20 mainnet incident) already demonstrated that canonical and flashblock updates can pile up in this shared unbounded queue while the node keeps following the chain. [6](#0-5) 

Anything that increases the rate or volume of flashblocks the builder streams (e.g. a sustained flood of transactions that the builder validly includes across many rapid flashblock indices/blocks, matching the Solana root cause of bot-generated transaction volume) while the processor's per-update work (state rebuild, DB reads, reorg detection) lags behind, causes the queue to accumulate items faster than they drain. The only existing guard is the separate, bounded `FlashblockCache` used for *out-of-order arrival before the parent canonical block lands* (capped at `MAX_CACHE_AHEAD_BLOCKS = 5`), which does not bound the main processing queue itself. [7](#0-6) 

### Impact Explanation
Unbounded growth of the shared `StateUpdate` queue is a direct memory-exhaustion vector on any Base node running the flashblocks pending-state pipeline (the public `eth_getBlockByNumber("pending")`, `eth_getBalance`, `eth_call`, and `newFlashblocks` subscription surfaces all depend on this state). [8](#0-7)  Sustained OOM growth degrades to process termination/restart, i.e. a node halt for the affected RPC/flashblocks surface — directly mirroring the Solana class of impact (memory overflow → node crash → network unavailability), scoped here to nodes serving flashblocks pending state rather than consensus-critical validators.

### Likelihood Explanation
This requires no privileged access — an unprivileged actor can generate transaction volume that a builder legitimately streams as many flashblocks in quick succession, and the vulnerability manifests whenever the processor's real work (state rebuild against the state provider, per-block reconciliation) cannot keep pace with ingestion, a condition the codebase's own README and regression test acknowledge has already occurred in production. [3](#0-2) [6](#0-5)  However, the existing per-block/per-flashblock staleness guards (`drop_pending_behind`, superseded-flashblock rejection, `max_pending_blocks_depth`) reduce steady-state backlog, so triggering unbounded growth specifically requires the processor to be persistently slower than ingestion rather than a single burst — this is a real but moderate-likelihood condition, not a one-shot exploit.

### Recommendation
Bound the `StateUpdate` channel (e.g., `mpsc::channel` with a fixed capacity) and define explicit backpressure/drop policy when full (prioritizing canonical updates over flashblocks, or dropping oldest-ahead-of-tip flashblocks), turning the currently unbounded queue into the same kind of depth-limited structure already used for `FlashblockCache` and `max_pending_blocks_depth`.

### Proof of Concept
1. Run a Base node with `flashblocks-node` enabled, subscribed to a builder's flashblocks stream.
2. Have the builder's underlying chain sustain high transaction throughput (many valid transactions from arbitrary senders, unprivileged) such that flashblocks are emitted faster than `StateProcessor::build_pending_state` can execute+commit them against the state provider (e.g., under I/O-constrained state provider).
3. Observe `queue.send(StateUpdate::Flashblock(...))` and `queue.send(StateUpdate::Canonical(...))` accumulate in the unbounded channel [5](#0-4)  without any enqueue-side limit, growing process memory until OOM/restart, consistent with the documented "sustained burst of payloads... still grows memory" limitation. [3](#0-2)

### Citations

**File:** crates/execution/flashblocks/src/state.rs (L34-40)
```rust
    pending_blocks: Arc<ArcSwapOption<PendingBlocks>>,
    queue: mpsc::UnboundedSender<StateUpdate>,
    rx: Arc<Mutex<mpsc::UnboundedReceiver<StateUpdate>>>,
    flashblock_sender: Sender<Arc<PendingBlocks>>,
    max_pending_blocks_depth: u64,
    last_canonical_block: AtomicU64,
}
```

**File:** crates/execution/flashblocks/src/state.rs (L126-173)
```rust
    pub fn on_canonical_block_received(&self, block: RecoveredBlock<BaseBlock>) {
        let block_number = block.number;

        // Deliberately not `fetch_max`: a reorg can move the tip down, and keeping the higher
        // height would suppress every flashblock built on the replacement chain.
        self.last_canonical_block.store(block_number, Ordering::Relaxed);
        self.drop_pending_behind(block_number);

        match self.queue.send(StateUpdate::Canonical(block)) {
            Ok(_) => {
                info!(message = "added canonical block to processing queue", block_number)
            }
            Err(e) => {
                error!(message = "could not add canonical block to processing queue", block_number, error = %e);
            }
        }
    }
}

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

**File:** crates/execution/flashblocks/README.md (L18-23)
```markdown
## Pending State

`StateProcessor` merges two inputs into a single pending snapshot: the flashblock stream from the
builder and the node's canonical block notifications. Both arrive as `StateUpdate` values on one
unbounded queue and are applied in order, so the processor's view of the chain falls behind the
node's real tip whenever applying updates is slower than receiving them.
```

**File:** crates/execution/flashblocks/README.md (L100-102)
```markdown
The update queue is unbounded. Dropping superseded flashblocks before they are queued removes the
backlog shape that let lag compound, but a sustained burst of payloads that are all ahead of the
tip still grows memory. A hard bound on the queue is separate work.
```

**File:** crates/execution/flashblocks/README.md (L139-150)
```markdown
## RPC Extensions

This crate provides pending-state-aware Ethereum RPC implementations used by
`base-flashblocks-node`:

- **`eth_getBlockByNumber("pending", ...)`**: returns the latest pending block built from flashblocks.
- **`eth_getTransactionReceipt`** and **`eth_getTransactionByHash`**: check canonical data first, then flashblocks pending state.
- **`eth_getBalance`**, **`eth_getTransactionCount`**, **`eth_call`**, **`eth_estimateGas`**, and **`eth_simulateV1`**: use flashblocks pending state when requested with the `pending` tag.
- **`eth_getLogs`**: combines historical logs with pending flashblock logs when the range ends at `pending`.
- **`eth_getBlockTransactionCountByNumber("pending")`**: returns the transaction count from the latest pending flashblock state.
- **`eth_sendRawTransactionSync`**: sends a raw transaction and waits for inclusion in flashblocks or the canonical chain.
- **`eth_subscribe("newFlashblocks")`**: streams pending block updates from flashblocks.
```

**File:** crates/execution/flashblocks-node/tests/state.rs (L855-858)
```rust
/// Advances the node past `pending`'s anchor without telling the state processor, reproducing
/// the queue-lag shape from the 2026-08-20 mainnet incident where canonical and flashblock
/// updates piled up in the shared unbounded queue while the node kept following the chain.
async fn advance_tip_without_processing(test: &mut FlashblocksBuilderTestHarness, blocks: u64) {
```

**File:** crates/execution/flashblocks/src/cache.rs (L8-11)
```rust
/// Maximum number of blocks ahead of the latest canonical block for which
/// flashblocks may be cached. Flashblocks further ahead than this are rejected
/// to avoid unbounded memory growth during syncing.
const MAX_CACHE_AHEAD_BLOCKS: u64 = 5;
```
