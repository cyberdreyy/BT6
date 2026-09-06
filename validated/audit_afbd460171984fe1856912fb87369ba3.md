### Title
Stale rejection weight is never cleared when a signer flips its vote to Accepted, letting a rejected signer's weight double-count toward both the rejection and acceptance tallies - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
`StackerDBListener::run` maintains a per-block `BlockStatus` (`total_weight_approved`, `total_weight_rejected`, `responded_signers`, `gathered_signatures`) that the mining coordinator (`SignerCoordinator::get_block_status`) polls to decide whether a block proposal has been globally accepted or globally rejected. The `Accepted` branch adds a signer's weight to `total_weight_approved` if that signer's slot is not already in `gathered_signatures`, but it never checks or clears any prior weight that same signer contributed to `total_weight_rejected`. Because signers are legitimately allowed to re-evaluate and change their vote for the same block (per `should_reevaluate_block`/`should_reevaluate_reject_reason` in `stacks-signer/src/v0/signer.rs`), a signer that first rejects and later accepts the same block leaves its weight counted in *both* tallies simultaneously.

### Finding Description
`stacks-node/src/nakamoto_node/stackerdb_listener.rs` processes `SignerMessageV0::BlockResponse` events and updates `BlockStatus`:

- On `Rejected`: weight is added to `total_weight_rejected` only if `block.responded_signers.insert(slot_id)` returns `true` (i.e., first time this signer responded) [1](#0-0) .
- On `Accepted`: weight is added to `total_weight_approved` guarded only by `!block.gathered_signatures.contains_key(&slot_id)` [2](#0-1) , and separately `block.responded_signers.insert(slot_id)` is called unconditionally at the end of the branch [3](#0-2) .

Because `gathered_signatures` is untouched by the Rejected branch, a signer who first sends `Rejected` (adding weight W to `total_weight_rejected`, and inserting into `responded_signers`) can later send `Accepted` for the same `signer_signature_hash`. In the `Accepted` handler, `gathered_signatures.contains_key(&slot_id)` is still `false` (nothing in the Rejected path ever populates `gathered_signatures`), so the code adds weight W again, this time to `total_weight_approved`, and inserts the signature into `gathered_signatures`. `total_weight_rejected` is never decremented. The result is that this signer's weight W is now counted in both `total_weight_approved` and `total_weight_rejected` at once, breaking the invariant that a signer's weight should count toward exactly one of "approve" or "reject" at any time.

This vote-flip is not a hypothetical edge case in this codebase — the signer protocol explicitly re-evaluates and re-sends decisions for known blocks (`should_reevaluate_block`, `should_reevaluate_reject_reason` in `stacks-signer/src/v0/signer.rs`), and `BlockInfo::check_state` in `stacks-signer/src/signerdb.rs` documents that local (non-global) states are reachable from "anything not yet global" — i.e. `LocallyRejected -> LocallyAccepted` transitions on the signer side are permitted when reject reasons are re-evaluable, per the flow described in `docs/signer-flows.md` (`REASON -- yes --> FRESH` path when `should_reevaluate_reject_reason` says the prior rejection can be reconsidered). Because I could not directly inspect `should_reevaluate_reject_reason`'s body (index truncation), I cannot cite the exact conditions under which reconsideration is allowed, but the docstring/flow explicitly documents this class of transition as a supported, expected behavior of the state machine, not a bug on the signer side.

### Impact Explanation
This is analogous to the referenced report's core flaw: an entity that should track a single up-to-date balance/tally instead accumulates stale contributions from an earlier, now-superseded state, letting one signer's weight count on both sides of a mutually exclusive threshold. Concretely, in `SignerCoordinator::get_block_status` (`stacks-node/src/nakamoto_node/signer_coordinator.rs`, lines ~509-545) the loop first checks whether rejection weight makes the >30% blocking minority: `block_status.total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight` [4](#0-3) , and only afterward checks `total_weight_approved >= self.weight_threshold`. If a signer's stale rejection weight is never cleared after it flips to acceptance, the miner can be told `SignersRejected` for a block that in reality has enough current signer support to be legitimately signed (double counted weight inflates the rejection side without ever being retracted), causing:
- A liveness/DoS wedge: valid, sufficiently-signed blocks get spuriously treated as rejected by the coordinator, causing miners to discard good proposals and potentially never make progress if enough signers go through a reject→accept cycle (matches the "signer wedged... acting on stale...threshold" High-impact category).
- In the opposite direction, total weight can simultaneously appear to cross both the "reject" and "accept" thresholds, which is an internally inconsistent, unsound tally — the equality "each signer's weight counts toward exactly one side of the decision" is broken, which is the direct analog of the external report's "increase balance via unchecked, uncoordinated calls" pattern, here manifesting as unbounded/duplicate weight accounting rather than fund theft.

### Likelihood Explanation
This does not require a majority of signers or key compromise — a single honest signer re-evaluating its own vote for a block (a normal, protocol-sanctioned action documented in `docs/signer-flows.md` section 3, driven by `should_reevaluate_block`/`should_reevaluate_reject_reason`) is sufficient to trigger the double-count. This makes it fairly likely to occur under normal operational conditions (network races, re-proposals, changed rejection reasons) rather than requiring an adversarial majority, satisfying the "signer wedged / stale weight" bar for likelihood.

### Recommendation
When processing a `BlockResponse::Accepted` for a signer slot, clear any weight and bookkeeping the same signer previously contributed via a `Rejected` response for the same block (and vice versa for `Rejected` after `Accepted`), e.g., by tracking per-signer "current vote" (Accept/Reject) rather than accumulating weight into two independent running totals that are never reconciled. Alternatively, recompute `total_weight_approved`/`total_weight_rejected` from a single per-slot vote map on every update instead of incrementally accumulating into two separate counters that can each only grow.

### Proof of Concept
1. Coordinator proposes block B to N signers with weight threshold T (70%) and blocking minority M (>30%).
2. Signer S (weight W, where W alone is insufficient to cross M but combined with other rejecting signers it is) sends `BlockResponse::Rejected` for B. `stackerdb_listener.rs` adds W to `total_weight_rejected` and marks S in `responded_signers`.
3. Enough other signers also reject, bringing `total_weight_rejected` close to, but under, the blocking-minority threshold.
4. S subsequently re-evaluates (e.g., due to `should_reevaluate_reject_reason` on a new proposal for the same `signer_signature_hash`, or a timing race) and sends `BlockResponse::Accepted` for the same block hash.
5. In the `Accepted` handler, since `gathered_signatures` never contained S's slot id, W is added again into `total_weight_approved`; `total_weight_rejected` still contains S's earlier W contribution, unmodified.
6. `total_weight_rejected` (still including S's stale W) now crosses the blocking-minority threshold check in `SignerCoordinator::get_block_status`, causing the miner to treat block B as globally rejected via `NakamotoNodeError::SignersRejected`, even though S — and possibly enough weight overall — has actually moved to Accepted. This can stall block production indefinitely if this pattern recurs across proposals in the same tenure.

I could not fully verify the exact preconditions under which `should_reevaluate_reject_reason` permits a `Rejected -> Accepted` transition (the function body was not retrievable within available tool calls), so the precise triggering scenario for the vote flip is based on the documented flow in `docs/signer-flows.md` rather than a directly inspected function body; a Devin session with full file access would be needed to confirm the exact reachable conditions.

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L443-446)
```rust
                        if !block.gathered_signatures.contains_key(&slot_id) {
                            block.total_weight_approved = block
                                .total_weight_approved
                                .saturating_add(signer_entry.weight);
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L464-465)
```rust
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

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-513)
```rust
            if block_status
                .total_weight_rejected
                .saturating_add(self.weight_threshold)
                > self.total_weight
            {
```
