### Title
Stale rejection weight is never retracted after a signer supersedes it with a valid acceptance, letting a genuinely-approved block be miscounted as globally rejected - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
This is the analog of the reported bug class: a value that must be symmetrically removed/updated on one code path is only handled on the other. In the loan report, `principalFee` was deducted in the normal repay path but silently dropped in the rollover path, corrupting an accounting invariant. Here, the `StackerDBListener`'s per-block weight tally in `BlockStatus` deducts/guards double-counting on the "accept" path but not on the "reject → later accept" transition, corrupting the invariant that a signer's weight should count toward at most one current verdict for a given block.

### Finding Description
`BlockStatus` tracks two independent counters, `total_weight_approved` and `total_weight_rejected`, each guarded by different membership sets:

- Acceptance path gates on `gathered_signatures` (keyed by `slot_id`): [1](#0-0) 
- Rejection path gates on `responded_signers`: [2](#0-1) 

Crucially, the acceptance arm unconditionally inserts into `responded_signers` after processing regardless of prior state: [3](#0-2) 

Per the signer's own documented behavior, a signer that rejected a proposal earlier may legitimately revisit and sign it later once "the reason I rejected has since gone away": [4](#0-3) 

Trace the sequence for a single signer `S` and single block `B`:
1. `S` rejects `B` first. `responded_signers.insert(slot_id)` returns `true` (first time), so `total_weight_rejected += S.weight`.
2. Later, `S`'s local conditions clear and `S` validly signs and accepts `B`. In the acceptance arm the gate is `!gathered_signatures.contains_key(&slot_id)`, which is `true` (S never accepted before), so `total_weight_approved += S.weight` is also applied — with **no corresponding decrement of `total_weight_rejected`**.

The `S.weight` originally recorded under "rejected" is never retracted; `total_weight_rejected` and `total_weight_approved` now both include `S.weight` simultaneously for the same block. This breaks the invariant the coordinator relies on: that the aggregated weight of "current" rejections should reflect only signers currently rejecting, not signers who have since superseded their rejection with a signature.

The reverse direction is explicitly protected (`responded_signers.insert(slot_id)` returns `false` if the signer already accepted, so a later rejection cannot double count), which confirms the omission on the reject→accept path is an asymmetric gap rather than a deliberate design choice.

### Impact Explanation
The coordinator's polling loop checks the rejection condition before the acceptance condition: [5](#0-4) 
and only falls through to the acceptance check afterward: [6](#0-5) 

Because a superseded (stale) rejection's weight is never retracted, a set of signers just under the 30% blocking minority can permanently pin phantom rejection weight against a block even after flipping to a valid signature. Combined with other signers' genuine but smaller rejections, `total_weight_rejected` can cross the `> total_weight - weight_threshold` line purely from stale, already-superseded entries, causing the coordinator to declare `SignersRejected` on a block that in reality has (or would have) reached the 70% acceptance threshold. This is a liveness wedge on block production driven purely by weight-accounting corruption in the node-side coordinator/listener — the component explicitly listed as in scope — and matches the "aggregated-weight vs verified-accepts" equality-break impact class.

### Likelihood Explanation
This requires no majority of signers and no key compromise — only ordinary signer behavior already contemplated by the signer's own reject/accept-supersession logic (rejecting due to a transient condition, then later signing once it clears), which is a normal, expected sequence rather than a crafted attack. Any single signer whose local view briefly diverges (e.g., a short mismatch resolved before the 70% threshold is reached by others) triggers the double count. It is fully reachable by a single signer's ordinary vote sequence plus gossip via StackerDB, with no privileged access needed.

### Recommendation
Symmetrically retract the reject-side weight (and remove the signer from `failed_txids`/rejection bookkeeping) when a previously-rejecting signer's later acceptance is recorded, mirroring the protection already present in the reverse direction. Concretely, in the `Accepted` arm, before adding to `total_weight_approved`, check whether `slot_id` is already present in a per-verdict-tracking structure distinguishing "responded as reject" vs "responded as accept," and if the signer had previously rejected, subtract `signer_entry.weight` from `total_weight_rejected` (and undo any `failed_txids` contribution) at the same time weight is added to `total_weight_approved`, so that at any instant a signer's weight counts toward exactly one current verdict.

### Proof of Concept
Not independently executable without live signer/miner infrastructure, but the sequence is deterministic from the code paths cited:
1. Miner proposes block `B`; signer `S` (weight `w`) initially responds `BlockResponse::Rejected` for `B` due to a transient condition (e.g. stale view) — `stackerdb_listener.rs` records `total_weight_rejected += w` via the `responded_signers.insert` gate at lines 515–518.
2. Per the documented "repeat my earlier answer unless the reason has gone away" behavior, `S`'s local view resolves and it later legitimately signs and broadcasts `BlockResponse::Accepted` for the same `B`.
3. `stackerdb_listener.rs`'s `Accepted` handler (lines 443–465) checks only `gathered_signatures`, sees `S` has not accepted before, and adds `total_weight_approved += w`, while `total_weight_rejected` still contains `w` from step 1.
4. If enough other signers accumulate genuine rejections such that `total_weight_rejected.saturating_add(weight_threshold) > total_weight` (lines 509–518 of `signer_coordinator.rs`), the coordinator returns `NakamotoNodeError::SignersRejected` for `B` even though `S`'s weight should no longer count as a rejection, potentially discarding a block that would otherwise reach the 70% acceptance threshold.

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

**File:** docs/signer-flows.md (L26-28)
```markdown
    P(["a miner proposes a block"]) --> SEEN{"have I already<br/>answered on this block?"}
    SEEN -- yes --> PRIOR(["repeat my earlier answer<br/>(unless the reason I rejected<br/>has since gone away)"])
    SEEN -- no --> SANE{"does it fit my view of the chain?<br/>expected tenure and miner,<br/>builds on the tip I expect,<br/>nothing obviously malformed"}
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-518)
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
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L541-545)
```rust
            } else if block_status.total_weight_approved >= self.weight_threshold {
                info!("Received enough signatures, block accepted";
                    "signer_signature_hash" => %block_signer_sighash,
                );
                return Ok(block_status.gathered_signatures.values().cloned().collect());
```
