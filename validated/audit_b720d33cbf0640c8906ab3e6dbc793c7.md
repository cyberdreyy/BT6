### Title
Stale rejection weight is never cleared when a signer switches its vote to Accept, letting `total_weight_rejected` and `total_weight_approved` double-count the same signer - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
The miner-side `StackerDBListener` tallies signer weight for a proposed block into two separate counters, `total_weight_approved` and `total_weight_rejected`. The dedup guard used to decide "have we already counted this signer's weight" differs between the two branches: the `Accepted` branch guards on `gathered_signatures` (a map that only the `Accepted` branch writes to), while the `Rejected` branch guards on `responded_signers` (a set shared by both branches). Because a `Reject`-then-`Accept` sequence from the same signer is not symmetric with an `Accept`-then-`Reject` sequence, a signer who first rejects and later accepts gets its weight added to *both* tallies, and the stale rejection weight is never removed. This is the same root-cause pattern as the ILOPool report: a per-identity cap/tally is bypassed because the code checks "have we already recorded this identity's contribution" using the wrong/incomplete piece of state, letting one identity's contribution be double-booked into two mutually-exclusive buckets.

### Finding Description
In the `StackerDBListener` message loop:

- On `BlockResponse::Accepted`: weight is only added to `total_weight_approved` `if !block.gathered_signatures.contains_key(&slot_id)`, after which `gathered_signatures.insert(slot_id, signature)` and `responded_signers.insert(slot_id)` both run. [1](#0-0) 

- On `BlockResponse::Rejected`: weight is only added to `total_weight_rejected` `if block.responded_signers.insert(slot_id)` (i.e., the first time this slot is seen in `responded_signers` at all, regardless of `gathered_signatures`). [2](#0-1) 

Trace the two possible orderings for one slot/signer on one block:

1. **Accept, then Reject** (safe): Accept adds weight to `total_weight_approved` and inserts the slot into both `gathered_signatures` and `responded_signers`. The later Reject checks `responded_signers.insert(slot_id)`, which returns `false` because the slot is already present, so no weight is added to `total_weight_rejected`. Correctly deduplicated.

2. **Reject, then Accept** (unsafe): Reject inserts the slot into `responded_signers` (first time -> `true`) and adds weight to `total_weight_rejected`. `gathered_signatures` is untouched by the Reject branch. The later Accept checks `gathered_signatures.contains_key(&slot_id)`, which is `false` (never touched), so it proceeds to add the *same* signer's weight to `total_weight_approved` as well - with no code path that ever subtracts the earlier contribution from `total_weight_rejected`.

The net effect: after a signer flips its vote from Reject to Accept, its weight is counted in `total_weight_rejected` forever and now also counted in `total_weight_approved`. `total_weight_approved + total_weight_rejected` can now exceed `self.total_weight`, i.e., the equality "each signer's weight counts toward at most one of {approved, rejected}" is broken - exactly analogous to the ILOPool bug where the same underlying investor's contribution was double-counted across two positions because the code checked identity via a mutable/transferable proxy (`balanceOf(recipient)`/NFT ownership) instead of a durable per-investor ledger.

### Impact Explanation
This coordinator logic drives the miner/coordinator decision of whether a block was globally rejected or approved: [3](#0-2) 
If `total_weight_rejected` (inflated with stale weight from signers who have since switched to Accept) plus the blocking-minority margin exceeds `total_weight`, the coordinator declares the block globally rejected via `NakamotoNodeError::SignersRejected`, even though those very signers' current votes (now counted correctly in `total_weight_approved`) may mean the block has genuinely reached the 70% approval threshold. This is a liveness wedge on the miner side: a block that is legitimately signed by ≥70% of weight can be discarded/retried by the miner because of stale, never-cleared rejection weight left over from an earlier vote by the same signers. It can also incorrectly trigger transaction-exclusion logic (`temporarily_excluded_txids`/`permanently_excluded_txids`) based on a rejection tally that no longer reflects the signers' current stance.

### Likelihood Explanation
Triggering this requires only a single signer (whether faulty, restarted, or simply re-evaluating after a chainstate re-check per the documented pre-commit flow) to send a `Rejected` `BlockResponse` for a block and later send an `Accepted` `BlockResponse` for the *same* block hash over StackerDB - both ordinary, permissionless message types any one signer can emit via gossip, with no majority coordination required. Signers in this codebase are explicitly documented to reconsider and re-issue responses for the same block (e.g., re-running chainstate checks at pre-commit time can flip a prior decision), making a reject→accept sequence for the same `signer_signature_hash` a realistic occurrence, not merely a contrived attack.

### Recommendation
Use a single, consistent per-slot "already counted" tracking structure for both branches (e.g., always gate weight addition to either bucket on `responded_signers`, and when a signer's classification changes, actively move its weight from `total_weight_rejected` to `total_weight_approved` instead of only guarding future insertions). Concretely, on `Accepted`, if the slot is already present in `responded_signers` because of a prior rejection, subtract `signer_entry.weight` from `total_weight_rejected` before adding it to `total_weight_approved`, mirroring the ILOPool remediation of tracking cumulative state per durable identity rather than per mutable/latest-seen proxy.

### Proof of Concept
Given a reward set with signer S (weight w) and a proposed block B:
1. S broadcasts `BlockResponse::Rejected(B)`. `stackerdb_listener.rs` runs the `Rejected` arm: `responded_signers.insert(slot_S)` returns `true`, so `total_weight_rejected += w`. [4](#0-3) 
2. S (or its process, after re-evaluating the pre-commit/chainstate checks as documented for normal signer operation) broadcasts `BlockResponse::Accepted(B)` with a valid signature. `stackerdb_listener.rs` runs the `Accepted` arm: `gathered_signatures.contains_key(slot_S)` is `false` (never set by the Rejected arm), so `total_weight_approved += w` as well. [1](#0-0) 
3. Now `total_weight_rejected` still includes `w` from step 1, and `total_weight_approved` also includes `w` from step 2, so `total_weight_approved + total_weight_rejected > total_weight` by `w`. If enough signers repeat this reject→accept sequence, `total_weight_rejected` can independently cross the blocking-minority threshold in `signer_coordinator.rs`, causing the coordinator to report `SignersRejected` for a block that has, by current votes, actually reached the 70% approval weight.

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L486-518)
```rust
                    SignerMessageV0::BlockResponse(BlockResponse::Rejected(rejected_data)) => {
                        let (lock, cvar) = &*self.blocks;
                        let mut blocks = lock.lock().expect("FATAL: failed to lock block status");

                        let Some(block) = blocks.get_mut(&rejected_data.signer_signature_hash)
                        else {
                            info!(
                                "StackerDBListener: Received rejection for block that we did not request. Ignoring.";
                                "signer_signature_hash" => %rejected_data.signer_signature_hash,
                                "slot_id" => slot_id,
                                "signer_set" => self.signer_set,
                            );
                            continue;
                        };

                        let rejected_pubkey = match rejected_data.recover_public_key() {
                            Ok(rejected_pubkey) => {
                                if rejected_pubkey != signer_pubkey {
                                    warn!("StackerDBListener: Recovered public key from rejected data does not match signer's public key. Ignoring.");
                                    continue;
                                }
                                rejected_pubkey
                            }
                            Err(e) => {
                                warn!("StackerDBListener: Failed to recover public key from rejected data: {e:?}. Ignoring.");
                                continue;
                            }
                        };

                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-521)
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
```
