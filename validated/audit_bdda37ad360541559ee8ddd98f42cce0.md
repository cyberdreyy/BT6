I found a concrete analog worth flagging: `store_and_process_block_signature` treats an unknown peer's *acceptance* message as a stand-in for a *pre-commit* whenever no prior pre-commit record exists for that signer, and this substitution happens even after weight tallies have already been computed against a different vote type.

### Title
Peer block-acceptance silently reinterpreted as a pre-commit lets weight be double-borrowed across the pre-commit and signature thresholds - (File: stacks-signer/src/v0/signer.rs)

### Summary
`store_and_process_block_signature` (stacks-signer/src/v0/signer.rs:2442-2538) stores a peer's `BlockAccepted` signature via `add_block_signature`, then checks `has_committed` to see if that signer ever sent a `BlockPreCommit` for the block. If not, it calls `handle_block_pre_commit` for that signer and returns, explicitly to "treat it as their pre-commit" for compatibility with older signer versions [1](#0-0) .

### Finding Description
A signer's real signature (the irreversible act) is stored unconditionally in `add_block_signature` before the pre-commit-vs-signature branch is even evaluated [2](#0-1) . `handle_block_pre_commit`, however, tallies pre-commit weight from `get_block_pre_committers` (the `block_pre_commits` table) and, once ≥70% pre-commit weight is reached, re-runs chainstate checks and *itself signs* if not already signed [3](#0-2) . Because the sighash is already recorded as accepted by the remote peer at this point, this path is reachable for both: (a) legitimate older-protocol peers whose acceptance never carried a pre-commit, and (b) a malicious/mis-implemented peer that always skips the pre-commit broadcast and jumps straight to `BlockAccepted` messages. In both cases the local signer folds that peer's weight into the pre-commit tally via `add_block_pre_commit`/`get_block_pre_committers`, potentially pushing local pre-commit weight over threshold using votes that were never actually pre-commits, and then separately the same signer's real signature is already stored and will also count toward the signature threshold in `store_and_process_block_signature`'s tally of `get_block_signatures` (lines 2474-2496). This means a subset of signers who never went through the section-5 pre-commit review can simultaneously supply weight to both gates guarding block signing, undermining the design intent described in docs/signer-flows.md that a "signer only spends its signature once it knows a supermajority intends to spend theirs" [4](#0-3) . This is the closest analog in-scope to the Jenkins bug class of "one identifier reinterpreted/aliased into another controlled namespace, bypassing the check meant to gate it" — here a `BlockAccepted` vote is aliased into the `block_pre_commits` table that the 70% pre-commit gate reads from, without the sender having actually gone through pre-commit review.

### Impact Explanation
This can push a colluding minority's weight to count twice (once in the pre-commit tally, once in the final signature tally) against thresholds meant to be independent gates, which could help a set of signers below the true 70% signature threshold locally cross the pre-commit threshold and trigger this signer's own signature earlier/incorrectly than intended. This maps to the "aggregated-weight vs verified-accepts" equality break called out in scope, but I could not fully prove it produces a signature over an actually-invalid/non-canonical block, because `handle_block_pre_commit` still re-runs `check_block_against_signer_db_state` before signing (line ~1345 onward, not fully captured) — so the practical damage is likely confined to weight-accounting duplication and premature/incorrect threshold crossing rather than a guaranteed invalid-block signature.

### Likelihood Explanation
Reachable by a single non-majority peer sending only `BlockAccepted` messages while withholding `BlockPreCommit`, requiring no more than what the threat model allows (a one-slot miner plus gossip/peer messages, no majority or key compromise). The behavior is explicitly intentional per the code comment (backward compatibility), which lowers confidence this is an unintended vulnerability versus a deliberate compatibility shim whose weight-accounting interaction was not fully modeled.

### Recommendation
Verify whether `handle_block_pre_commit`'s post-threshold logic can be triggered redundantly by a signature-derived synthetic pre-commit for signers already contributing to `get_block_signatures`, and if so, exclude any address already present in `block_signatures` from being double-counted when a synthetic pre-commit is derived from a `BlockAccepted` message, or explicitly de-duplicate weight across `block_pre_commits` and `block_signatures` at both gates.

### Proof of Concept
Could not construct a runnable end-to-end PoC within the available tool calls/iterations. This would need a background Devin session with test-suite access (`stacks-signer/src/v0/tests.rs`) to simulate: signer set of weight-diverse peers; one subset only ever emits `BlockAccepted` (never `BlockPreCommit`) for a fresh block; observe whether `commit_weight` in `handle_block_pre_commit` (line 1295) crosses `min_weight` using that subset's weight, while the *same* subset's weight is also later counted in `store_and_process_block_signature`'s `total_signature_weight` (line 2495), and confirm whether this materially changes signing outcome versus a baseline run where that subset is entirely absent.

**Caveat:** Given the difficulty of the scoped bug class and the size limits on what the index surfaces, I was not able to fully trace `handle_block_pre_commit`'s complete conflict-recheck logic (lines ~1345 onward were truncated in retrieval) to confirm whether it fully neutralizes this double-counting before any signature is produced. I recommend a Devin session read the full `handle_block_pre_commit` and `store_and_process_block_signature` bodies plus `signer.rs`'s `has_committed`/`add_block_pre_commit` implementations to confirm or refute exploitability with certainty before treating this as confirmed rather than a plausible analog.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1282-1338)
```rust
        let block_hash = block_info.block.header.signer_signature_hash();
        // do we have enough pre-commits to reach consensus?
        // i.e. is the threshold reached?
        //
        // Tally this up front, before the early returns below, so that every pre-commit we
        // receive can be logged with the running weight. Crossing this threshold is what
        // triggers our block response, so without it the wait for the threshold, which can
        // be minutes and is the bulk of a stalled block's latency, leaves no trace at all.
        let committers = self
            .signer_db
            .get_block_pre_committers(&block_hash)
            .unwrap_or_else(|_| panic!("{self}: Failed to load block commits"));

        let commit_weight = self.compute_signature_signing_weight(committers.iter());
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });

        info!("{self}: Received block pre-commit";
            "signer_address" => %stacker_address,
            "signer_signature_hash" => %block_hash,
            "consensus_hash" => %block_info.block.header.consensus_hash,
            "block_height" => block_info.block.header.chain_length,
            "signer_weight" => self.signer_weights.get(stacker_address).copied().unwrap_or(0),
            "pre_commit_weight" => commit_weight,
            "pre_commit_weight_required" => min_weight,
            "total_weight" => total_weight,
            "pre_commit_threshold_reached" => commit_weight >= min_weight,
            "already_signed" => block_info.signed_self.is_some(),
        );

        if block_info.signed_self.is_some() {
            debug!(
                "{self}: Received pre-commit for a block that we have already signed. Doing nothing...",
            );
            return;
        }

        if !block_info.valid.unwrap_or(false) {
            // We received a pre-commit for a block that we have not validated or we have already marked this block as invalid.
            // We should not do anything further as we do not know what our response should be and we do not change our votes on rejected
            // blocks unless we receive a new block proposal for it and the reject reason allows us to reconsider.
            debug!(
                "{self}: Received a pre-commit for a block that we have not determined to be valid: {:?}. Doing nothing...", block_info.valid
            );
            return;
        }

        if min_weight > commit_weight {
            debug!(
                "{self}: Not enough pre-committed to block {block_hash} (have {commit_weight}, need at least {min_weight}/{total_weight})"
            );
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2450-2460)
```rust
    ) {
        let block_hash = &block_info.signer_signature_hash();
        // signature is valid! store it.
        // if this returns false, it means the signature already exists in the DB, so just return.
        if !self
            .signer_db
            .add_block_signature(block_hash, signer_address, signature)
            .unwrap_or_else(|_| panic!("{self}: Failed to save block signature"))
        {
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2461-2466)
```rust

        // If this isn't our own signature and we haven't seen a pre-commit from this signer yet, try treating it as a pre-commit in case the caller is running an outdated version
        if signer_address != &self.stacks_address && !self.signer_db.has_committed(block_hash, signer_address).inspect_err(|e| warn!("Failed to check if pre-commit message already considered for {signer_address:?} for {block_hash}: {e}")).unwrap_or(false) {
            self.handle_block_pre_commit(stacks_client, sortition_state, signer_address, block_hash);
            return;
        }
```

**File:** docs/signer-flows.md (L57-59)
```markdown
- **Nobody signs alone.** The pre-commit round means a signer only spends its
  signature once it knows a supermajority intends to spend theirs, so a block
  that will never reach 70% rarely collects stray signatures at all.
```
