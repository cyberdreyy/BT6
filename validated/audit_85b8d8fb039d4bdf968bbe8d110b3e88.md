## Analysis Result

### Title
Node-side signer weight tallying double-counts a signer that first rejects then accepts a block, corrupting the rejected/approved equality - (File: stacks-node/src/nakamoto_node/stackerdb_listener.rs)

### Summary
The node-side `StackerDBListener`, which the `SignerCoordinator` uses to tally signer `BlockResponse` messages for a proposed block, tracks two independent counters (`total_weight_approved`, `total_weight_rejected`) guarded by two different dedup sets (`gathered_signatures` and `responded_signers`). Because the `Accepted` handler gates its weight increment on `gathered_signatures` rather than on `responded_signers`, a signer that legitimately transitions from a local rejection to a later local acceptance of the same block (a documented, valid state transition, `LocallyRejected -> LocallyAccepted : re-evaluated`) has its weight counted in **both** buckets. This corrupts the invariant that `total_weight_rejected` reflects only signers currently rejecting the block, letting the coordinator compute a false "blocking minority" (>30% weight rejected) using stale weight from a signer who has since moved to acceptance.

### Finding Description
`stackerdb_listener.rs` maintains a `BlockStatus` per proposed block: [1](#0-0) 

When an `Accepted` message arrives, the weight is added only if the slot is not already in `gathered_signatures`, and then the slot is inserted into *both* `gathered_signatures` and `responded_signers`: [2](#0-1) 

When a `Rejected` message arrives, the weight is added only if `responded_signers.insert(slot_id)` succeeds (i.e., the signer hasn't been seen before, in either direction): [3](#0-2) 

This ordering is asymmetric:
- If a signer **accepts first**, `responded_signers` is set at accept time, so a later `Rejected` message from the same signer is correctly ignored (the `responded_signers.insert` returns `false`).
- If a signer **rejects first** (`responded_signers.insert` succeeds, `total_weight_rejected += w`), and then later **accepts** the same block, the `Accepted` handler's guard only checks `gathered_signatures` (not yet containing the slot), so it happily adds `w` to `total_weight_approved` as well. The prior rejection weight is never subtracted from `total_weight_rejected`.

The v0 signer's own local state machine explicitly allows a signer to revise a rejection into an acceptance for the same block after re-evaluation (`BlockInfo::check_state` permits `LocallyRejected -> LocallyAccepted`), and the design documentation states that a signature is a "bearer instrument" that persists once given, while a rejection is merely "a revocable opinion." The coordinator's tally, however, treats the *rejection* as similarly permanent and irrevocable, even after the same signer supersedes it with an acceptance, producing an inflated and stale `total_weight_rejected`.

`SignerCoordinator::get_block_status` evaluates the rejection condition before the acceptance condition each iteration: [4](#0-3) 

Because `total_weight_rejected` can retain weight from signers who have since flipped to acceptance, the sum `total_weight_rejected + weight_threshold > total_weight` can become true (declaring `SignersRejected`, and permanently banning transactions those signers originally objected to via `permanently_excluded_txids`) even when the *current* signer-set decision, reflected by `total_weight_approved >= weight_threshold`, has genuinely passed. Since the rejection branch is checked first, a legitimately/validly signed block can be discarded by the miner as rejected.

### Impact Explanation
This breaks the equality between "aggregated weight" and "verified (current) accepts/rejects" that the coordinator relies on to decide a block's fate: a single signer's weight is recounted from the rejected bucket into the approved bucket without ever being retracted from the rejected bucket, per the analog's required impact category "a rejection recounted as an accept" (weight double-booked across mutually exclusive tallies). The practical consequence is a High-severity liveness issue: a tenure/miner can be wedged into discarding blocks that a genuine 70% weight of signers currently support, because stale rejection weight from signers who have since re-evaluated to acceptance crosses the 30% blocking-minority threshold first. It can also poison `permanently_excluded_txids` with weight from a rejection opinion that the issuing signer itself has since abandoned.

### Likelihood Explanation
This requires no majority collusion and no other signer's key — only a single signer (or several, additively) legitimately following the documented re-evaluation path (`LocallyRejected -> LocallyAccepted`) for one block, e.g. due to a benign re-proposal/rewind/timing event that first caused a local rejection and later, upon fresh evaluation, a local acceptance. This is an ordinary, expected occurrence in the signer state machine (explicitly documented as a first-class transition), not an edge case requiring active malice, making the likelihood of triggering it in production reasonably high whenever a rejection is later reconsidered.

### Recommendation
In `stackerdb_listener.rs`'s `Accepted` handler, gate the `total_weight_approved` increment (and ideally reverse any prior contribution to `total_weight_rejected`) on `responded_signers` rather than solely on `gathered_signatures`, and likewise ensure `total_weight_rejected` is adjusted (or the signer's slot excluded from the rejected tally) when that same signer later supplies a valid acceptance for the same block, so `total_weight_approved` and `total_weight_rejected` never both include the same signer's weight for the same block.

### Proof of Concept
1. Node proposes block `B`; signer set has slots `S1..Sn` with assorted weights, `weight_threshold = 70` out of `total_weight = 100`.
2. Signer `S1` (weight 35) initially evaluates `B` unfavorably and broadcasts `BlockResponse::Rejected` — `stackerdb_listener.rs` sets `responded_signers.insert(S1)`, `total_weight_rejected = 35`.
3. Other signers with combined weight 60 accept `B` normally — `total_weight_approved = 60` (below the 70 threshold, so the block is still pending in `get_block_status`).
4. `S1` (following its own documented `LocallyRejected -> LocallyAccepted` re-evaluation path) later re-evaluates and broadcasts `BlockResponse::Accepted` for the same `B`. `gathered_signatures` does not yet contain `S1`'s slot, so `total_weight_approved` becomes `60 + 35 = 95` (crossing threshold) while `total_weight_rejected` remains `35`.
5. Depending on message arrival order relative to the coordinator's polling loop in `get_block_status`, the still-nonzero `total_weight_rejected = 35` combined with any further rejections from other signers can independently satisfy `total_weight_rejected + weight_threshold > total_weight` on an earlier poll iteration (e.g. once one more signer of weight ≥ 5 additionally, currently, rejects), causing the miner to declare `SignersRejected` for a block that already has genuine, currently-valid 95-weight signer approval, discarding the block and permanently excluding any txid `S1` had originally flagged even though `S1` has withdrawn that objection. [5](#0-4)

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L70-82)
```rust
#[derive(Debug, Clone)]
pub struct BlockStatus {
    /// Set of the slot ids of signers who have responded
    pub responded_signers: HashSet<u32>,
    /// Map of the slot id of signers who have signed the block and their signature
    pub gathered_signatures: BTreeMap<u32, MessageSignature>,
    /// Total weight of signers who have signed the block
    pub total_weight_approved: u32,
    /// Total weight of signers who have rejected the block
    pub total_weight_rejected: u32,
    /// Per-txid rejection tracking from signers
    pub failed_txids: HashMap<Txid, FailedTxInfo>,
}
```

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

**File:** docs/signer-flows.md (L141-149)
```markdown
    PreCommitted --> LocallyAccepted : mark_locally_accepted = WE SIGN
    Unprocessed --> LocallyRejected : mark_locally_rejected
    PreCommitted --> LocallyRejected : mark_locally_rejected
    LocallyRejected --> LocallyAccepted : re-evaluated
    LocallyAccepted --> LocallyRejected : re-evaluated
    LocallyAccepted --> GloballyAccepted : mark_globally_accepted
    LocallyRejected --> GloballyRejected : mark_globally_rejected
    GloballyAccepted --> [*]
    GloballyRejected --> [*]
```
