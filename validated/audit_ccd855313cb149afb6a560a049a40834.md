### Title
Signer thread panics (via `handle_insert_block_error`) on any `signer_db.insert_block` failure, permanently wedging the signer on a poisoned/locked SQLite DB - ([File: stacks-signer/src/v0/signer.rs])

### Summary
The external report's bug class is: code assumes a state-changing call cannot fail, so when it does fail (revert), the caller has no recovery path and the asset/state is stuck forever, no matter how many times the operation is retried. The `stacks-signer` analog is `Signer::handle_insert_block_error`, which is invoked as the `unwrap_or_else` handler on essentially every `self.signer_db.insert_block(...)` call across the block-proposal, validation, pre-commit, rejection, and timeout paths. Instead of returning an error the caller can recover from, it unconditionally panics, killing the signer's runloop thread.

### Finding Description
Every place the signer needs to persist block state calls `self.signer_db.insert_block(&block_info).unwrap_or_else(|e| self.handle_insert_block_error(e))`, and `handle_insert_block_error` is defined as: [1](#0-0) 

This pattern recurs on the proposal path, the pre-commit threshold path, the validate-ok path, the validate-reject path, the submission-timeout path, and the rejection-threshold path: [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

Just as the XC20Wrapper code assumed `mint()` cannot revert and therefore had no fallback, this code assumes `insert_block` cannot fail and therefore has no fallback — it panics on any SQLite error (disk full, `SQLITE_BUSY`/lock contention from concurrent readers such as `check_pending_block_validations`, corruption, permission errors, etc.). A rapid sequence of block proposals crafted by the single miner-of-the-slot (each proposal triggers validation, pre-commit, and `insert_block` calls in quick succession), combined with ordinary lock contention on the local SQLite file from the signer's own background tasks reading/writing the same DB, is sufficient to raise a transient `rusqlite`/`DBError` that this code turns into a fatal panic rather than a retry.

Unlike a normal crash-and-restart, this is a poor recovery path for a signer: the runloop thread dies, and (depending on how the process/supervisor is configured) the signer simply stops participating — it will not reject, will not pre-commit, and will not sign anything until manually restarted. If the same triggering condition (e.g., persistent disk/lock contention) recurs after restart, the signer can be repeatedly wedged, mirroring the "no matter how hard you retry ... it always fails" scenario described in the source report.

### Impact Explanation
This maps to the High-severity bucket: "a signer wedged into never signing valid blocks." A single miner (plus normal gossip/StackerDB traffic causing DB read/write concurrency) can produce conditions that panic the signer's thread via `handle_insert_block_error`, removing that signer's weight from all subsequent voting/pre-commit tallies until it is manually restarted, and — if the underlying DB condition persists — the signer can be wedged repeatedly, degrading the pre-commit/acceptance threshold reliability of the honest signer set. It does not lead to a Critical-tier equivocation itself, since the panic drops state rather than mis-signing, but it is a genuine liveness wedge triggerable without collusion.

### Likelihood Explanation
Likelihood is moderate: it does not require a majority of signers or any key material, only conditions that induce a `DBError` from SQLite (busy/locked file, disk pressure, or any other I/O failure) at the moment `insert_block` is called during ordinary one-slot-miner block-proposal traffic. Because `insert_block` is called on nearly every hot path (proposal, validation callback, pre-commit, rejection, timeout), the panic surface is broad, increasing the odds that some transient DB hiccup during a busy tenure trips it.

### Recommendation
Do not treat `insert_block` failures as fatal. Distinguish between truly unrecoverable corruption (which may warrant a controlled shutdown) and transient errors like `SQLITE_BUSY`/lock timeouts, which should be retried (with backoff) or, if retries are exhausted, should degrade gracefully (skip this response cycle, log loudly, and let the next validation/proposal event or timeout re-drive state) rather than `panic!`. This mirrors the `_safeMint`-style mitigation in the source report: swallow the failure into a well-defined error branch instead of letting an unhandled failure propagate into permanent loss of function.

### Proof of Concept
Conceptual trigger (cannot be executed from this read-only environment, but derivable directly from the code paths cited above):
1. A miner proposes a block; the signer runs `handle_block_proposal`, which calls `self.signer_db.insert_block(&block_info).unwrap_or_else(|e| self.handle_insert_block_error(e))` at `stacks-signer/src/v0/signer.rs:1717-1719`.
2. Concurrently, the signer's own background maintenance (e.g., `check_pending_block_validations`/timeout checks) is also writing to the same local SQLite file, or the host is under disk pressure, causing the `INSERT`/`UPDATE` inside `signer_db.insert_block` to return `Err(DBError::SqliteError(SQLITE_BUSY))` (or any other I/O error).
3. `handle_insert_block_error` at `stacks-signer/src/v0/signer.rs:2660-2664` unconditionally logs and then `panic!`s, terminating the signer's event-processing thread.
4. The signer stops responding to proposals, pre-commits, and rejections for all subsequent blocks until it is manually restarted; if the DB contention condition persists (e.g., degraded disk, or the same miner keeps flooding proposals fast enough to sustain lock contention), the signer panics again immediately after restart, reproducing the "retry never succeeds" pattern from the source report.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1355-1362)
```rust
            if let Err(e) = block_info.mark_locally_rejected() {
                if !block_info.has_reached_consensus() {
                    warn!("{self}: Failed to mark block as locally rejected: {e:?}");
                }
            };
            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
```

**File:** stacks-signer/src/v0/signer.rs (L1466-1474)
```rust
        // It is only considered globally accepted IFF we receive a new block event confirming it OR see the chain tip of the node advance to it.
        if let Err(e) = block_info.mark_locally_accepted(false) {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally accepted: {e:?}",);
            }
        }
        self.signer_db
            .insert_block(&block_info)
            .unwrap_or_else(|e| self.handle_insert_block_error(e));
```

**File:** stacks-signer/src/v0/signer.rs (L1716-1719)
```rust
            // Do not store KNOWN invalid blocks as this could DOS the signer. We only store blocks that are valid or unknown.
            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
```

**File:** stacks-signer/src/v0/signer.rs (L1955-1957)
```rust
            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
```

**File:** stacks-signer/src/v0/signer.rs (L2170-2172)
```rust
        self.signer_db
            .insert_block(&block_info)
            .unwrap_or_else(|e| self.handle_insert_block_error(e));
```

**File:** stacks-signer/src/v0/signer.rs (L2660-2664)
```rust
    /// Helper for logging insert_block error
    pub fn handle_insert_block_error(&self, e: DBError) {
        error!("{self}: Failed to insert block into signer-db: {e:?}");
        panic!("{self} Failed to write block to signerdb: {e}");
    }
```
