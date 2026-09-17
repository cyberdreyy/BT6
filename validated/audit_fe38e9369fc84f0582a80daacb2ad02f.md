## Analog Found

### Title
Unbounded flashblocks state-update queue can exhaust node memory and halt flashblocks-serving RPC nodes - (File: `crates/execution/flashblocks/src/state.rs`)

### Summary
`FlashblocksState` backs its `StateUpdate` pipeline with a Tokio `mpsc::unbounded_channel`, and a single background task drains it strictly in order. There is no cap on how many `Canonical` or `Flashblock` updates may be enqueued before the processor catches up, mirroring the Sherlock M-5 pattern of an unbounded pending-item queue that is drained by one sequential loop with no admission limit.

### Finding Description
`FlashblocksState::new` creates the queue with `mpsc::unbounded_channel::<StateUpdate>()` and stores both the sender and a single shared receiver. [1](#0-0) 

Every canonical block notification and every flashblock payload received from the upstream builder is pushed onto this same unbounded queue via `on_canonical_block_received` / `on_flashblock_received`. [2](#0-1) 

A single `StateProcessor::start` task drains and applies these updates strictly sequentially, one at a time, including cache replays after canonical blocks. [3](#0-2) 

The crate's own documentation acknowledges the queue has no bound: superseded flashblocks are dropped *before* being queued, but that only prevents backlog from the *same* block height — a sustained burst of updates that are all ahead of the current tip (e.g., processor falling behind during high transaction throughput, heavy state-root recomputation, or a reconciliation-triggered rebuild) still grows memory without limit, and "a hard bound on the queue is separate work." [4](#0-3) 

This is structurally identical to the Market bug class: an unbounded pending-item queue (protected position updates vs. flashblocks state updates) that is settled/applied by a single sequential loop (`_settle`'s while loop vs. `StateProcessor::start`'s while loop) with no admission-side limit, so once production of items outruns consumption, the backlog compounds indefinitely.

### Impact Explanation
If flashblock/canonical-update production sustains a rate above what the single-threaded processor can apply — which can happen during normal high-traffic periods, expensive reorg reconciliation, or provider-read slowdowns, without requiring a malicious builder or malicious peer — the `UnboundedSender` queue grows without bound, consuming node memory until the flashblocks-enabled RPC process is OOM-killed (a node halt for that node's flashblocks/pending-state RPC surface, i.e. `eth_getBlockByNumber("pending")`, `eth_call`/`eth_estimateGas` against `pending`, `eth_subscribe("newFlashblocks")`, `eth_getLogs` ending at `pending`, all of which are public `eth_*`/WebSocket paths this queue feeds).

### Likelihood Explanation
Similar to the original issue's judged rationale, this does not require an attacker: it only requires normal but sustained conditions where update production outpaces the single consumer (busy periods, slow state application, or reconciliation rebuilds), consistent with the crate's own changelog referencing a real "2026-08-20 mainnet incident where canonical and flashblock updates piled up in the shared unbounded queue while the node kept following the chain." The likelihood is therefore non-trivial but conditional on sustained backpressure, matching a Medium-severity profile (external/operational conditions required, not an attacker-crafted single transaction).

### Recommendation
Bound the `StateUpdate` queue (e.g., a bounded channel with backpressure, or a hard cap with oldest/duplicate-height eviction as already done for superseded flashblocks) so that a lagging processor cannot cause unbounded memory growth, matching the fix pattern recommended for the Market issue (cap the pending queue and/or cap how much is drained/settled per step).

### Proof of Concept
1. Start a flashblocks-enabled node with `FlashblocksState::start` and `FlashblocksSubscriber` connected to a builder feed as in `crates/execution/flashblocks-node/src/extension.rs`. [5](#0-4) 
2. Simulate sustained high-throughput conditions (e.g., in the harness `advance_tip_without_processing` helper used by `test_stale_pending_dropped_once_node_advances_past_it`) so canonical blocks and flashblocks are produced faster than `StateProcessor::start` can apply them. [6](#0-5) 
3. Because `queue` is an `mpsc::unbounded_channel`, `on_canonical_block_received`/`on_flashblock_received` never block or reject;每 message accumulates in memory indefinitely while the single processor task works through the backlog one update at a time, reproducing the unbounded-queue growth documented in the README.

### Citations

**File:** crates/execution/flashblocks/src/state.rs (L47-60)
```rust
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

**File:** crates/execution/flashblocks/src/processor.rs (L257-300)
```rust
    /// Processes updates from the queue until the channel closes.
    pub async fn start(&self) {
        while let Some(update) = self.rx.lock().await.recv().await {
            let prev_pending_blocks = self.load_pending_or_drop_stale();
            match update {
                StateUpdate::Canonical(block) => {
                    debug!(message = "processing canonical block", block_number = block.number);
                    match self.process_canonical_block(prev_pending_blocks, &block) {
                        Ok(new_pending_blocks) => {
                            self.pending_blocks.swap(new_pending_blocks);

                            let mut cache = self.cache.lock().await;
                            cache.update_canonical(block.number);
                            let cached = cache.drain(block.number + 1);
                            drop(cache);

                            if !cached.is_empty() {
                                debug!(
                                    message = "replaying cached flashblocks after canonical block",
                                    canonical_block = block.number,
                                    cached_count = cached.len(),
                                );
                                for flashblock in cached {
                                    let fb_prev = self.load_pending_or_drop_stale();
                                    self.apply_flashblock(fb_prev, flashblock).await;
                                }
                            }
                        }
                        Err(e) => {
                            error!(message = "could not process canonical block", error = %e);
                        }
                    }
                }
                StateUpdate::Flashblock(flashblock) => {
                    debug!(
                        message = "processing flashblock",
                        block_number = flashblock.metadata.block_number,
                        flashblock_index = flashblock.index
                    );
                    self.apply_flashblock(prev_pending_blocks, flashblock).await;
                }
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

**File:** crates/execution/flashblocks-node/src/extension.rs (L37-52)
```rust
        let state = cfg.state;
        let mut subscriber = FlashblocksSubscriber::new(
            Arc::clone(&state),
            cfg.websocket_url,
            cfg.subscriber_ping_interval,
        );

        let state_for_canonical = Arc::clone(&state);
        let state_for_rpc = Arc::clone(&state);
        let state_for_start = state;

        // Start state processor, subscriber, and canonical subscription after node is started
        let hooks = hooks.add_node_started_hook(move |ctx| {
            info!(message = "Starting Flashblocks state processor");
            state_for_start.start(ctx.provider().clone());
            subscriber.start();
```

**File:** crates/execution/flashblocks-node/tests/state.rs (L855-862)
```rust
/// Advances the node past `pending`'s anchor without telling the state processor, reproducing
/// the queue-lag shape from the 2026-08-20 mainnet incident where canonical and flashblock
/// updates piled up in the shared unbounded queue while the node kept following the chain.
async fn advance_tip_without_processing(test: &mut FlashblocksBuilderTestHarness, blocks: u64) {
    for _ in 0..blocks {
        test.new_canonical_block_without_processing(vec![]).await;
    }
}
```
