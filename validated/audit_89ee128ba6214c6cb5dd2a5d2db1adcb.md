### Title
Miner-side signature/rejection tally lets a single signer's weight be double-counted toward both `total_weight_approved` and `total_weight_rejected` - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
This is the analog of the Merkle "arbitrary length / no strict structural check" bug class: just as the Merkle verifier failed to enforce a single canonical proof-length equality (letting an intermediate node be mistaken for a leaf), the miner-side vote tally in `StackerDBListener` uses two *different* guards to decide whether to add a signer's weight to the approval pool versus the rejection pool, instead of one canonical "has this signer already voted" check. This lets one signer's weight be credited to both `total_weight_approved` and `total_weight_rejected` for the same block, breaking the aggregated-weight-vs-verified-accepts invariant the coordinator relies on.

### Finding Description
In `handle_message` (stacks-node/src/nakamoto_node/stackerdb_listener.rs), the two branches that tally signer responses use inconsistent de-duplication keys:

- `BlockResponse::Accepted` branch gates weight addition on `block.gathered_signatures.contains_key(&slot_id)` [1](#0-0) 
- `BlockResponse::Rejected` branch gates weight addition on `block.responded_signers.insert(&slot_id)` [2](#0-1) 

The `Accepted` branch also inserts the slot into `responded_signers` (line 465), but only *after* an accept; it never checks `responded_signers` before crediting weight. So the two code paths do not share one single "already voted" gate:

- If a signer Rejects first: `responded_signers` gets the slot, `total_weight_rejected` is incremented, but `gathered_signatures` is **not** touched (only the `Accepted` branch touches that map).
- If that same signer later sends an Accept for the *same* block (e.g., a stale/duplicate resend after a state re-evaluation, a race between its own rejection and a later re-check, or a StackerDB replay), the `Accepted` branch checks only `gathered_signatures.contains_key(&slot_id)`, which is still empty — so it happily adds the signer's weight to `total_weight_approved` as well.

The result: the same signer's weight is now present in **both** `total_weight_approved` and `total_weight_rejected` simultaneously. This breaks the disjointness the coordinator assumes when computing the two mutually-exclusive termination conditions:

```
block_status.total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight   // reject
block_status.total_weight_approved >= self.weight_threshold                                     // accept
``` [3](#0-2) 

Because `insert_block` initializes fresh, disjoint counters per block and `reset_rejections` is the only place that explicitly re-syncs `responded_signers` with `gathered_signatures` [4](#0-3) , that repair only happens on a proposal-timeout retry — not on the ordinary reject-then-accept sequence described above. The mismatch is a direct parallel to the reported class: an equality/uniqueness check ("has this signer's weight already been counted?") that is supposed to be one canonical predicate is implemented with two different, non-interchangeable "proofs" (`gathered_signatures` vs `responded_signers`), and an attacker (or even an ordinary flip-flopping signer) can satisfy the weaker of the two checks to get double credit.

### Impact Explanation
This directly matches the in-scope "aggregated-weight vs verified-accepts" equality break. A single signer's weight ends up counted in both the approval and rejection tallies for one block, which can:
- Push `total_weight_approved` over the 70% threshold using weight that is simultaneously counted against the block in `total_weight_rejected`, causing the miner to treat the block as accepted (and push it to the node) when the actual number of *distinct* approving signers/weight is lower than the protocol's 70% supermajority requirement.
- Conversely, contribute to a spurious "blocking minority" rejection using weight that also appears as an approval.

Either direction corrupts the supermajority accounting that the two-round (pre-commit/threshold) design in `docs/signer-flows.md` is built to guarantee, i.e., "a block that will never reach 70% rarely collects stray signatures" [5](#0-4) . This falls under Critical because it can let the block-acceptance threshold be satisfied by miscounted weight rather than genuinely-verified distinct-signer accepts.

### Likelihood Explanation
It only requires ordinary message traffic from a single signer's slot — no majority, no other signer's key, and no auth token — matching the allowed threat model (a one-slot miner/gossip observer triggering it). The trigger is any legitimate or malicious Reject-then-Accept sequence for the same `signer_signature_hash` from one signer (e.g., a signer that rejects on an initial view, then later re-evaluates and legitimately accepts the same block hash once conditions change, or a malicious/faulty signer that deliberately resends conflicting `BlockResponse`s). Because the state machine in `signer.rs` does permit late-arriving/duplicate/replayed responses to be reprocessed (see `handle_block_response`/pending-response replay flow in `docs/signer-flows.md` section 6), a same-hash reject followed by an accept is a realistic, protocol-visible sequence, not merely a theoretical corner case.

### Recommendation
Use one canonical de-duplication set for both branches. Concretely, gate weight-crediting in the `Accepted` branch on the same `responded_signers` check used by `Rejected` (i.e., only add `total_weight_approved` if `responded_signers.insert(&slot_id)` is true, and if the slot was previously counted under `total_weight_rejected`, subtract that weight or refuse to switch state), and make `gathered_signatures` a data cache rather than the authority for whether weight was already tallied. Enforce a single "first (and only) counted response per slot" invariant across both accept and reject paths, and add a corresponding regression test that sends Reject-then-Accept from the same slot and asserts the two weight totals remain disjoint (`total_weight_approved + total_weight_rejected <= total_weight`).

### Proof of Concept
1. Coordinator starts collecting responses for block `B`, `insert_block(B)` initializes `total_weight_approved = 0`, `total_weight_rejected = 0`, empty `gathered_signatures`/`responded_signers` [6](#0-5) .
2. Signer `S` (weight `w`) sends `BlockResponse::Rejected` for `B`. `responded_signers.insert(S)` succeeds → `total_weight_rejected += w`. `gathered_signatures` untouched. [2](#0-1) 
3. `S` later sends `BlockResponse::Accepted` with a valid signature for the same `B` (e.g., after re-evaluating and legitimately/duplicately deciding to accept, or by simply replaying/crafting a second message). `gathered_signatures.contains_key(S)` is `false` (never set by the reject path), so the check at line 443 passes and `total_weight_approved += w`. [1](#0-0) 
4. Now `S`'s weight `w` is counted in both `total_weight_approved` and `total_weight_rejected`. If other signers push `total_weight_approved` to just under `weight_threshold`, `S`'s double-counted weight can tip `total_weight_approved >= weight_threshold` in `signer_coordinator.rs`, causing the coordinator to treat the block as accepted [7](#0-6)  even though `S`'s weight is simultaneously on record as a rejection, meaning the real distinct approving weight is below the 70% supermajority the protocol requires.

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L692-704)
```rust
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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L706-723)
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

**File:** docs/signer-flows.md (L56-60)
```markdown
  A signer that cannot yet safely sign says nothing rather than rejecting.
- **Nobody signs alone.** The pre-commit round means a signer only spends its
  signature once it knows a supermajority intends to spend theirs, so a block
  that will never reach 70% rarely collects stray signatures at all.
- **Adoption is the ground truth.** Reaching 70% signatures makes a block
```
