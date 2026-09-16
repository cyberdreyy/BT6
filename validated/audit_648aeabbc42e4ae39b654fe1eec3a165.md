I found a strong analog. The `FlashblocksState` maintains an `mpsc::UnboundedSender<StateUpdate>` queue that receives every incoming flashblock payload from the builder's WebSocket stream, and this queue is explicitly documented as unbounded with no per-frame size or count cap enforced before enqueueing.

### Title
Unbounded Flashblocks State-Update Queue Enables Memory-Exhaustion Denial of Service - ([File: crates/execution/flashblocks/src/state.rs])

### Summary
`FlashblocksState` merges canonical-block and flashblock updates into a single `mpsc::unbounded_channel::<StateUpdate>` queue that the `StateProcessor` drains sequentially. Neither the per-message payload size nor the number of queued messages is bounded before a `Flashblock` or `Canonical` update is pushed onto this queue, so a sustained burst of flashblock payloads that stay ahead of the canonical tip is queued indefinitely and grows heap memory without limit, mirroring the Mattermost issue where neither frame count nor overall file size was capped before the animated GIF was processed and stored.

### Finding Description
`on_flashblock_received` only rejects a flashblock if its block number is at or below `last_canonical_block`; anything else is unconditionally pushed onto the unbounded queue via `self.queue.send(StateUpdate::Flashblock(flashblock))`. [1](#0-0) 
The queue itself is created as an `mpsc::unbounded_channel` with no capacity limit. [2](#0-1) 
The crate's own README explicitly documents this as an acknowledged, unresolved gap: "The update queue is unbounded. Dropping superseded flashblocks before they are queued removes the backlog shape that let lag compound, but a sustained burst of payloads that are all ahead of the tip still grows memory. A hard bound on the queue is separate work." [3](#0-2) 
The existing guards (`drop_pending_behind`, `is_superseded`, `MAX_CACHE_AHEAD_BLOCKS` in `FlashblockCache`) only bound *staleness* of the published pending snapshot and the parent-wait cache, not the volume of unprocessed messages sitting in the processing queue itself. [4](#0-3) 
Each `Flashblock` carries a full execution-payload delta (`ExecutionPayloadFlashblockDeltaV1`) including transactions, so queued entries are not small fixed-size structs — a backlog of them can represent substantial heap allocation, similar to how each animated-GIF frame the Mattermost server failed to cap represented additional decoded image memory. [5](#0-4) 

### Impact Explanation
Any source able to feed flashblocks into a node's `FlashblocksState` (the flashblocks WebSocket subscriber connected to the block builder) faster than the `StateProcessor` can apply them — or that sustains a stream of "ahead of tip" flashblocks — causes the unbounded queue to grow without limit, exhausting node memory and ultimately crashing or hanging the node process (denial of service / node halt). This directly matches the CWE-770 "allocation of resources without limits" class in the Mattermost report, but here it affects a live rollup node's flashblocks pending-state pipeline rather than a chat server's emoji store.

### Likelihood Explanation
The repository's own documentation records that this exact backlog-growth pattern already occurred in production ("reproducing the queue-lag shape from the 2026-08-20 mainnet incident where canonical and flashblock updates piled up in the shared unbounded queue while the node kept following the chain"), confirming the condition is reachable under real operating conditions, not merely theoretical. [6](#0-5) 
The README itself flags the fix as still outstanding ("A hard bound on the queue is separate work."), meaning the vulnerability window remains open in the current code. [3](#0-2) 

### Recommendation
Bound the `StateUpdate` queue (e.g., replace the `mpsc::unbounded_channel` with a bounded channel sized to a safe memory budget, or track cumulative queued-payload bytes/count and drop or backpressure new flashblocks once a threshold is exceeded) so that a sustained burst of payloads ahead of the tip cannot grow memory without limit, closing the gap the README already calls out.

### Proof of Concept
1. Connect to a node's flashblocks ingestion path (the WebSocket flashblock subscriber feeding `on_flashblock_received`).
2. Continuously emit `Flashblock` payloads whose `block_number` stays ahead of `last_canonical_block` (so `on_flashblock_received`'s only guard never rejects them) at a rate exceeding the `StateProcessor`'s consumption rate, as in `advance_tip_without_processing` combined with repeated `send_flashblock` calls in the existing regression test. [7](#0-6) 
3. Because the backing channel is `mpsc::unbounded_channel`, every payload is accepted and queued regardless of how far the processor has fallen behind, growing heap usage until the node exhausts memory and halts.

### Citations

**File:** crates/execution/flashblocks/src/state.rs (L33-60)
```rust
pub struct FlashblocksState {
    pending_blocks: Arc<ArcSwapOption<PendingBlocks>>,
    queue: mpsc::UnboundedSender<StateUpdate>,
    rx: Arc<Mutex<mpsc::UnboundedReceiver<StateUpdate>>>,
    flashblock_sender: Sender<Arc<PendingBlocks>>,
    max_pending_blocks_depth: u64,
    last_canonical_block: AtomicU64,
}

impl FlashblocksState {
    /// Creates a new flashblocks state manager.
    ///
    /// The state is created without a client. Call [`start`](Self::start) with a client
    /// to spawn the state processor after the node is launched.
    pub fn new(max_pending_blocks_depth: u64) -> Self {
        let (tx, rx) = mpsc::unbounded_channel::<StateUpdate>();
        let pending_blocks: Arc<ArcSwapOption<PendingBlocks>> = Arc::new(ArcSwapOption::new(None));
        let (flashblock_sender, _) = broadcast::channel(BUFFER_SIZE);

        Self {
            pending_blocks,
            queue: tx,
            rx: Arc::new(Mutex::new(rx)),
            flashblock_sender,
            max_pending_blocks_depth,
            last_canonical_block: AtomicU64::new(0),
        }
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

**File:** crates/execution/flashblocks/src/cache.rs (L8-11)
```rust
/// Maximum number of blocks ahead of the latest canonical block for which
/// flashblocks may be cached. Flashblocks further ahead than this are rejected
/// to avoid unbounded memory growth during syncing.
const MAX_CACHE_AHEAD_BLOCKS: u64 = 5;
```

**File:** crates/execution/flashblocks/src/processor.rs (L50-58)
```rust
/// Messages consumed by the state processor.
#[allow(clippy::large_enum_variant)]
#[derive(Debug)]
pub enum StateUpdate {
    /// New canonical block to reconcile against pending state.
    Canonical(RecoveredBlock<BaseBlock>),
    /// Incoming flashblock payload to extend pending state.
    Flashblock(Flashblock),
}
```

**File:** crates/execution/flashblocks-node/tests/state.rs (L855-857)
```rust
/// Advances the node past `pending`'s anchor without telling the state processor, reproducing
/// the queue-lag shape from the 2026-08-20 mainnet incident where canonical and flashblock
/// updates piled up in the shared unbounded queue while the node kept following the chain.
```

**File:** crates/execution/flashblocks-node/tests/state.rs (L858-862)
```rust
async fn advance_tip_without_processing(test: &mut FlashblocksBuilderTestHarness, blocks: u64) {
    for _ in 0..blocks {
        test.new_canonical_block_without_processing(vec![]).await;
    }
}
```
