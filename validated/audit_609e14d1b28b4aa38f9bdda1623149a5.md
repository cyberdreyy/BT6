### Title
Stale block-rejection weight is never cleared when a signer later switches to acceptance, corrupting the coordinator's aggregated-weight tally - (File: stacks-node/src/nakamoto_node/stackerdb_listener.rs)

### Summary
The mining coordinator's per-block vote tally (`total_weight_approved` / `total_weight_rejected`) is maintained by two independent code paths — one for `BlockResponse::Accepted`, one for `BlockResponse::Rejected` — that use two different de-duplication keys (`gathered_signatures` vs. `responded_signers`). A signer that legitimately rejects a block and later (still validly, per the signer's own re-evaluation state machine) accepts the same block causes its weight to be added to `total_weight_approved` **without ever removing it from `total_weight_rejected`**, permanently inflating the rejected-weight tally. This is analogous to the WETH `Router.createPoolETH` bug: a piece of logic (`safeTransferFrom`) implicitly assumed an invariant (that `src == msg.sender` self-transfers are always allowed) that does not hold in every context, breaking the operation silently on some paths. Here, the tally logic implicitly assumes "a signer only ever sends one kind of response per block," an invariant that does not hold given the signer's own documented ability to move from `LocallyRejected` back to `LocallyAccepted`.

### Finding Description
In `stackerdb_listener.rs`, the `Accepted` branch guards weight addition on `gathered_signatures`: [1](#0-0) 

while the `Rejected` branch guards weight addition on the *shared* `responded_signers` set: [2](#0-1) 

`responded_signers` is written by *both* branches (`block.responded_signers.insert(slot_id)` appears in the accept path too), so it acts as a global "have we ever heard from this signer" flag. That correctly stops an *Accept → Reject* flip from being double-counted (the reject branch's `insert` returns `false` once the slot is already marked, so `total_weight_rejected` is never incremented after an accept). But the mirror direction is broken: the accept branch never checks `responded_signers` — only `gathered_signatures` — so a *Reject → Accept* flip:
1. Adds the signer's weight to `total_weight_approved`.
2. Leaves the previously-added weight in `total_weight_rejected` untouched (nothing decrements it).

A signer moving from `LocallyRejected` to `LocallyAccepted` for the same block is an explicitly supported and tested transition in the signer's own state machine — `BlockInfo::check_state` allows `LocallyRejected -> LocallyAccepted`, and the signer test suite documents exactly this "reject-then-later-sign" scenario once a conflicting sibling becomes stale: [3](#0-2) [4](#0-3) 

So a single signer, without any majority or key compromise, can broadcast `Rejected` then later `Accepted` for the same `signer_signature_hash`, causing the node-side coordinator to double-book that signer's weight into both buckets simultaneously and forever.

### Impact Explanation
Because `total_weight_rejected` in the coordinator can only grow and never shrink even after a rejecting signer switches to acceptance, the "blocking minority" check: [5](#0-4) 

can be satisfied by stale, superseded rejections that no longer reflect any signer's live position. This lets `total_weight_rejected + weight_threshold > total_weight` fire spuriously, causing the miner to treat a block as `NakamotoNodeError::SignersRejected` and abandon/exclude it (including permanently banning transactions via `permanently_excluded_txids` if the stale rejection carried a `failed_txid`) even though a real, live supermajority (≥70%) has in fact accepted the block. This is a liveness wedge on block production driven purely by the ordering of vote messages from ordinary signer participants — no majority collusion or key compromise is required, only one (or a few) signers naturally flipping their vote as the protocol's own conflict-staleness rules intend them to.

### Likelihood Explanation
Reject→accept flips are not a rare edge case; they are an explicit, tested part of the signer design (a signer refuses to sign a fresh conflicting sibling but signs it once the conflicting signature goes stale, or re-evaluates after a prior rejection reason becomes stale — see `should_reevaluate_reject_reason`/`should_reevaluate_block` in the signer flow). Any tenure with a contested/sibling proposal, or any block whose validation was initially rejected for a since-resolved reason, can trigger this path. A lone gossiping signer (plus the normal StackerDB relay) is sufficient — no majority is needed to *trigger* the corruption, only to eventually reach the (already-corrupted) rejection threshold along with a handful of genuinely rejecting/undecided signers.

### Recommendation
Track per-signer vote state with a single authoritative "current vote" map (e.g., `HashMap<slot_id, VoteKind>`) instead of two independently-updated weight counters gated by different sets. When a signer's vote changes from Reject to Accept (or vice versa), atomically subtract the weight from the old bucket and add it to the new one, e.g.:
```rust
match block.votes.insert(slot_id, VoteKind::Accepted) {
    Some(VoteKind::Rejected) => {
        block.total_weight_rejected = block.total_weight_rejected.saturating_sub(signer_entry.weight);
        block.total_weight_approved = block.total_weight_approved.saturating_add(signer_entry.weight);
    }
    Some(VoteKind::Accepted) => { /* no-op, already counted */ }
    None => {
        block.total_weight_approved = block.total_weight_approved.saturating_add(signer_entry.weight);
    }
}
```
and symmetrically for the Rejected branch, so `total_weight_approved + total_weight_rejected` at any time always equals the sum of weights of signers whose *current* vote is known, matching the actual live tally the coordinator relies on for both the accept and reject thresholds.

### Proof of Concept
1. Configure `N` signers with weights such that no single signer exceeds the 30% blocking minority alone, but two signers together do (e.g., 5 signers of equal weight 20% each; blocking minority is >30%, i.e., ≥2 signers).
2. Have a miner propose a tenure-start block `B` while a stale/rival sibling from a prior tenure is still "fresh" in signer S1's DB (per `conflict_still_blocks`/freshness window) — S1 broadcasts `BlockResponse::Rejected(B)` while its rival is still fresh, and S2 independently rejects for an unrelated timing reason. Coordinator observes `total_weight_rejected = 40%`, not yet enough to reject (`40% + 70% ≤ 100%` false only if threshold set so 40% alone doesn't cross; adjust weights/threshold so it's below the blocking bound with 2/5 rejecting).
3. Once S1's conflicting signature goes stale (`tenure_last_block_proposal_timeout` elapses) it re-evaluates and legitimately signs/accepts `B`, broadcasting `BlockResponse::Accepted(B)`.
4. Observe in `stackerdb_listener.rs` that `total_weight_approved` increases by S1's weight, but `total_weight_rejected` still includes S1's original 20% contribution — it is never decremented.
5. Have S3 also reject for a separate, transient reason, pushing `total_weight_rejected` to 60% (20% stale-from-S1 + 20%-S2 + 20%-S3), even though S1 has already switched to accept. If `total_weight_rejected.saturating_add(weight_threshold) > total_weight` now evaluates true purely because of S1's stale rejection weight, the coordinator returns `NakamotoNodeError::SignersRejected` for block `B`, even though S1's real, current vote is Accept and the block may in fact reach genuine 70% approval shortly after — demonstrating the corrupted, non-decreasing tally causing a spurious/liveness-wedging rejection.

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

**File:** stacks-signer/src/v0/tests.rs (L809-826)
```rust
    #[test]
    fn stale_sibling_replaced_when_canonical_tip_below() {
        // A zero timeout makes A's signature stale immediately, and the node's canonical tip
        // is still the parent (height 9): A failed to be confirmed, so the signer must sign
        // the replacement rather than stall the tenure (the reorg-recovery case).
        let (info_a, info_b, _) = run_sibling_scenario(Duration::ZERO, false, None);
        assert_a_signed(&info_a);
        assert_eq!(
            info_b.state,
            BlockState::LocallyAccepted,
            "block B should be signed: the conflicting sibling timed out and is not canonical, got: {}",
            info_b.state
        );
        assert!(
            info_b.signed_self.is_some(),
            "block B should carry our signature after the conflict timed out unconfirmed"
        );
    }
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-513)
```rust
            if block_status
                .total_weight_rejected
                .saturating_add(self.weight_threshold)
                > self.total_weight
            {
```
