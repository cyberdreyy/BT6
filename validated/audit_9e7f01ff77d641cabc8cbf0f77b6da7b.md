### Title
Stale rejection weight is never retracted when a signer later accepts the same block, letting a false blocking-minority wedge a valid block — (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
The node-side `StackerDBListener` tallies `total_weight_approved` and `total_weight_rejected` for a proposed block using two *independent* membership checks — `gathered_signatures` (keyed on slot id) for acceptances and `responded_signers` (also keyed on slot id) for rejections — instead of a single authoritative "current verdict per signer" table. A signer that first broadcasts a `Rejected` response and later reconsiders (a behavior the signer explicitly supports, per `docs/signer-flows.md`'s "repeat my earlier answer, unless the reason I rejected has since gone away") and broadcasts `Accepted` for the *same* block has its weight counted into **both** `total_weight_rejected` and `total_weight_approved`, because the accept path never checks `responded_signers` and nothing ever decrements `total_weight_rejected` for a signer who changed their mind.

### Finding Description
In `stackerdb_listener.rs`, when an `Accepted` signature arrives, the weight is added to `total_weight_approved` gated only on `!block.gathered_signatures.contains_key(&slot_id)`: [1](#0-0) 

When a `Rejected` response arrives, weight is added to `total_weight_rejected` gated on `block.responded_signers.insert(slot_id)`: [2](#0-1) 

If a signer's *first* response is `Accepted`, it also inserts into `responded_signers` (line 465), which correctly suppresses a later stray `Rejected` from double counting. But if the *first* response is `Rejected` (adding to `total_weight_rejected` and `responded_signers`), and the same signer subsequently reconsiders and sends `Accepted` for the same block, the accept branch does **not** consult `responded_signers` at all — it only checks `gathered_signatures`, which is still empty for that signer — so `total_weight_approved` is incremented as well. The signer's weight now counts toward *both* totals simultaneously, and nothing in the code path ever removes it from `total_weight_rejected`.

This reconsideration is not a hypothetical or malicious scenario: the signer's own event loop is documented to retry a previously-rejected block once the rejection reason becomes stale ("repeat my earlier answer... unless the reason I rejected has since gone away"): [3](#0-2) 

and transient/retryable rejection reasons such as `SortitionViewMismatch`/`ConnectivityIssues` are explicitly called out as re-evaluated conditions in the same flow (section 3, `check_block_against_state`): [4](#0-3) 

Meanwhile, `reset_rejections` (used on proposal timeout) explicitly documents the asymmetric design assumption that approvals are a durable "bearer instrument" that can never be cleared, but makes no equivalent accommodation for a rejection being retracted by the same signer's later acceptance: [5](#0-4) 

The `NakamotoSignerCoordinator::gather_signatures` loop checks the blocking-minority rejection condition *before* the approval condition on every poll: [6](#0-5) 
(see also the same ordering with concrete threshold arithmetic) [7](#0-6) 

So the stale (never-retracted) rejection weight is fully live: as soon as enough signers who *later* changed their minds have their earlier rejection weight sum past `total_weight - weight_threshold` (the blocking minority), the coordinator declares the block `SignersRejected`, even though those same signers have (or will) legitimately sign it. This breaks the equality "`total_weight_rejected` reflects signers currently refusing this block" — the aggregated rejection weight no longer matches the set of signers who actually still reject the block, which is exactly the "aggregated-weight vs verified-accepts equality" class of defect this scan is looking for, but manifesting as an aggregated-rejects vs current-rejecters mismatch.

### Impact Explanation
This is a liveness wedge: a single well-behaved signer that (a) rejects a proposal for a transient/retryable reason and (b) later signs the same proposal after the transient condition clears (both explicitly-supported signer behaviors) causes its weight to remain permanently double-counted for that block's lifetime in the miner's in-memory `BlockStatus`. Combined with a small number of other signers behaving the same way (well within normal operation during sortition-view convergence, which the docs describe as a routine multi-second window where proposals are transiently rejected), the aggregate stale-rejection weight can cross the 30%+ blocking-minority threshold and force the coordinator to treat an otherwise fully-signable block as globally rejected, stalling that tenure and requiring a miner retry/new tenure. No majority collusion or key compromise is needed — it is a bookkeeping asymmetry in the node's per-block vote aggregation, in the in-scope `stacks-node/src/nakamoto_node/stackerdb_listener.rs` and `signer_coordinator.rs`.

### Likelihood Explanation
The precondition — a signer rejecting for a transient reason and then reconsidering and accepting the same block — is a normally-occurring, documented event (not an edge case reachable only via malice), particularly during the burn-block-arrival convergence window that `docs/signer-flows.md` calls out as producing "No signer consensus reached" rejections that resolve within 5-10 seconds. Because `total_weight_rejected` is never decremented except on a full timeout/reset of the proposal (`reset_rejections`), any accumulation of such benign flip cases across the required ~30%+ weight of signers during that window is sufficient to trigger the false rejection, making this plausible in ordinary network operation rather than requiring an attacker-crafted majority.

### Recommendation
Track a single authoritative last-verdict-per-signer state (e.g., one map from `slot_id`/pubkey to `Accepted`/`Rejected`) instead of two independently-gated sets (`gathered_signatures` and `responded_signers`). When a signer's verdict flips from `Rejected` to `Accepted` (or vice versa), atomically move their weight between `total_weight_rejected` and `total_weight_approved` rather than allowing both to include it. Alternatively, gate the accept path on `responded_signers` the same way the reject path does, and when an accept supersedes a prior reject for the same signer, subtract that signer's weight from `total_weight_rejected` before adding it to `total_weight_approved`.

### Proof of Concept
Conceptual sequence (requires only ordinary signer behavior, no key compromise or majority):
1. Miner proposes block B. Signer S evaluates it while its sortition view has not yet converged and rejects with a transient reason (e.g. `SortitionViewMismatch`); `stackerdb_listener` adds S's weight to `total_weight_rejected` and `responded_signers`.
2. Shortly after, S's sortition view converges; per the documented "repeat my earlier answer, unless the reason I rejected has since gone away" logic, S re-evaluates B, validates it via its node, and signs it, broadcasting `Accepted`.
3. `stackerdb_listener`'s accept handler checks only `gathered_signatures` (empty for S), so it adds S's weight to `total_weight_approved` as well — S's weight is now counted in both totals.
4. If enough other signers follow the same rejected→accepted pattern during the same convergence window, `total_weight_rejected` (which is never decremented for these signers) can cross `total_weight - weight_threshold`, causing `gather_signatures` to return `Err(SignersRejected)` for block B even though those same signers have since signed it and the true current approval weight would meet the 70% threshold.

Exact code locations exercised: accept-weight gate [8](#0-7) , reject-weight gate [2](#0-1) , and the rejection-first coordinator check [9](#0-8) .

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L443-465)
```rust
                        if !block.gathered_signatures.contains_key(&slot_id) {
                            block.total_weight_approved = block
                                .total_weight_approved
                                .saturating_add(signer_entry.weight);

                            info!("StackerDBListener: Signature Added to block";
                                "signer_signature_hash" => %block_sighash,
                                "signer_pubkey" => signer_pubkey.to_hex(),
                                "signer_slot_id" => slot_id,
                                "signature" => %signature,
                                "signer_weight" => signer_entry.weight,
                                "total_weight_approved" => block.total_weight_approved,
                                "percent_approved" => block.total_weight_approved as f64 / self.total_weight as f64 * 100.0,
                                "total_weight_rejected" => block.total_weight_rejected,
                                "percent_rejected" => block.total_weight_rejected as f64 / self.total_weight as f64 * 100.0,
                                "weight_threshold" => self.weight_threshold,
                                "tenure_extend_timestamp" => tenure_extend_timestamp,
                                "read_count_extend_timestamp" => read_count_extend_timestamp,
                                "server_version" => metadata.server_version,
                            );
                        }
                        block.gathered_signatures.insert(slot_id, signature);
                        block.responded_signers.insert(slot_id);
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-518)
```rust
                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L706-722)
```rust
    /// Reset rejections for a block proposal.
    /// This is used when a block proposal times out and we need to retry it by
    /// clearing the block's rejections. Block approvals cannot be cleared
    /// because an old approval could always be used to make a block reach
    /// the approval threshold.
    pub fn reset_rejections(&self, signer_sighash: &Sha512Trunc256Sum) {
        let (lock, _cvar) = &*self.blocks;
        let mut blocks = lock.lock().expect("FATAL: failed to lock block status");
        if let Some(block) = blocks.get_mut(signer_sighash) {
            block.responded_signers.clear();
            block.total_weight_rejected = 0;

            // Add approving signers back to the responded signers set
            for (slot_id, _) in block.gathered_signatures.iter() {
                block.responded_signers.insert(*slot_id);
            }
        }
```

**File:** docs/signer-flows.md (L26-27)
```markdown
    P(["a miner proposes a block"]) --> SEEN{"have I already<br/>answered on this block?"}
    SEEN -- yes --> PRIOR(["repeat my earlier answer<br/>(unless the reason I rejected<br/>has since gone away)"])
```

**File:** docs/signer-flows.md (L186-188)
```markdown
    FRESH --> CHECK["check_block_against_state:<br/>protocol version consensus (NoSignerConsensus),<br/>static validity, no problematic_txs<br/>(ProblematicTransactions), then<br/>v1 SortitionsView::check_proposal or<br/>v2 GlobalStateView::check_proposal → section 7"]
    CHECK -- invalid --> REJ["send rejection<br/>(not stored)"]:::bad
    CHECK -- "not provably invalid" --> BUSY{"validation slot free?<br/>submitted_block_proposal"}
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-545)
```rust
            if block_status
                .total_weight_rejected
                .saturating_add(self.weight_threshold)
                > self.total_weight
            {
                info!(
                    "{}/{} signer weight votes to reject block",
                    block_status.total_weight_rejected, self.total_weight;
                    "signer_signature_hash" => %block_signer_sighash,
                );
                counters.bump_naka_rejected_blocks();

                // Only act on failed txids that a blocking minority (>30% weight) agrees on
                let blocking_minority = self.total_weight.saturating_sub(self.weight_threshold);
                let mut temporarily_excluded_txids = HashSet::new();
                let mut permanently_excluded_txids = HashSet::new();
                for (txid, info) in &block_status.failed_txids {
                    if info.total_weight > blocking_minority {
                        // Do not perma ban txids that only a small minority of signers reported as problematic
                        // But make sure its removed from the next block proposal
                        if info.problematic_weight > blocking_minority {
                            permanently_excluded_txids.insert(txid.clone());
                        } else {
                            temporarily_excluded_txids.insert(txid.clone());
                        }
                    }
                }

                return Err(NakamotoNodeError::SignersRejected {
                    temporarily_excluded_txids,
                    permanently_excluded_txids,
                });
            } else if block_status.total_weight_approved >= self.weight_threshold {
                info!("Received enough signatures, block accepted";
                    "signer_signature_hash" => %block_signer_sighash,
                );
                return Ok(block_status.gathered_signatures.values().cloned().collect());
```
