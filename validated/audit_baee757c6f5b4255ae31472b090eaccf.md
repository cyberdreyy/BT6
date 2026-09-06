## Finding [1](#0-0) [2](#0-1) 

### Title
StackerDBListener double-counts a single signer's weight into both `total_weight_approved` and `total_weight_rejected`, letting stale rejection weight veto an already-approved block - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`StackerDBListener` tallies `BlockResponse::Accepted`/`Rejected` messages per `BlockStatus` using two *different* dedup keys for the same logical "has this signer already responded" question: the `Accepted` arm dedups on `gathered_signatures` (keyed by `slot_id`), while the `Rejected` arm dedups on `responded_signers` (a separate `HashSet<u32>`). Because a single signer legitimately can send a `Rejected` response for a block and later, upon re-evaluation, send an `Accepted` response for the *same* block hash (`signer.rs`'s `should_reevaluate_reject_reason`/`should_reevaluate_block` flow explicitly allows re-considering a prior rejection), that signer's weight gets added to `total_weight_rejected` on the first message and then *also* added to `total_weight_approved` on the second, because the `Accepted` arm only checks `gathered_signatures`, which the `Rejected` arm never touches.

### Finding Description
In the `Rejected` branch:
```
if block.responded_signers.insert(slot_id) {
    block.total_weight_rejected = block.total_weight_rejected.saturating_add(signer_entry.weight);
    ...
}
``` [3](#0-2) 

In the `Accepted` branch:
```
if !block.gathered_signatures.contains_key(&slot_id) {
    block.total_weight_approved = block.total_weight_approved.saturating_add(signer_entry.weight);
    ...
}
block.gathered_signatures.insert(slot_id, signature);
block.responded_signers.insert(slot_id);
``` [1](#0-0) 

If a signer's slot first sends `Rejected` (weight added to `total_weight_rejected`, `responded_signers` now contains the slot, `gathered_signatures` does not), then later sends `Accepted` for the *same* `signer_signature_hash`, the `Accepted` arm's guard (`!gathered_signatures.contains_key(&slot_id)`) is still true, so the same signer's weight is *also* added to `total_weight_approved`. Nothing ever removes the earlier contribution from `total_weight_rejected`. The two tallies are supposed to be built from disjoint sets of "signers currently rejecting" vs "signers currently accepting," but a flip-vote signer occupies weight in both simultaneously.

This is directly reachable without any signer majority or key compromise: the signer's own re-evaluation logic in `stacks-signer/src/v0/signer.rs` allows a previously-rejected block to be reconsidered and accepted on a re-proposal when `should_reevaluate_reject_reason` returns true (e.g. `ConnectivityIssues`, `NoSignerConsensus`), which is a normal, benign occurrence (e.g. a transient RPC error to the node, or the global-state view becoming available) rather than a malicious act. [4](#0-3) 

### Impact Explanation
`SignerCoordinator::gather_signatures` (or equivalent poll loop) checks rejection *before* checking approval:
```
if block_status.total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight {
    ... return Err(NakamotoNodeError::SignersRejected { ... })
} else if block_status.total_weight_approved >= self.weight_threshold {
    ... return Ok(...)
}
``` [5](#0-4) 

Because the stale rejection weight from a signer who has since flipped to `Accepted` is never retracted from `total_weight_rejected`, it can push `total_weight_rejected` past the blocking-minority threshold (`total_weight - weight_threshold`) even while enough signers, including the flip-voter, have actually accepted the block. This causes the miner/coordinator to declare the block globally rejected (`NakamotoNodeError::SignersRejected`) and discard it, even though the *true* current vote tally (recomputed from each signer's final, most-recent response) would show sufficient approval. This is a liveness wedge on the node's block-acceptance state machine: a valid block that legitimately reached (or would reach) the 70% approval bar can be spuriously killed by phantom rejection weight left behind by a single signer's earlier, superseded rejection — reachable by one signer (plus gossip of its own two sequential, honest responses), no majority collusion required.

### Likelihood Explanation
This requires only one signer to emit a `Rejected` response followed later by an `Accepted` response for the same block hash. The signer-side code explicitly supports and documents this reconsideration path (`should_reevaluate_reject_reason`, `REASON -- yes --> FRESH` in the flow docs), so it is expected to occur under ordinary conditions such as transient connectivity errors or delayed global-state convergence, not just adversarial behavior. Any additional flip-voting signer added to the block increases the phantom rejection weight, making the wedge easier to trigger as reward-cycle size/threshold ratios vary.

### Recommendation
Use a single per-signer "current vote" record (e.g., `HashMap<u32, Vote>` mapping `slot_id -> Accepted|Rejected`) instead of two independently-dedup'd counters (`gathered_signatures` vs `responded_signers`). When a new message from a slot supersedes a prior vote of the opposite kind, retract the old weight from the corresponding tally before adding the new weight to the other tally, so `total_weight_approved` and `total_weight_rejected` are always computed from disjoint, up-to-date sets of currently-accepting vs currently-rejecting signers.

### Proof of Concept
1. Start a `StackerDBListener`/`SignerCoordinator` tracking a `BlockStatus` for block `H` with `weight_threshold` set such that one signer's weight alone can approach the 30% blocking bar (or combine with a few genuine rejections that are just under the bar).
2. Signer S (slot `k`, weight `w`) sends `SignerMessageV0::BlockResponse(BlockResponse::Rejected(...))` for `H` with `reason_code` such as `RejectCode::ValidationFailed(ValidateRejectCode::ConnectivityIssues)` (or similar re-evaluable reason). `total_weight_rejected += w`, `responded_signers = {k}`, `gathered_signatures = {}`.
3. Enough other signers reject to bring `total_weight_rejected` to just under the blocking threshold (`total_weight - weight_threshold - w`), so the block hasn't been declared rejected yet.
4. Node re-broadcasts/`S` re-evaluates (its own retry logic in `signer.rs` naturally resends a validated response once the connectivity issue clears) and sends `SignerMessageV0::BlockResponse(BlockResponse::Accepted(...))` for the *same* `H`. Since `gathered_signatures` doesn't contain slot `k`, `total_weight_approved += w` is accepted — but `total_weight_rejected` still includes `w` from step 2, unchanged.
5. `total_weight_rejected` is now `(threshold-adjacent value) + w`, crossing `total_weight - weight_threshold`, and the coordinator's poll loop declares `NakamotoNodeError::SignersRejected` for block `H` — even though `total_weight_approved` also legitimately grew and the signer set that currently supports the block might well be at/above `weight_threshold`. The block is discarded/re-proposed purely due to the un-retracted stale rejection weight.

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

**File:** docs/signer-flows.md (L180-203)
```markdown
    DONE1 -- no --> REASON{"prior reject reason<br/>re-evaluable?<br/>should_reevaluate_reject_reason"}
    REASON -- no --> PC{"state = PreCommitted?"}
    PC -- yes --> RESEND["re-send pre-commit, re-run<br/>handle_block_pre_commit → section 5"]
    PC -- no --> PREV["re-send previous response<br/>determine_response, or wait if<br/>validation still pending"]
    REASON -- yes --> FRESH
    KNOWN -- no --> DRAIN["collect early votes<br/>drain_pending_block_responses"] --> FRESH["fresh evaluation:<br/>new BlockInfo, fetch<br/>SortitionsView if needed"]
    FRESH --> CHECK["check_block_against_state:<br/>protocol version consensus (NoSignerConsensus),<br/>static validity, no problematic_txs<br/>(ProblematicTransactions), then<br/>v1 SortitionsView::check_proposal or<br/>v2 GlobalStateView::check_proposal → section 7"]
    CHECK -- invalid --> REJ["send rejection<br/>(not stored)"]:::bad
    CHECK -- "not provably invalid" --> BUSY{"validation slot free?<br/>submitted_block_proposal"}
    BUSY -- yes --> SUBMIT["submit_block_for_validation<br/>(ask the stacks-node)"]
    BUSY -- no --> QUEUE["queue it<br/>insert_pending_block_validation"]
    SUBMIT --> STORE["insert_block +<br/>process_pending_responses_for_block<br/>(replay early votes)"]
    QUEUE --> STORE
    classDef bad fill:#d84a3f22,stroke:#c9473d,stroke-width:1.5px;
```

Early votes: acceptances, rejections, and pre-commits that arrived before the
proposal itself are parked in pending tables and replayed once the proposal is
known.

> Anchors: `handle_block_proposal`, `should_reevaluate_block`,
> `should_reevaluate_reject_reason`, `check_block_against_state`,
> `submit_block_for_validation`, `process_pending_responses_for_block`
> (signer.rs); `check_proposal` (chainstate/v1.rs, v2.rs)
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
