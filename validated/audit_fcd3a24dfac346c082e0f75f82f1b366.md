### Title
Signer's reject→accept vote flip is double-counted on the node's `StackerDBListener`, permanently inflating the rejection tally and wedging block production - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
`StackerDBListener::run` tallies each signer's `BlockResponse` toward two independent counters, `total_weight_approved` and `total_weight_rejected`, that the mining coordinator (`signer_coordinator.rs::get_block_status`) uses to decide whether to broadcast a signed block or abort with `SignersRejected`. The gate that prevents double-counting a signer's weight differs between the two branches: the `Rejected` branch gates on the shared `responded_signers` set, but the `Accepted` branch gates on the unrelated `gathered_signatures` map. Because the signer-side state machine explicitly allows a `LocallyRejected → LocallyAccepted` transition ("re-evaluated", `stacks-signer/src/signerdb.rs::BlockInfo::check_state`), a single signer can legitimately reject a block and later accept the very same block. When that happens, the node's tally credits that signer's weight to *both* `total_weight_rejected` and `total_weight_approved`, with no mechanism ever retracting the stale rejection weight. This is the same bug class as the Caller front-run: an action meant to revoke a prior grant of authority ("this signer rejects, i.e., withholds sign-off") can be silently superseded/duplicated instead of cleanly replaced, and the stale state keeps counting.

### Finding Description
In `stacks-node/src/nakamoto_node/stackerdb_listener.rs`:

- `Accepted` branch (~line 443):
```
if !block.gathered_signatures.contains_key(&slot_id) {
    block.total_weight_approved = block.total_weight_approved.saturating_add(signer_entry.weight);
    ...
}
block.gathered_signatures.insert(slot_id, signature);
block.responded_signers.insert(slot_id);
```
- `Rejected` branch (~line 515):
```
if block.responded_signers.insert(slot_id) {
    block.total_weight_rejected = block.total_weight_rejected.saturating_add(signer_entry.weight);
    ...
}
```

`responded_signers: HashSet<u32>` is shared across both message kinds. If a signer accepts first, a later rejection for the same `slot_id` is correctly suppressed (`responded_signers.insert` returns `false`). But if a signer rejects first, the later acceptance is gated purely on `gathered_signatures` — a map that the reject path never touches — so the acceptance always looks "new" and adds the signer's weight to `total_weight_approved` on top of the weight already sitting in `total_weight_rejected`. Nothing ever decrements `total_weight_rejected`.

The reject-then-accept sequence is not a corner case invented for this analysis; it is the designed behavior of the signer state machine. `stacks-signer/src/v0/signer.rs::should_reevaluate_reject_reason` explicitly lists rejection reasons that must be reconsidered on re-proposal (`ValidationFailed(UnknownParent)`, `ValidationFailed(NotFoundError)`, `NoSortitionView`, `ConnectivityIssues`, `NoSignerConsensus`, etc.), and `docs/signer-flows.md` §2 documents `LocallyRejected --> LocallyAccepted : re-evaluated` as a canonical transition. A signer that transiently rejects a proposal (e.g., a burn view not yet indexed, a connectivity blip, or a not-yet-processed parent) and then accepts it on re-evaluation will broadcast a `Rejected` message followed later by an `Accepted` message for the identical `signer_signature_hash` — exactly the trigger this bug needs, requiring only that single signer's own ordinary operation, not a majority of colluding signers.

### Impact Explanation
`total_weight_rejected` and `total_weight_approved` are the two quantities `SignerCoordinator::get_block_status` (`stacks-node/src/nakamoto_node/signer_coordinator.rs`) races against `weight_threshold`/`total_weight` to decide the fate of a proposal:
```
if block_status.total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight {
    ... return Err(NakamotoNodeError::SignersRejected { ... });
} else if block_status.total_weight_approved >= self.weight_threshold {
    ... return Ok(...);
}
```
Because a flipped signer's weight is stuck in `total_weight_rejected` forever (it is never cleared, and pruning only happens via the whole-block-status reset on a new proposal/timeout), the rejection side of the tally can be inflated by every signer that ever rejected-then-accepted that specific proposal. In borderline situations (weight distributions near the 70/30 split that the protocol is explicitly designed to tolerate, per `docs/signer-flows.md` §5-6), this stale weight can push `total_weight_rejected + weight_threshold > total_weight` even though the *current* set of signers who still hold a standing rejection is smaller than the blocking minority. The miner then aborts the proposal (`SignersRejected`), discarding transactions, applying tx exclusion penalties (`temporarily_excluded_txids`/`permanently_excluded_txids`), and forcing a re-propose — a liveness wedge triggerable by ordinary signer behavior (no majority collusion, no signer-key compromise), matching the High-severity category "a signer [set] wedged into never signing valid blocks" via a corrupted aggregated-weight vs. verified-accepts equality. It is a counting/liveness defect rather than a safety break (the actual `signer_signature` vector assembled from `gathered_signatures` is still deduplicated by `slot_id` and independently re-verified by `NakamotoBlockHeader::verify_signer_signatures`, so no invalid/forged signature can result), but it directly breaks the weight-tally equality the coordinator relies on to make liveness-critical decisions.

### Likelihood Explanation
Medium-to-High. The reject→accept transition is not adversarial — it is a documented, intentionally-supported code path (`should_reevaluate_reject_reason`, `docs/signer-flows.md` §2/§3) that fires whenever a signer's validation initially fails for a re-evaluable reason (missing burn view, connectivity hiccup, pending parent, etc.) and later succeeds on re-proposal — situations that are common under normal network jitter, not just attacker-crafted races. A malicious single signer can also trigger it deliberately at will, at no cost, by sending a `Rejected` message and then immediately following it with a genuine `Accepted` message for the same block, requiring only its own signing key and ordinary StackerDB gossip access (a "one-slot" actor, no majority needed).

### Recommendation
Use one shared gate for both branches, and make the weight update commutative/idempotent per slot regardless of order:
- Track, per `slot_id`, which side (accept/reject) the signer is currently counted toward, and when a message arrives for the opposite side, first subtract the previously-counted weight from the old side before adding it to the new side (or store the assignment in a single `HashMap<u32, Vote>` and recompute both totals from it rather than maintaining two independently-incremented running sums).
- At minimum, change the `Accepted` branch's gate from `gathered_signatures.contains_key(&slot_id)` to also check/clear `responded_signers`/`total_weight_rejected` for that `slot_id`, so a later acceptance retracts an earlier rejection's contribution symmetrically to how the `Rejected` branch already blocks double-processing of a prior acceptance.

### Proof of Concept
1. Configure a reward set where one signer's weight, call it `w`, is large enough that `total_weight_rejected(without w) + weight_threshold <= total_weight < total_weight_rejected(without w) + w + weight_threshold` (i.e., `w` is the deciding weight for the blocking-minority threshold).
2. Have that signer send `SignerMessageV0::BlockResponse(BlockResponse::Rejected(...))` for a given `signer_signature_hash` with a re-evaluable reason (e.g., `ValidateRejectCode::NotFoundError`) — this is the normal path taken when, e.g., the signer hasn't yet indexed the relevant burn block (`stacks-node/src/tests/signer/v0/missing_burn_block_proposal.rs` demonstrates this exact rejection/re-evaluation pattern).
3. `StackerDBListener` adds `w` to `total_weight_rejected`, crossing the blocking-minority threshold; `SignerCoordinator::get_block_status` observes this and would emit `SignersRejected`.
4. The miner re-proposes (or the signer's own re-evaluation on re-proposal succeeds); the same signer now sends `BlockResponse::Accepted(...)` for the identical `signer_signature_hash`.
5. `StackerDBListener` adds `w` to `total_weight_approved` as well (gate is `gathered_signatures`, unaffected by step 2/3), while `total_weight_rejected` still contains `w` from step 3 — it is never removed.
6. The coordinator's live `BlockStatus` now simultaneously carries an inflated `total_weight_rejected` that still trips the "blocking minority" branch on the very next poll tick (the rejection check runs before the acceptance check in `get_block_status`), even though the actual set of currently-standing rejections should no longer block the block, producing a spurious `SignersRejected` and wedging that tenure's block production.