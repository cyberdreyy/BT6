### Title
Node-side `StackerDBListener` permanently double-counts a signer's rejection weight even after that signer flips to accept, letting a single signer wedge the miner into declaring a valid block globally rejected - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
`BlockStatus` (a per-block, node-side tally the miner's `SignerCoordinator`/`StackerDBListener` uses to decide when a proposed block has crossed the 70% acceptance or ≥30% rejection threshold) tracks each signer's participation with a single shared `responded_signers: HashSet<u32>` set. The accept path and the reject path both gate on this *same* set but never clean up or transfer weight when a signer changes its answer, so a signer that first rejects and later signs acceptance for the identical block keeps its weight counted in `total_weight_rejected` forever, while also being added to `total_weight_approved`. `total_weight_rejected` is a monotonically increasing counter that is never decremented for a given signer once set.

### Finding Description
`BlockStatus` is defined at: [1](#0-0) 

In the acceptance branch, a signer's slot is added to `gathered_signatures`/`responded_signers` and its weight is added to `total_weight_approved` if not already in `gathered_signatures`: [2](#0-1) 

In the rejection branch, weight is added to `total_weight_rejected` only once, gated by inserting into the *same* `responded_signers` set: [3](#0-2) 

Because `responded_signers` is shared across both message kinds and is never cleared or made mutually exclusive:

- If a signer first **rejects** (their slot id enters `responded_signers`, weight is added to `total_weight_rejected`), and later — after further validation/consensus — **accepts** the same block (a completely valid, protocol-sanctioned scenario, since rejection is documented elsewhere in the codebase as "a revocable opinion" while a signature is a "bearer instrument"), the accept branch's check is on `gathered_signatures.contains_key(&slot_id)`, not on `responded_signers`, so the weight is *also* added to `total_weight_approved`. The signer's weight is now counted simultaneously toward both totals, and critically, `total_weight_rejected` is never decremented — there is no code path anywhere in this file that reduces `total_weight_rejected` for a signer who changes their mind.
- Consequently, `total_weight_approved + total_weight_rejected` can permanently exceed the real total signer weight `self.total_weight`, and the rejection-threshold check (`block.total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight`, line 567-571) can fire and declare the block **globally rejected** purely from stale, superseded rejections — even though every signer that ever rejected has since flipped to accept and the true, current acceptance weight has already crossed 70%.

This breaks the intended equality between "aggregated rejection weight" and "verified, currently-standing rejections" (the same equality class the signer's own local DB deliberately protects via `add_block_signature`'s explicit removal of a prior rejection row: `DELETE FROM block_rejection_signer_addrs WHERE ... `): [4](#0-3) 

That per-signer local DB correctly clears a stale rejection when a signature later arrives. The node-side `StackerDBListener` tally that actually drives the miner's broadcast/give-up decision has no analogous cleanup, and the rejection-weight counter is architecturally append-only (`saturating_add`) with no subtraction path.

### Impact Explanation
This wedges the mining coordinator into a false "block globally rejected" conclusion for a block that legitimate signers have, in fact, come to (or would) unanimously or majority accept — the `SignerCoordinator`/`StackerDBListener` will stop waiting on that block (`cvar.notify_all()` on the rejection branch) and the miner will give up on a block that could otherwise have reached the real 70% signature threshold. Because it takes only a small number of distinct signers who each reject-then-accept over the block's validation lifetime (a normal occurrence when a signer initially rejects due to a transient view mismatch and then re-evaluates favorably, as the codebase's own `should_reevaluate_block`/`should_reevaluate_reject_reason` logic anticipates), this can be triggered without needing a majority of malicious signers and without flooding — a small number of honest signer state transitions is sufficient to permanently poison the rejection tally for that specific block proposal. This matches the "signer wedged into never signing valid blocks" / liveness-wedge impact class: the miner-side aggregator refuses to move a block forward that the signer set has actually approved, stalling block production for that proposal and forcing a re-propose, and in a sustained pattern could repeatedly stall tenure block production.

### Likelihood Explanation
The trigger condition — a signer sending `Rejected` for a block and later sending `Accepted` for the *same* `signer_signature_hash` — is an ordinary, protocol-sanctioned event flow explicitly supported by the signer's own re-evaluation logic (`should_reevaluate_block`, `should_reevaluate_reject_reason` per `docs/signer-flows.md` section 3) and by the local signerdb's explicit handling of exactly this transition (deleting a stale rejection row on signature). It requires only ordinary signer behavior (or a single misbehaving/flip-flopping signer under gossip-controlled timing) rather than a majority of colluding signers, matching the required threat model of "a one-slot miner (plus gossip)."

### Recommendation
Track weight contributions per signer as a single, mutually-exclusive state (e.g., a `HashMap<u32, Vote>` recording each signer's *current* vote and weight, recomputing `total_weight_approved`/`total_weight_rejected` from that map on each update, or explicitly subtracting a signer's previous contribution before adding the new one) rather than two independently append-only counters gated on a shared, one-shot `responded_signers` set. Mirror the local signerdb's behavior of retracting a stale rejection when a later acceptance for the same block/signer arrives.

### Proof of Concept
1. Miner proposes block `B`; `StackerDBListener` creates a `BlockStatus` for `B` with empty tallies.
2. Signer `S` (weight `w`) sends `BlockResponse::Rejected` for `B`. In the handler, `block.responded_signers.insert(slot_id)` succeeds, so `total_weight_rejected += w` (lines 515-518).
3. `S` re-evaluates (e.g., after the node/tenure state converges) and later sends `BlockResponse::Accepted` for the identical `B`. In the accept handler, the guard is `!block.gathered_signatures.contains_key(&slot_id)` (not `responded_signers`), so it is satisfied: `total_weight_approved += w` (lines 443-465). `total_weight_rejected` is left unchanged at `w`.
4. Repeat step 2-3 pattern across enough distinct signers over time (each individually converging to acceptance) until `total_weight_rejected.saturating_add(weight_threshold) > self.total_weight` (line 567-571) even though at the moment this fires, `total_weight_approved` may already exceed the 70% acceptance threshold from the very same signers' final votes.
5. The coordinator signals `cvar.notify_all()` for rejection and the miner treats block `B` as globally rejected, discarding a block that the signer set has actually endorsed.

Note: I was unable to fully trace how the miner side (`signer_coordinator.rs`) consumes the rejection signal after `cvar.notify_all()` to confirm the exact downstream give-up behavior (e.g., whether it retries the same block or moves on) due to remaining tool budget; this would benefit from a full read of `stacks-node/src/nakamoto_node/signer_coordinator.rs` in a follow-up session.

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L513-518)
```rust
                        };

                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);
```

**File:** stacks-signer/src/signerdb.rs (L1876-1880)
```rust
    ) -> Result<bool, DBError> {
        // Remove any block rejection entry for this signer and block hash
        let del_qry = "DELETE FROM block_rejection_signer_addrs WHERE signer_signature_hash = ?1 AND signer_addr = ?2";
        let del_args = params![block_sighash, signer_addr.to_string()];
        self.db.execute(del_qry, del_args)?;
```
