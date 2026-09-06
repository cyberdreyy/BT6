## Title
Panic-on-DB-error in vote/pre-commit storage functions lets any single signer wedge peers into a permanent crash loop that can never sign a valid block — (File: stacks-signer/src/v0/signer.rs)

### Summary
The AccountManager report's bug class is: iterating over/recording multiple sub-items where a single failing item aborts the *whole* aggregate operation, permanently blocking a critical function (liquidation). The analogous pattern in the signer is the vote/pre-commit/signature bookkeeping path: `handle_block_pre_commit`, `handle_block_signature` → `store_and_process_block_signature`, and `store_and_process_block_rejection` all call `signer_db` write/read helpers and `panic!` the entire process on any `Err` from those calls, rather than skipping/logging the single offending record.

### Finding Description
Every step of vote tallying treats an `Err` from `signer_db` as fatal to the whole signer process instead of just the one record:

- `add_block_pre_commit(...).unwrap_or_else(|_| panic!(...))` and `get_block_pre_committers(...).unwrap_or_else(|_| panic!(...))` in `handle_block_pre_commit`. [1](#0-0) 
- `add_block_signature(...).unwrap_or_else(|_| panic!(...))` and `get_block_signatures(...).unwrap_or_else(|_| panic!(...))` in `store_and_process_block_signature`. [2](#0-1) 
- `insert_block(...)` failures are routed through `handle_insert_block_error`, which unconditionally panics. [3](#0-2) 

Because this state is *persisted* in `signerdb` (a SQLite file that survives restarts), if any one peer's pre-commit/signature/rejection message provokes a write or read error on this signer's local DB (e.g., a locked/corrupted DB file, a disk-full condition hit while writing a specific row, or any other environmental SQLite failure tied to that record), the panic is not a one-off blip: the offending row remains in the DB, so on restart the signer will replay pending responses (`process_pending_responses_for_block`, `drain_pending_block_responses` per `docs/signer-flows.md` section 3) and hit the exact same call path again, panicking again — a crash loop. Unlike `AccountManager.sweepTo`, where one failing asset transfer blocks the *entire* liquidation forever, here one problematic vote record blocks the *entire signer* forever (it can never again evaluate any pre-commit/signature/rejection for any block, because the very entry point functions that would do so panic before returning).

This maps onto the required High-severity outcome: "a signer wedged into never signing valid blocks." A wedged signer that cannot process pre-commits or signatures can never reach the 70% threshold needed to co-sign a block (`docs/signer-flows.md` §5–6), and if enough signers in the set are individually wedged (each hitting their own local DB error on the same received message), the reward-cycle signer set as a whole may fail to reach quorum, directly breaking the aggregated-weight-vs-verified-accepts liveness invariant described in the pre-commit/acceptance tally logic. [4](#0-3) 

### Impact Explanation
A crash-loop of even a single signer removes that signer's weight from every future tally, and because the crash is deterministic (tied to a persisted DB row, replayed on every startup), a supervisor/restart does not repair the signer — it must be manually intervened on (DB wipe/migration). This is the "wedged into never signing valid blocks" High-severity category defined in the rules. It does not require a majority of signers or any other signer's key: a single crafted or coincidentally-adversarial message plus an environmental DB condition (disk pressure, contention, or corruption from an earlier partial write) targeting one signer's local sqlite file is sufficient.

### Likelihood Explanation
This requires the DB helper (`add_block_signature`/`add_block_pre_commit`/`get_block_signatures`/`get_block_pre_committers`/`insert_block`) to actually return an `Err` for a specific row rather than `Ok`. I was not able to fully inspect the exact SQL/constraints of these functions in `stacks-signer/src/signerdb.rs` within the available context (their line ranges were not returned by search), so I cannot confirm a concrete, attacker-triggerable SQL failure mode (e.g., a UNIQUE constraint violation on a legitimately-differing row, a type/size mismatch, or busy-timeout under contention) versus a purely environmental one. This is the main source of uncertainty in this finding — the panic-on-error pattern itself is confirmed and consistently applied across all vote-processing call sites, but the guaranteed reproducibility of the triggering `Err` from a remote actor's message alone (as opposed to environmental faults) is not proven with the available code excerpts.

### Recommendation
Do not `panic!` on `signer_db` read/write errors inside per-message vote-processing paths (`handle_block_pre_commit`, `store_and_process_block_signature`, `store_and_process_block_rejection`). Instead:
1. Log the error and skip/return early for that specific message, exactly as is already done for `get_block_rejection_signer_addrs` failures in `store_and_process_block_rejection` (which correctly `return`s on `Err` instead of panicking). [5](#0-4) 
2. Ensure that any inconsistency detected on restart (e.g., a poisoned/corrupt row) is self-healing — e.g., by best-effort deleting/ignoring the corrupt record — rather than being replayed identically into the same panic on every startup.
3. Add regression tests that inject a `signer_db` error for a single pre-commit/signature/rejection insert and assert the signer continues processing subsequent, unrelated blocks/messages rather than terminating.

### Proof of Concept
Not independently verified in this analysis due to inability to inspect the exact SQL definitions of `add_block_signature`, `add_block_pre_commit`, `get_block_pre_committers`, and `get_block_signatures` in `stacks-signer/src/signerdb.rs` (their bodies were not retrieved from the index in the exploration performed). The finding is based on the confirmed, in-code `panic!`-on-`Err` pattern at the call sites cited above and the confirmed persistence/replay behavior of pending responses documented in `docs/signer-flows.md` (sections 3–6). A concrete PoC would require: (a) locating the exact schema/constraints for these tables, (b) constructing a sequence of two BlockPreCommit/BlockResponse messages for the same signer/block pair that provoke an `Err` (not just the `Ok(false)`/duplicate case) on the second insert, and (c) demonstrating that this error is re-triggered identically after restart via `process_pending_responses_for_block`.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1278-1293)
```rust
        self.signer_db
            .add_block_pre_commit(block_hash, stacker_address)
            .unwrap_or_else(|_| panic!("{self}: Failed to save block pre-commit"));

        let block_hash = block_info.block.header.signer_signature_hash();
        // do we have enough pre-commits to reach consensus?
        // i.e. is the threshold reached?
        //
        // Tally this up front, before the early returns below, so that every pre-commit we
        // receive can be logged with the running weight. Crossing this threshold is what
        // triggers our block response, so without it the wait for the threshold, which can
        // be minutes and is the bulk of a stalled block's latency, leaves no trace at all.
        let committers = self
            .signer_db
            .get_block_pre_committers(&block_hash)
            .unwrap_or_else(|_| panic!("{self}: Failed to load block commits"));
```

**File:** stacks-signer/src/v0/signer.rs (L2297-2303)
```rust
        let rejection_addrs = match self.signer_db.get_block_rejection_signer_addrs(block_hash) {
            Ok(addrs) => addrs,
            Err(e) => {
                warn!("{self}: Failed to load block rejection addresses: {e:?}.",);
                return;
            }
        };
```

**File:** stacks-signer/src/v0/signer.rs (L2454-2477)
```rust
        if !self
            .signer_db
            .add_block_signature(block_hash, signer_address, signature)
            .unwrap_or_else(|_| panic!("{self}: Failed to save block signature"))
        {
            return;
        }

        // If this isn't our own signature and we haven't seen a pre-commit from this signer yet, try treating it as a pre-commit in case the caller is running an outdated version
        if signer_address != &self.stacks_address && !self.signer_db.has_committed(block_hash, signer_address).inspect_err(|e| warn!("Failed to check if pre-commit message already considered for {signer_address:?} for {block_hash}: {e}")).unwrap_or(false) {
            self.handle_block_pre_commit(stacks_client, sortition_state, signer_address, block_hash);
            return;
        }

        if block_info.signed_group.is_some() {
            // We have already processed this block to the accepted state. Adding more signatures will not change anything so nothing to check.
            return;
        }
        // do we have enough signatures to broadcast?
        // i.e. is the threshold reached?
        let signatures = self
            .signer_db
            .get_block_signatures(block_hash)
            .unwrap_or_else(|_| panic!("{self}: Failed to load block signatures"));
```

**File:** stacks-signer/src/v0/signer.rs (L2495-2523)
```rust
        let total_signature_weight = self.compute_signature_signing_weight(addrs_to_sigs.keys());
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });

        if min_weight > total_signature_weight {
            info!("{self}: Received block acceptance, but have not yet reached the acceptance threshold.";
                "signer_signature_hash" => %block_hash,
                "signature_weight" => signature_weight,
                "consensus_hash" => %block_info.block.header.consensus_hash,
                "block_height" => block_info.block.header.chain_length,
                "total_weight_approved" => total_signature_weight,
                "total_weight" => total_weight,
                "percent_approved" => (total_signature_weight as f64 / total_weight as f64 * 100.0),
            );
            return;
        }
        info!("{self}: have reached the block acceptance threshold";
            "signer_signature_hash" => %block_hash,
            "signature_weight" => signature_weight,
            "consensus_hash" => %block_info.block.header.consensus_hash,
            "block_height" => block_info.block.header.chain_length,
            "total_weight_approved" => total_signature_weight,
            "total_weight" => total_weight,
            "percent_approved" => (total_signature_weight as f64 / total_weight as f64 * 100.0),
        );
```

**File:** stacks-signer/src/v0/signer.rs (L2660-2664)
```rust
    /// Helper for logging insert_block error
    pub fn handle_insert_block_error(&self, e: DBError) {
        error!("{self}: Failed to insert block into signer-db: {e:?}");
        panic!("{self} Failed to write block to signerdb: {e}");
    }
```
