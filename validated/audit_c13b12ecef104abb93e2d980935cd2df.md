### Title
Stale rejection weight is never retracted when a signer re-evaluates Rejected → Accepted, permanently inflating `total_weight_rejected` - (File: stacks-node/src/nakamoto_node/stackerdb_listener.rs)

### Summary
The signer state machine explicitly allows a locally-rejected block proposal to be re-evaluated and later locally accepted (signed) once the reason for rejection no longer applies. The node-side aggregator that a miner polls to decide whether a block has reached consensus, however, tracks acceptance weight and rejection weight in two independently-gated counters that are only ever incremented, never decremented. A single signer's rejection therefore survives in the tally forever, even after that same signer subsequently signs and accepts the identical block. This is the same class of bug as the Nouns Builder report: a running total (`totalSupply` there, `total_weight_rejected` here) is incremented on one event but never corrected when that event is superseded, permanently skewing a percentage/threshold calculation that downstream logic relies on.

### Finding Description
The `BlockInfo` state machine in the signer explicitly permits a local verdict to flip in either direction while the block is not yet globally finalized: [1](#0-0) 

and the accompanying documentation confirms this is by design ("re-evaluated" transitions both ways between `LocallyAccepted` and `LocallyRejected`): [2](#0-1) 

On the node/miner side, `StackerDBListener` aggregates these per-signer verdicts into a shared `BlockStatus` used by the mining coordinator to decide whether a block is signed or dead: [3](#0-2) 

For an `Accepted` response, the weight is added only once, gated on `gathered_signatures` for that `slot_id`: [4](#0-3) 

For a `Rejected` response, the weight is added only once, gated on a *separate* set, `responded_signers`, for that `slot_id`: [5](#0-4) 

Because `Accepted` gates on `gathered_signatures` and `Rejected` gates on `responded_signers`, a signer that first broadcasts a `Rejected` message for a block (incrementing `total_weight_rejected` and marking `responded_signers`) and later re-evaluates the same proposal to `LocallyAccepted` — a transition the signer's own state machine explicitly allows — will have its `Accepted` message counted too (since `gathered_signatures` does not yet contain its slot), incrementing `total_weight_approved`. Nowhere in this file, nor in `SignCoordinator::get_block_status` which consumes `BlockStatus`, is `total_weight_rejected` ever decremented or the earlier rejection retracted: [6](#0-5) 

The consequence mirrors the Nouns Builder bug precisely: a value that should represent "current live rejections" is instead a monotonically-increasing counter of "rejections ever seen," so `total_weight_rejected` can no longer be trusted to reflect the current, canonical set of signer verdicts, exactly as `totalSupply` no longer reflected "currently live tokens" once burns stopped being subtracted.

### Impact Explanation
The mining coordinator (`SignCoordinator::get_block_status`) treats a block as dead once `total_weight_rejected + weight_threshold > total_weight`: [7](#0-6) 

Because stale rejection weight from signers who have since flipped to acceptance is never removed, this threshold can be crossed (or approached) using phantom weight that no longer represents any live signer's opinion. This can cause the coordinator to spuriously abort/re-propose a block that in fact has (or would have) sufficient live acceptance weight, wedging block production and delaying otherwise-valid, canonical blocks from being signed and pushed — a liveness impact matching the "signer wedged into never signing valid blocks" category. It also breaks the aggregated-weight vs. verified-accepts equality that the >30%-rejected / ≥70%-accepted thresholds are supposed to enforce, since a signer's weight can simultaneously be double-counted (once as rejected, once as approved) instead of always reflecting a single, current verdict.

### Likelihood Explanation
No majority or key compromise is required: this is triggered purely by the intended, documented re-evaluation flow of a single honest signer reacting to timing/fork conditions (e.g., initially rejecting because a rival block appeared to win, then re-evaluating once the rival goes stale and locally accepting/signing, per `should_reevaluate_block`/`should_reevaluate_reject_reason` in `stacks-signer/src/v0/signer.rs`). Any block proposal that gets re-proposed or re-evaluated near a fork/timeout boundary can trigger this, making it plausible in normal network operation, not just under adversarial conditions.

### Recommendation
When a signer's verdict for a given `signer_signature_hash` is updated (Rejected → Accepted or vice versa) in `StackerDBListener`, remove/replace the signer's prior contribution to `total_weight_rejected`/`total_weight_approved` rather than only additively gating on independent sets. Track each signer's *current* verdict per block (e.g., a `HashMap<slot_id, Verdict>`) and recompute the two weight totals from that map, or explicitly subtract the previous weight before adding the new one, so the aggregated tallies always reflect only live, non-superseded verdicts.

### Proof of Concept
1. Signer A submits a `BlockResponse::Rejected` for block `H` (e.g., because a competing block at the same height currently looks canonical). `StackerDBListener` records this: `responded_signers.insert(A)`, `total_weight_rejected += weight_A` (`stacks-node/src/nakamoto_node/stackerdb_listener.rs:515-518`).
2. The competing block goes stale; Signer A's local state machine re-evaluates block `H` from `LocallyRejected` to `LocallyAccepted` (`stacks-signer/src/signerdb.rs:321-324`, permitted transition) and Signer A signs and broadcasts `BlockResponse::Accepted` for `H`.
3. `StackerDBListener` receives the `Accepted` message; since A's slot is not yet in `gathered_signatures`, it adds `total_weight_approved += weight_A` (`stackerdb_listener.rs:443-465`) — but `total_weight_rejected` still contains `weight_A` from step 1, with no code path to remove it.
4. `BlockStatus.total_weight_rejected` for block `H` is now permanently inflated by `weight_A` even though signer A currently supports the block, moving the block artificially closer to (or past) the `>30%` rejection cutoff checked in `signer_coordinator.rs:509-513`, independent of the block's real current support.

### Citations

**File:** stacks-signer/src/signerdb.rs (L313-329)
```rust
    /// Check if the block state transition is valid
    fn check_state(&self, state: BlockState) -> bool {
        let prev_state = &self.state;
        if *prev_state == state {
            return true;
        }
        match state {
            BlockState::Unprocessed => false,
            BlockState::LocallyAccepted | BlockState::LocallyRejected => !matches!(
                prev_state,
                BlockState::GloballyRejected | BlockState::GloballyAccepted
            ),
            BlockState::GloballyAccepted => !matches!(prev_state, BlockState::GloballyRejected),
            BlockState::GloballyRejected => !matches!(prev_state, BlockState::GloballyAccepted),
            BlockState::PreCommitted => matches!(prev_state, BlockState::Unprocessed),
        }
    }
```

**File:** docs/signer-flows.md (L140-150)
```markdown
    Unprocessed --> PreCommitted : mark_pre_committed
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
```

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

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L487-540)
```rust
            if rejections != block_status.total_weight_rejected {
                rejections = block_status.total_weight_rejected;
                let (rejections_step, new_rejections_timeout) = self
                    .block_rejection_timeout_steps
                    .range((Included(0), Included(rejections)))
                    .last()
                    .ok_or_else(|| {
                        NakamotoNodeError::SigningCoordinatorFailure(
                            "Invalid rejection timeout step function definition".into(),
                        )
                    })?;
                rejections_timeout = new_rejections_timeout;
                info!("Number of received rejections updated, resetting timeout";
                                    "rejections" => rejections,
                                    "rejections_timeout" => rejections_timeout.as_secs(),
                                    "rejections_step" => rejections_step,
                                    "rejections_threshold" => self.total_weight.saturating_sub(self.weight_threshold));

                counters.set_miner_current_rejections_timeout_secs(rejections_timeout.as_secs());
                counters.set_miner_current_rejections(rejections);
            }

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
```
