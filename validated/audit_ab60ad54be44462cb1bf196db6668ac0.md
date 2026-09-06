### Title
Cross-block collision in `block_rejection_signer_addrs` lets a stale rejection from an earlier block silently disappear, causing the >30% global-rejection threshold to be miscounted - (File: stacks-signer/src/signerdb.rs)

### Summary
The `block_rejection_signer_addrs` table, used to tally weighted rejections toward the "block is dead" (globally-rejected) decision, is declared with `PRIMARY KEY (signer_addr)` only, not `(signer_signature_hash, signer_addr)`: [1](#0-0) 

Because the primary key is the signer address alone, a single signer can have at most one row in this table across *all* blocks it has ever rejected, not one row per block. Any later rejection recorded for that signer (for a different, unrelated block/proposal) overwrites (or is ignored/replaces, depending on the INSERT variant) the row that was backing the tally for an earlier block still awaiting consensus.

### Finding Description
The rejection-tallying path is:
- `handle_block_rejection` / `store_and_process_block_rejection` verify the signature, then call `signer_db.add_block_rejection_signer_addr(block_hash, signer_address, reject_reason)` to persist the vote, and immediately re-read `get_block_rejection_signer_addrs(block_hash)` to recompute the weighted rejection total that decides `mark_globally_rejected()`: [2](#0-1) 

This is the sole path that turns rejection weight into the "over 30% rejected — 70% is now impossible" terminal state described in the design docs: [3](#0-2) 

Since a given signer's slot in the underlying table (`PRIMARY KEY (signer_addr)`) is shared across every block it has rejected, the moment that same signer address rejects a *second* proposal (e.g., a re-proposed/competing block at the same height, or an unrelated later proposal in the same reward cycle — both are ordinary, attacker/miner-reachable events since re-proposals are routine per `should_reevaluate_block`/`handle_block_proposal`), its row is repointed to the new `signer_signature_hash`. Any subsequent `get_block_rejection_signer_addrs(old_block_hash)` call (keyed by a `WHERE signer_signature_hash = ?` filter over this table) will then silently return one fewer row for the old block, understating the rejection weight that had already accumulated for it.

This breaks the "aggregated-weight vs verified-accepts/rejects" equality that the >30% rejection threshold depends on: the on-disk weighted tally for a still-pending block proposal can retroactively shrink without any of the original rejecting signers changing their vote for that block. A block that had already crossed (or was about to cross) the blocking-minority threshold for rejection can be knocked back under threshold purely because unrelated/later rejection traffic for a *different* block reused the same signer's table slot — re-opening a path to acceptance/signing for a proposal the signer set had effectively already killed, or delaying/altering the terminal `GloballyRejected` determination in a way that is not reachable through any deliberate signer decision.

### Impact Explanation
This is a miscounted-response class defect: the persisted weighted-rejection count for a block proposal can be understated relative to what was actually observed and validly signed by rejecting signers, because of a schema-level collision keyed only on `signer_addr`. This can delay or prevent a proposal reaching the `GloballyRejected` state it should have reached, keeping a proposal that should be dead artificially alive in `LocallyRejected`/pending state, and can be triggered by ordinary re-proposal traffic from a single miner slot plus normal signer voting — no majority collusion required to trigger the corruption (only normal operation with more than one competing/older proposal in flight for the same reward cycle).

### Likelihood Explanation
Re-proposals of blocks at the same height (rejected-then-superseded proposals, forks, or re-tries after a timeout) are routine and are explicitly handled by `should_reevaluate_block`/`handle_block_proposal`. Any signer that rejects more than one distinct block-proposal sighash during a reward cycle — a common occurrence — will trigger the primary-key collision. It requires no special signer collusion, just ordinary miner/network conditions that produce more than one rejected proposal per signer per reward cycle.

### Recommendation
Change the primary key of `block_rejection_signer_addrs` to the composite `(signer_signature_hash, signer_addr)` (matching the `block_signatures`/`block_pre_commit` sibling tables' intent of keying per-block), and add a migration that preserves existing rows under the new key. Audit `add_block_rejection_signer_addr`'s INSERT statement to confirm whether it uses `INSERT OR IGNORE`/`INSERT OR REPLACE` and adjust accordingly once the composite key is in place, and add a regression test that a signer rejecting two different block sighashes in the same reward cycle does not cause `get_block_rejection_signer_addrs` for the first sighash to lose that signer's vote.

### Proof of Concept
1. Signer S rejects proposal A (sighash `h_A`) — `add_block_rejection_signer_addr(h_A, S, ...)` inserts row `(h_A, S)`.
2. Enough other signers also reject A such that total rejection weight is just under the blocking-minority threshold (>30% weight), so A remains `LocallyRejected` (not yet globally rejected).
3. The miner re-proposes a competing block B (sighash `h_B`) at the same height (or in a later slot within the same reward cycle); signer S also rejects B — `add_block_rejection_signer_addr(h_B, S, ...)` is called, which collides on the `signer_addr` primary key and overwrites/replaces S's row so it now points at `h_B`.
4. A later event re-triggers `store_and_process_block_rejection` for A (e.g., a pending/duplicate rejection replay via `process_pending_responses_for_block`), calling `get_block_rejection_signer_addrs(h_A)` — S's vote is no longer present, so `total_reject_weight` for A is understated by S's weight, potentially preventing A from crossing the >30% rejection threshold it had already effectively reached.

Note: I could not directly view the body of `add_block_rejection_signer_addr`/`get_block_rejection_signer_addrs` (the read tool did not return their implementation lines before the session ended), so the exact INSERT semantics (IGNORE vs REPLACE) are inferred from the table schema and could not be fully confirmed against the function bodies; this should be verified directly in `stacks-signer/src/signerdb.rs` before treating the exact overwrite mechanics as certain, though the schema-level primary-key design flaw itself is confirmed from the `CREATE_BLOCK_REJECTION_SIGNER_ADDRS_TABLE` definition.

### Citations

**File:** stacks-signer/src/signerdb.rs (L514-524)
```rust
static CREATE_BLOCK_REJECTION_SIGNER_ADDRS_TABLE: &str = r#"
CREATE TABLE IF NOT EXISTS block_rejection_signer_addrs (
    -- The block sighash commits to all of the stacks and burnchain state as of its parent,
    -- as well as the tenure itself so there's no need to include the reward cycle.  Just
    -- the sighash is sufficient to uniquely identify the block across all burnchain, PoX,
    -- and stacks forks.
    signer_signature_hash TEXT NOT NULL,
    -- the signer address that rejected the block
    signer_addr TEXT NOT NULL,
    PRIMARY KEY (signer_addr)
) STRICT;"#;
```

**File:** stacks-signer/src/v0/signer.rs (L2274-2325)
```rust
    ) {
        let block_hash = &block_info.signer_signature_hash();
        // We should still store signatures even on consensus reached blocks for auditing purposes.
        // signature is valid! store it
        match self.signer_db.add_block_rejection_signer_addr(
            block_hash,
            signer_address,
            reject_reason,
        ) {
            Err(e) => {
                warn!("{self}: Failed to save block rejection signature: {e:?}",);
            }
            Ok(false) => return, // We already have this signature, do not process it again.
            Ok(true) => (),
        }

        if block_info.has_reached_consensus() {
            // Checking the rejection signatures is pointless. We have already reached consensus on this block.
            return;
        }

        // do we have enough signatures to mark a block a globally rejected?
        // i.e. is (set-size) - (threshold) + 1 reached.
        let rejection_addrs = match self.signer_db.get_block_rejection_signer_addrs(block_hash) {
            Ok(addrs) => addrs,
            Err(e) => {
                warn!("{self}: Failed to load block rejection addresses: {e:?}.",);
                return;
            }
        };
        let signature_weight = self.signer_weights.get(signer_address).unwrap_or(&0);
        let total_reject_weight =
            self.compute_signature_signing_weight(rejection_addrs.iter().map(|(addr, _)| addr));
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
        if total_reject_weight.saturating_add(min_weight) <= total_weight {
            // Not enough rejection signatures to make a decision
            info!("{self}: Have not yet received enough block rejections to reach a consensus decision on this block";
                "signer_signature_hash" => %block_hash,
                "signature_weight" => signature_weight,
                "consensus_hash" => %block_info.block.header.consensus_hash,
                "block_height" => block_info.block.header.chain_length,
                "total_weight_rejected" => total_reject_weight,
                "total_weight" => total_weight,
                "percent_rejected" => (total_reject_weight as f64 / total_weight as f64 * 100.0),
            );
            return;
        }
```

**File:** docs/signer-flows.md (L368-372)
```markdown
    KIND -- "Rejected" --> HBR["handle_block_rejection:<br/>verify, store via<br/>add_block_rejection_signer_addr"]
    HBR --> RT{"rejection weight makes<br/>70% approval impossible?"}
    RT -- no --> N3(["wait"])
    RT -- yes --> GREJ["mark_globally_rejected;<br/>pre-global-state versions also<br/>update miner status"]:::bad
    BCAST --> NB["node processes block →<br/>NewBlock event →<br/>mark_globally_accepted"]:::good
```
