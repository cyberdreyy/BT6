### Title
Stale rejection weight not cleared when a signer flips to acceptance corrupts the miner's aggregated-weight tally - (File: stacks-node/src/nakamoto_node/stackerdb_listener.rs)

### Summary
`StackerDBListener` (the node-side signer-response coordinator, analogous to `LiquidationManager`'s running `entireSystemColl`) maintains two independent running counters per block — `total_weight_approved` and `total_weight_rejected` — inside `BlockStatus`. As with the Liquity/Bima bug where `entireSystemColl` was decremented by an amount smaller than what was actually removed from the system (gas comp not subtracted), here the counters are only ever incremented and are never reconciled against each other when a signer's vote for the same block changes from reject to accept. This produces an aggregated-weight value that no longer reflects the set of currently-valid per-signer verdicts.

### Finding Description
`BlockStatus::total_weight_rejected` is incremented in the `BlockResponse::Rejected` arm gated only by `block.responded_signers.insert(slot_id)` succeeding [1](#0-0) . `total_weight_approved` is incremented in the `BlockResponse::Accepted` arm gated only by `!block.gathered_signatures.contains_key(&slot_id)` [2](#0-1) .

Because signers legitimately re-evaluate and can flip a previous rejection into an acceptance for the very same block (the signer-side re-evaluation path explicitly supports "reject reason re-evaluable" transitions, see `should_reevaluate_reject_reason` in the state-machine flow docs) [3](#0-2) , the sequence "signer rejects → signer later accepts the same block" is reachable by a single signer with no majority required. When it happens:
1. On the first (reject) message, `responded_signers.insert(slot_id)` succeeds, so `total_weight_rejected` is incremented by that signer's weight, and `slot_id` is now permanently marked in `responded_signers`.
2. On the later (accept) message for the same block, the gating check is `!gathered_signatures.contains_key(&slot_id)`, which is still true (accept path never previously ran), so `total_weight_approved` is *also* incremented by the same signer's weight.

There is no code path that subtracts the earlier reject weight from `total_weight_rejected` once the signer's current, most-recent verdict is an accept. The running tally therefore diverges from ground truth exactly the way `entireSystemColl` diverged from the real system collateral in the reported bug: an amount that should have been removed from one accumulator (the stale rejection) is never removed, inflating `total_weight_rejected` indefinitely for as long as that block is being tracked. This breaks the "aggregated-weight vs verified-accepts" equality the coordinator depends on to decide whether to keep waiting, declare rejection, or declare acceptance.

### Impact Explanation
The coordinator uses `total_weight_rejected` directly to decide whether to abort the block with `NakamotoNodeError::SignersRejected` [4](#0-3) . Because the stale rejection weight from a signer who has since accepted is never removed, the rejection accumulator can cross the blocking-minority threshold (`total_weight_rejected + weight_threshold > total_weight`) purely from outdated data, even while every currently-polled signer, including the one who flipped, is actually willing to sign. This wedges block production: the miner aborts a proposal that in truth has (or would have) sufficient real-time approval weight, and does so deterministically and repeatably for any block that a single re-evaluating signer touches. This is a liveness wedge triggerable by one signer (plus normal gossip of its own two messages) with no majority required, matching the class of "aggregated-weight vs verified-accepts" defects called out in scope.

### Likelihood Explanation
No majority coordination is needed — a single signer legitimately reversing its own vote for a block (a supported, documented behavior per the re-evaluation flow) is sufficient to corrupt the tally. This can happen during ordinary operation whenever a rejection reason is later resolved (e.g., timing/ordering races where a signer initially rejects due to state not yet caught up, then re-evaluates and accepts once caught up), making the miner-side weight bookkeeping unreliable on essentially every tenure where such a flip occurs.

### Recommendation
Track a single current-verdict-per-signer map (e.g., `HashMap<u32, Verdict>`) instead of two independently-incremented counters gated by different membership checks. When a new verdict for a `slot_id` supersedes a stored one, subtract the old verdict's weight from its counter before adding the new verdict's weight to the other counter — mirroring the LiquityV1 pattern of always removing the *exact* amount that left the accumulator (here, the previous vote's weight) rather than assuming votes are append-only and mutually exclusive.

### Proof of Concept
1. Configure a reward-cycle signer set with signer S holding weight `w`, and `total_weight`, `weight_threshold` such that `w` alone is close to (but under) the blocking-minority threshold `total_weight - weight_threshold`.
2. Miner proposes block B.
3. Signer S broadcasts `BlockResponse::Rejected` for B (e.g., due to a transient state mismatch) → `stackerdb_listener` increments `total_weight_rejected += w`, marks `responded_signers.insert(slot_id)`.
4. Signer S re-evaluates (its locally rejected block, still `should_reevaluate_reject_reason`-eligible) and later broadcasts `BlockResponse::Accepted` with a valid signature for the same `signer_signature_hash`.
5. `stackerdb_listener` handles the accept: `gathered_signatures` does not yet contain `slot_id`, so it increments `total_weight_approved += w` — but `total_weight_rejected` still contains the stale `w` from step 3, unmodified.
6. Have other signers, whose combined weight is just under the blocking-minority threshold on their own, also reject B for a legitimate reason. Combined with the stale `w` still sitting in `total_weight_rejected` from S (who has since accepted), `total_weight_rejected + weight_threshold > total_weight` becomes true and `signer_coordinator.rs` returns `Err(SignersRejected{..})`, aborting a block that in reality has sufficient live acceptance weight once S's flip is correctly accounted for.

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L443-446)
```rust
                        if !block.gathered_signatures.contains_key(&slot_id) {
                            block.total_weight_approved = block
                                .total_weight_approved
                                .saturating_add(signer_entry.weight);
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-518)
```rust
                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);
```

**File:** docs/signer-flows.md (L177-184)
```markdown
    KNOWN -- yes --> REEV["should_reevaluate_block"]
    REEV --> DONE1{"globally accepted and<br/>already responded?"}
    DONE1 -- yes --> IGN2(["ignore"])
    DONE1 -- no --> REASON{"prior reject reason<br/>re-evaluable?<br/>should_reevaluate_reject_reason"}
    REASON -- no --> PC{"state = PreCommitted?"}
    PC -- yes --> RESEND["re-send pre-commit, re-run<br/>handle_block_pre_commit → section 5"]
    PC -- no --> PREV["re-send previous response<br/>determine_response, or wait if<br/>validation still pending"]
    REASON -- yes --> FRESH
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-520)
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

```
