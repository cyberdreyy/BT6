### Title
Miner's `StackerDBListener` double-counts a signer who rejects then later accepts, corrupting the aggregated-weight tally - (File: stacks-node/src/nakamoto_node/stackerdb_listener.rs)

### Summary
`StackerDBListenerComms` tracks `total_weight_approved` and `total_weight_rejected` per block independently, keyed only by whether a signer's slot is present in `gathered_signatures` / `responded_signers`. When a signer first rejects a block and later reconsiders and accepts the same block (a supported flow — CHANGELOG notes "a signer will reconsider a block proposal that it previously rejected"), the rejected weight is never removed. The signer's weight ends up counted in *both* the approved and rejected tallies, breaking the invariant that `total_weight_approved + total_weight_rejected` (over the set of distinct decided signers) cannot exceed the total signer weight.

### Finding Description
In the `Accepted` branch of `stackerdb_listener.rs`'s message loop, weight is added to `total_weight_approved` guarded only by `!block.gathered_signatures.contains_key(&slot_id)`, with no check of whether that `slot_id` is already present in `responded_signers` (i.e., already recorded as a rejector): [1](#0-0) 

Compare this with the `Rejected` branch, which correctly guards against double counting by only adding rejection weight `if block.responded_signers.insert(slot_id)` returns `true` (i.e. the slot has never responded before, whether by accept or reject): [2](#0-1) 

So the two branches are asymmetric:
- Accept-then-Reject: correctly suppressed (rejection branch checks `responded_signers`, which was already set by the acceptance).
- Reject-then-Accept: **not** suppressed — the acceptance branch never checks `responded_signers`, only `gathered_signatures`, so it unconditionally adds the signer's weight to `total_weight_approved` even though that same signer's weight is still counted in `total_weight_rejected` from the earlier rejection. The stale rejection weight is never decremented.

This mirrors the `EthLexscrow` pattern from the external report: a per-party accumulator (`amountDeposited` there, `total_weight_rejected`/`responded_signers` here) is not cleared when the party's earlier action is superseded by a later, different action from the same party, leaving stale accounting that no longer reflects reality.

The consuming code in `signer_coordinator.rs` treats these two weight counters as if they partition the signer set: [3](#0-2) 

Because of the bug, `total_weight_rejected` can remain inflated by weight that has actually moved to acceptance, and the sum `total_weight_approved + total_weight_rejected` can exceed `self.total_weight` (the real signer set weight) for a single reconsidering signer.

### Impact Explanation
This breaks the aggregated-weight vs. verified-accepts equality that the reward-threshold/rejection-threshold logic in `signer_coordinator.rs` depends on. Concretely:
- If a signer rejects, and later (per the supported reconsideration flow) accepts the same block, the block can simultaneously appear closer to the >30%-weight rejection threshold (stale rejected weight) and closer to the ≥70% approval threshold (fresh accepted weight) than the real, current signer decisions warrant.
- In a scenario where a signer's weight is significant (e.g., signers running close to threshold boundaries), this stale double count can push `total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight` to trip even though the signer no longer rejects, causing the miner to spuriously treat the block as rejected by a "blocking minority" that does not actually exist at that moment — or conversely can make the approval threshold appear reached earlier than the actual distinct-signer count justifies, since `total_weight_approved` no longer represents a disjoint subset of signer weight from `total_weight_rejected`. This is a miscounted-response-class safety break on the miner's tally of signer decisions (aggregated-weight vs. verified-accepts equality) reachable by a single signer's ordinary message sequence (reject, then reconsider-and-accept) plus normal StackerDB gossip, requiring no majority collusion.

### Likelihood Explanation
Triggering this requires only a single one-slot participant (the reporting signer itself) to send a `Rejected` message for a block hash followed later by an `Accepted` message for the *same* `signer_signature_hash` — a state transition explicitly supported by the signer's own reconsideration logic (`store_and_process_block_rejection` / `process_pending_responses_for_block` allow a stored rejection to be followed by processing of a later signature for the same block). No majority collusion, no other signer's key, and no node/auth access is needed; it only requires the natural request/gossip path already exercised by `stackerdb_listener.rs`.

### Recommendation
When processing a `BlockAccepted` message, check whether the signer's slot is already present in `responded_signers` as a rejection and, if so, first subtract (`saturating_sub`) that signer's weight from `total_weight_rejected` before adding it to `total_weight_approved` (mirroring the guard already present in the `Rejected` branch). Equivalently, track each slot's current decision in a single map (e.g., `HashMap<slot_id, Decision>`) and derive `total_weight_approved`/`total_weight_rejected` from that map rather than maintaining separately-incrementing counters, so a slot's weight is provably a member of exactly one bucket at any time.

### Proof of Concept
1. Miner proposes block B (`signer_signature_hash = H`) and calls `insert_block`, initializing `BlockStatus` with zeroed tallies: [4](#0-3) 
2. Signer S (slot `k`, weight `w`) sends `BlockResponse::Rejected` for `H`. In the `Rejected` branch, `responded_signers.insert(k)` succeeds (first response), so `total_weight_rejected += w`.
3. Signer S later reconsiders (a supported flow) and sends `BlockResponse::Accepted` for the same `H`. In the `Accepted` branch, the code checks only `gathered_signatures.contains_key(&k)` (false, since S never accepted before), so `total_weight_approved += w` executes unconditionally, and `responded_signers.insert(k)` is called again (no-op, already present) at line 465.
4. Now `block.total_weight_rejected == w` (stale) and `block.total_weight_approved == w` (fresh) for the *same* signer S — S's weight `w` is counted in both tallies, and `total_weight_approved + total_weight_rejected` for this single signer already equals `2w`, i.e., double S's actual weight, corrupting the sums used in `signer_coordinator.rs`'s threshold checks. [5](#0-4)

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-519)
```rust
                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);

```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L691-704)
```rust
impl StackerDBListenerComms {
    /// Insert a block into the block status map with initial values.
    pub fn insert_block(&self, block: &NakamotoBlockHeader) {
        let (lock, _cvar) = &*self.blocks;
        let mut blocks = lock.lock().expect("FATAL: failed to lock block status");
        let block_status = BlockStatus {
            responded_signers: HashSet::new(),
            gathered_signatures: BTreeMap::new(),
            total_weight_approved: 0,
            total_weight_rejected: 0,
            failed_txids: HashMap::new(),
        };
        blocks.insert(block.signer_signature_hash(), block_status);
    }
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

**File:** stacks-signer/CHANGELOG.md (L176-180)
```markdown
## [3.1.0.0.8.0]

### Changed

- For some rejection reasons, a signer will reconsider a block proposal that it previously rejected ([#5880](https://github.com/stacks-network/stacks-core/pull/5880))
```
