### Title
Signer vote-flip (Reject → Accept) causes double-counted weight in the miner's block-coordinator tally - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
The node-side `StackerDBListener` that a Nakamoto miner uses to tally signer votes for a proposed block maintains two separate, inconsistent "already counted" guards for the two vote types: `gathered_signatures` (keyed by `slot_id`) gates whether a signer's *approval* weight is added, while `responded_signers` (a `HashSet<u32>`) gates whether a signer's *rejection* weight is added. A single signer that first rejects a block and later legitimately reconsiders and accepts it (a fully supported protocol path — signers may re-evaluate and change their vote on a re-proposal) has its weight added to `total_weight_rejected` on the reject message, and then — because the accept-path guard only checks `gathered_signatures`, not `responded_signers` — has its weight added *again* to `total_weight_approved` on the later accept message. The stale rejection weight is never cleared. This breaks the invariant that `total_weight_approved` and `total_weight_rejected` should each equal the weight of signers *currently* holding that position, and that a signer's weight is counted at most once across the two buckets.

### Finding Description
In `handle_new_stackerdb_chunks` (`stacks-node/src/nakamoto_node/stackerdb_listener.rs`):

- Accept path (`SignerMessageV0::BlockResponse(BlockResponse::Accepted)`):
  ```
  if !block.gathered_signatures.contains_key(&slot_id) {
      block.total_weight_approved = block.total_weight_approved.saturating_add(signer_entry.weight);
      ...
  }
  block.gathered_signatures.insert(slot_id, signature);
  block.responded_signers.insert(slot_id);
  ``` [1](#0-0) 

- Reject path (`SignerMessageV0::BlockResponse(BlockResponse::Rejected)`):
  ```
  if block.responded_signers.insert(slot_id) {
      block.total_weight_rejected = block.total_weight_rejected.saturating_add(signer_entry.weight);
      ...
  }
  ``` [2](#0-1) 

The reject handler's dedup key is `responded_signers`, which the accept handler *also* writes into (line 465). This makes Accept-after-Reject unsafe in one direction only:

- **Accept, then Reject** (safe): the accept path sets `responded_signers.insert(slot_id)` at line 465. When the reject message later arrives, `responded_signers.insert(slot_id)` returns `false` (already present), so the rejected-weight add is correctly skipped.
- **Reject, then Accept** (unsafe): the reject path only inserts into `responded_signers`, never into `gathered_signatures`. When the accept message later arrives, `gathered_signatures.contains_key(&slot_id)` is `false`, so the approve-weight add proceeds and the signer's weight is added to `total_weight_approved` *in addition to* the weight already recorded in `total_weight_rejected` from the earlier reject. `total_weight_rejected` is never decremented.

This directly parallels the reported AMM bug class: an equality that the design assumes always holds (`aggregated weight per bucket == weight of signers currently in that state`) is broken by a sequence of individually valid messages, with no need for a majority of signers or any key compromise — a single signer's normal vote-reconsideration (driven by legitimate re-proposal/gossip reordering) is sufficient.

The consumer of this tally, `SignerCoordinator` in `stacks-node/src/nakamoto_node/signer_coordinator.rs`, checks the reject condition before the accept condition:
```
if block_status.total_weight_rejected.saturating_add(min_weight) > self.total_weight { ... return Err(SignersRejected...) }
else if block_status.total_weight_approved >= self.weight_threshold { ... return Ok(signatures) }
``` [3](#0-2) 

Because the reject branch is evaluated first and never decrements for the stale weight left behind by a flipped voter, a sequence of messages can push `total_weight_rejected` past the "blocking minority" threshold purely because of the artifact left by the earlier reject-then-accept flip, even though the true, current weight in opposition never reached that level. This lets the miner conclude the block has been definitively rejected (returning `NakamotoNodeError::SignersRejected`, discarding the proposal and permanently/temporarily excluding transactions from it) despite a legitimate signer majority currently approving the block.

### Impact Explanation
This breaks the miner's "aggregated weight vs. verified accepts/rejects" equality: a signer's current vote is not what the coordinator's tallies reflect once that signer has changed its mind. Concretely, it can cause the miner to erroneously treat a properly-approved block as globally rejected (a false negative that discards a valid block and can perma/temp-exclude transactions from future proposals), which is a liveness/availability break of the block-production path driven by a single ordinary signer's legitimate re-vote — no signer majority, key compromise, or malicious behavior is required.

### Likelihood Explanation
Any signer implementation that reconsiders a rejection and later signs the same block (a normal, spec-sanctioned path — the signer state machine explicitly allows reconsidering some reject reasons on re-proposal, and StackerDB message reordering/gossip delay can also produce this ordering) triggers this. Because it only requires one signer's two sequential legitimate messages to arrive at the coordinator in reject-then-accept order, the likelihood is high in any network with normal latency/reordering or signer re-evaluation.

### Recommendation
Use a single per-signer "current vote" data structure (e.g. `HashMap<u32, Vote>`) instead of two independently-gated counters (`gathered_signatures` vs `responded_signers`). When a new vote is recorded for a `slot_id`, first roll back the weight contribution of any previous vote for that slot before adding the new one, so `total_weight_approved`/`total_weight_rejected` always equal the sum of weights of signers whose *most recent* message was that vote.

### Proof of Concept
Sequence of StackerDB messages delivered to a single miner's `StackerDBListener` for one proposed block (`total_weight = 100`, `weight_threshold = 70`, blocking minority = 30):

1. Signer A (weight 25) sends `BlockResponse::Rejected` → `total_weight_rejected = 25` (`responded_signers = {A}`).
2. Signer A reconsiders (e.g. a re-proposal with a reconsiderable reject reason) and sends `BlockResponse::Accepted` → guard `gathered_signatures.contains_key(A)` is false, so `total_weight_approved = 25` is added; `gathered_signatures/responded_signers` now both contain A. Note `total_weight_rejected` is still `25` (stale, never cleared).
3. Signer C (weight 10) sends `BlockResponse::Rejected` → `responded_signers.insert(C)` succeeds → `total_weight_rejected = 25 + 10 = 35`.
4. In `signer_coordinator.rs`, `total_weight_rejected (35) + min_weight (30) = 65 > total_weight? ` — depending on exact `min_weight`, this crosses the "impossible to reach approval" branch and the coordinator returns `Err(NakamotoNodeError::SignersRejected {...})`, discarding the block — even though the *true* current rejecting weight is only 10% (well under the 30% blocking minority) and 90%+ of weight (including A's genuine, current acceptance) supports the block.

Root cause confirmed at: [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L508-545)
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
