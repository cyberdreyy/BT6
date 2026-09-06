### Title
Rejected-signer weight is never retracted on a later Accept, causing `total_weight_rejected` to permanently diverge from the true rejecting weight - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
The `BlockStatus` weight tally the mining node's `StackerDBListener` keeps for a proposed block tracks `total_weight_approved` and `total_weight_rejected` as two independent, monotonically-increasing counters. The two counters are updated with inconsistent duplicate-guards: the `Rejected` handler is guarded by `responded_signers` (a single global "have we heard from this signer at all" set), while the `Accepted` handler is guarded only by `gathered_signatures` (a "have we counted an accept from this signer" map). This asymmetry means a signer that first rejects and later legitimately re-evaluates to accept the same block has its weight counted in `total_weight_rejected` *forever*, in addition to being correctly counted in `total_weight_approved`. `total_weight_rejected` is never decremented anywhere in the file.

### Finding Description
`BlockStatus` is defined with independent counters and no reconciliation between them: [1](#0-0) 

In the `Accepted` arm, weight is added to `total_weight_approved` guarded solely by `gathered_signatures.contains_key(&slot_id)`: [2](#0-1) 

In the `Rejected` arm, weight is added to `total_weight_rejected` guarded by `block.responded_signers.insert(slot_id)`, which returns `false` (skipping the add) if the signer has already been recorded by *either* an earlier Accept or an earlier Reject: [3](#0-2) 

So the order of events matters:
- Accept → later Reject: the later Reject is a no-op (blocked by `responded_signers`), so no double count occurs, but this is only accidental protection.
- Reject → later Accept (the documented, expected re-evaluation path — the signer state machine explicitly allows `LocallyRejected --> LocallyAccepted: re-evaluated`, e.g. when a signer initially rejects due to a conflict that later goes stale and subsequently signs): the Accept arm does not consult `responded_signers`, so it happily adds the signer's weight to `total_weight_approved` **without ever subtracting it from `total_weight_rejected`.** The signer's weight is now simultaneously present in both totals, permanently.

This is structurally the same class of bug as the referenced report: a value that must be tracked consistently across a state transition (there, `Value` dropped from a cost sum when blob cost was added; here, rejected weight not retracted when an accept supersedes it) silently diverges from the quantity it's supposed to represent, and downstream consumers trust the wrong number.

The consumer of this stale total is the mining coordinator's decision loop, which checks the rejection bound *before* the acceptance bound: [4](#0-3) 

Because `total_weight_rejected` only grows and is checked with `saturating_add(self.weight_threshold) > self.total_weight`, stale rejection weight from signers who have since flipped to accept (a normal, honest, and expected occurrence per the signer state machine) accumulates across the lifetime of a single block proposal's tally and can push the aggregate over the ~30% blocking bound even though the *current* set of genuinely-still-rejecting signers is smaller.

### Impact Explanation
This breaks the equality the coordinator relies on: "aggregated rejected weight" should equal "weight of signers currently rejecting," but instead it equals "weight of signers who *ever* rejected, even if they since accepted." The result is a false-positive `SignersRejected` outcome for a block that could otherwise legitimately reach the acceptance threshold, forcing the miner to abandon a viable block, exclude transactions it should not exclude (`temporarily_excluded_txids` / `permanently_excluded_txids`), and retry — a liveness degradation reachable by ordinary, honest signer re-evaluation behavior (no majority, no key compromise, no StackerDB tampering required), consistent with the report's "value silently dropped from an accounting sum, corrupting a downstream safety/liveness check" pattern. It does not, however, let an invalid block be signed or push a false acceptance past the real signature-verification (the actual gathered signatures are still individually verified before being counted into `total_weight_approved`, and the resulting block's on-chain signature set is independently re-verified via `verify_signer_signatures`), so this does not rise to a Critical (invalid/non-canonical block signed, or accept-recount) finding — it is a coordinator-side liveness/DoS issue against block *production*, not chain safety.

### Likelihood Explanation
Requires only a single signer to reject-then-accept the same block proposal within one coordinator wait window — an explicitly supported and documented path in the signer state machine (`LocallyRejected --> LocallyAccepted: re-evaluated`), which happens whenever a signer's earlier rejection reason (e.g. a conflicting block, a stale reorg permit) resolves before the pre-commit/acceptance threshold is reached. No attacker collusion or majority control is needed; it can occur from perfectly honest signer behavior under normal chain-fork/timing conditions.

### Recommendation
Track each signer's current vote in a single map keyed by `slot_id` (accept/reject/none) instead of two independently-incremented counters, and derive `total_weight_approved` / `total_weight_rejected` from that map (or explicitly decrement the opposite counter when a signer's vote flips), so a signer's weight is attributed to exactly one bucket at any time — mirroring the general fix theme of the referenced report (ensure every quantity contributing to a safety/liveness check is kept in sync across all update paths, not duplicated by inconsistent guards).

### Proof of Concept
1. Miner proposes block `B`; coordinator opens a `BlockStatus` for `signer_signature_hash(B)`.
2. Signer `S` (weight `w`, `w` less than 30% of total weight) initially evaluates `B` against a conflicting locally-accepted block and broadcasts `BlockResponse::Rejected` → `stackerdb_listener` adds `w` to `total_weight_rejected` and marks `S` in `responded_signers`.
3. The conflict that caused `S`'s rejection becomes stale (per the documented `conflict_still_blocks` freshness logic), and `S`'s local state machine transitions `LocallyRejected → LocallyAccepted`, broadcasting `BlockResponse::Accepted` with a valid signature over `B`.
4. `stackerdb_listener`'s `Accepted` arm checks only `gathered_signatures.contains_key(&slot_id)` (false, since `S` never accepted before) and adds `w` to `total_weight_approved` — `total_weight_rejected` is left unchanged at `w`.
5. Repeat with enough other signers independently flipping reject→accept for the same block (a plausible scenario during resolving contested/forked proposals) until `Σ(stale rejected weights) + weight_threshold > total_weight`, even though the true current rejecting weight is 0.
6. `signer_coordinator.rs`'s wait loop hits the `total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight` branch and returns `NakamotoNodeError::SignersRejected`, aborting a block that has, or could still reach, full legitimate acceptance.

Note: I was not able to execute this end-to-end in a live cluster from the index alone; the trace above is fully supported by the cited source (guard-condition asymmetry between the `Accepted` and `Rejected` arms, and the documented reject→accept re-evaluation transition), but confirming exact timing windows in a running network would benefit from a Devin session with test-harness access.

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-518)
```rust
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
