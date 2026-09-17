### Title
`PipelineCursor` leaks `origin_infos` entries on eviction, causing unbounded memory growth during L2 derivation - ([File: crates/proof/driver/src/cursor.rs])

### Summary
`PipelineCursor::advance` evicts an entry from `self.tips` once the bounded cache reaches `capacity`, but never removes the corresponding entry from `self.origin_infos`. This is the same bug class as the reported OTel eBPF `CappedConcurrentHashMap` issue: an insertion-ordered auxiliary structure (`origin_infos`, a plain `HashMap`) is fed by every insertion but only partially drained on eviction, so it grows without bound relative to the "capped" companion structure (`tips`).

### Finding Description
`PipelineCursor` is documented as implementing "a capacity-bounded LRU cache to prevent unbounded memory growth" [1](#0-0) . It maintains three parallel structures keyed by L1 origin block number: `origins` (a `VecDeque` used as the eviction order queue), `origin_infos` (a `HashMap<u64, BlockInfo>`), and `tips` (a `BTreeMap<u64, TipCursor>`) [2](#0-1) .

In `advance()`, when the cache is full, the code pops the oldest key off `origins` and removes it from `tips`, but the same key is never removed from `origin_infos`:
```rust
pub fn advance(&mut self, origin: BlockInfo, l2_tip_block: TipCursor) {
    if self.tips.len() >= self.capacity {
        let key = self.origins.pop_front().unwrap();
        self.tips.remove(&key);
    }
    self.origin = origin;
    self.origins.push_back(origin.number);
    self.origin_infos.insert(origin.number, origin);
    self.tips.insert(origin.number, l2_tip_block);
}
``` [3](#0-2) 

Every call inserts a new key into `origin_infos` (line 92), but eviction (lines 85-88) only ever calls `self.tips.remove(&key)`, never `self.origin_infos.remove(&key)`. There is no other code path in the file, or anywhere else in the repository (confirmed via search — `origin_infos` appears exclusively in `cursor.rs`), that removes stale keys from `origin_infos`. As a result, `origin_infos` grows by exactly one entry per L1 origin processed for the entire lifetime of the `PipelineCursor`, while `tips` and `origins` stay bounded at `capacity`.

`advance()` is driven directly by the derivation pipeline's L1 origin, i.e., attacker-influenced L1 data: `self.pipeline.origin()` from L1-derived batch/channel data feeds directly into `advance(origin, ...)` [4](#0-3) . This driver is used both by the live rollup node's derivation pipeline and by the fault-proof program (`crates/proof/proof`, `crates/proof/client`), which are exactly the components the scope calls out ("derivation of attacker-written L1 deposit and system-config data, the fault-proof program").

### Impact Explanation
Because `origin_infos` is fed once per processed L1 origin block and is never pruned, a long-running derivation session (node syncing a long L1 window, or a fault-proof program required to derive over a large range of L1 blocks/disputed range) accumulates one `HashMap` entry (`u64 -> BlockInfo`) per L1 block indefinitely. In the fault-proof program context this runs inside a constrained-memory zkVM/MIPS/RISC-V execution environment; unbounded heap growth there can exhaust available memory, causing the proof program to fail to produce an output root, or forcing it to compute in a memory environment where behavior is undefined — matching the reported "chain split / wrong provable output root / node halt" impact class. In the standard node derivation path, this is a slow, unbounded memory leak in the driver, consistent with CWE-401/CWE-770 in the source report.

### Likelihood Explanation
The leak triggers automatically as a side effect of ordinary derivation progress — no special conditions or malicious input are needed. Each `advance_to_target` iteration in `crates/proof/driver/src/core.rs` calls `advance()` for every derived L1 origin, so it fires continuously by design, similar to the source-report's TLS-handshake churn scenario. Any sufficiently long derivation window (either sustained node operation or a fault-proof program required to walk many L1 blocks) reaches the leak. The magnitude of impact depends on the length of the run before restart/regeneration, so exploitability is bounded but essentially unavoidable, matching the report's medium-severity availability classification.

### Recommendation
In `PipelineCursor::advance`, remove the evicted key from `origin_infos` alongside `tips`:
```rust
if self.tips.len() >= self.capacity {
    let key = self.origins.pop_front().unwrap();
    self.tips.remove(&key);
    self.origin_infos.remove(&key);
}
```
Consider adding a debug assertion or test that `origin_infos.len() == origins.len()` (or bounded by `capacity`) after `advance()` to prevent regression.

### Proof of Concept
1. Construct a `PipelineCursor` with a small `channel_timeout` (e.g. `channel_timeout = 0`, giving `capacity = 5`) via `PipelineCursor::new`.
2. Call `advance(origin, tip)` in a loop with monotonically increasing `origin.number` for, say, 1,000,000 iterations (simulating a long-running derivation over attacker/L1-driven origins).
3. Observe that `cursor.tips.len()` and `cursor.origins.len()` stay bounded at `capacity` (5), matching the intended cap, while `cursor.origin_infos.len()` grows to ~1,000,000 — proving the leak of `origin_infos` relative to the "capacity-bounded" design documented in the struct's own doc comment. [1](#0-0)

### Citations

**File:** crates/proof/driver/src/cursor.rs (L14-18)
```rust
/// A cursor that tracks the derivation pipeline state and progress.
///
/// The [`PipelineCursor`] maintains a cache of recent L1 origins and their corresponding
/// L2 tips to efficiently handle reorgs and provide quick access to recent derivation
/// state. It implements a capacity-bounded LRU cache to prevent unbounded memory growth.
```

**File:** crates/proof/driver/src/cursor.rs (L20-33)
```rust
pub struct PipelineCursor {
    /// The maximum number of cached L1/L2 mappings before evicting old entries.
    pub capacity: usize,
    /// The channel timeout in blocks used for reorg recovery calculations.
    pub channel_timeout: u64,
    /// The current L1 origin block that the pipeline is processing.
    pub origin: BlockInfo,
    /// Ordered list of L1 origin block numbers for cache eviction policy.
    pub origins: VecDeque<u64>,
    /// Mapping from L1 block numbers to their corresponding [`BlockInfo`].
    pub origin_infos: HashMap<u64, BlockInfo>,
    /// Mapping from L1 origin block numbers to their corresponding L2 tips.
    pub tips: BTreeMap<u64, TipCursor>,
}
```

**File:** crates/proof/driver/src/cursor.rs (L83-94)
```rust
    /// Advances the cursor to a new L1 origin and corresponding L2 tip.
    pub fn advance(&mut self, origin: BlockInfo, l2_tip_block: TipCursor) {
        if self.tips.len() >= self.capacity {
            let key = self.origins.pop_front().unwrap();
            self.tips.remove(&key);
        }

        self.origin = origin;
        self.origins.push_back(origin.number);
        self.origin_infos.insert(origin.number, origin);
        self.tips.insert(origin.number, l2_tip_block);
    }
```

**File:** crates/proof/driver/src/core.rs (L168-180)
```rust
            // Get the pipeline origin and update the tip cursor.
            let origin = self.pipeline.origin().ok_or(PipelineError::MissingOrigin.crit())?;
            let l2_info = L2BlockInfo::from_block_and_genesis(
                &block,
                &self.pipeline.rollup_config().genesis,
            )?;
            let output_root = self.executor.compute_output_root().map_err(DriverError::Executor)?;
            on_block(l2_info, output_root);
            result = Some((l2_info, output_root));
            let tip_cursor = TipCursor::new(l2_info, outcome.header.clone(), output_root);
            // Advance the derivation pipeline cursor
            drop(pipeline_cursor);
            self.cursor.write().advance(origin, tip_cursor);
```
