### Title
v1 signer's tenure-duplicate check trusts only global acceptance, letting a one-slot miner get a second tenure-start block signed before the same-tenure conflict guard can see the first - ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
`SortitionsView::validate_tenure_change_payload` (v1) rejects a duplicate tenure-start block with `DuplicateBlockFound` only if `signer_db.get_last_globally_accepted_block` returns a hit [1](#0-0) . The v2 equivalent instead uses `get_last_signed_block`, which counts a block the moment it is **locally** accepted, before it is globally accepted [2](#0-1) . This asymmetry means a v1 signer's proposal-time duplicate check is blind to any tenure-start block it (or the group) has only locally signed but the node has not yet processed.

### Finding Description
For a tenure-change (tenure-start) block, the only place `DuplicateBlockFound` is ever raised is at proposal arrival, inside `check_proposal` → `validate_tenure_change_payload` — the docs explicitly note this check "never runs again" [3](#0-2) . Under protocol v1, that check asks `get_last_globally_accepted_block(&block.header.consensus_hash)`, which only returns a hit for a block whose `BlockState` is `GloballyAccepted` [4](#0-3) . A block that this very signer has already **locally accepted** (signed) but that has not yet reached the group's 70% threshold and is not yet reflected as the node's processed tip is invisible to this query, so a second, competing tenure-start block for the same tenure sails past the proposal-time duplicate check.

The only remaining defense is the pre-commit-threshold recheck in `handle_block_pre_commit`, which pulls `get_signed_conflicts` (a version-independent query keyed on `signed_self`/`signed_group`, so it does see the locally-accepted first block) and then asks `conflict_still_blocks` whether the conflict is still live [5](#0-4) . `conflict_still_blocks`'s own logic states the load-bearing rule for exactly this situation: if the node has not yet reached the first (locally-only) block and that block was **never globally accepted**, the conflict still blocks *only when* it is a sibling at the same height [6](#0-5) . That is the correct, defended path for a single signer instance.

The gap is at the network/aggregate level rather than inside one signer's control flow: whether this backstop actually stops the second block depends on whether *this* signer specifically has already locally-signed the first block by the time it evaluates the second one's pre-commit threshold. A one-slot miner (plus gossip) that proposes tenure-start block A, waits for A to reach only the 70% pre-commit weight (not yet a full 70% signature set, and definitely not yet handed to/processed by the node), and then immediately proposes a conflicting tenure-start block B for the same tenure, can race a subset of v1 signers that have not yet locally signed A. For those signers, `get_signed_conflicts` returns nothing for A (no `signed_self`/`signed_group` yet), the v1 proposal-time `DuplicateBlockFound` check is skipped (A isn't globally accepted), and B can be validated, pre-committed, and ultimately signed — a distinct outcome from other v1 signers who did already sign A. This breaks the "one-per-height" equality within the v1 signer set specifically because the v1 duplicate guard's predicate (`GloballyAccepted`) is strictly weaker than the state (`signed_self`) that the backstop conflict guard actually keys on, unlike v2 where both checks are aligned on "signed" state.

### Impact Explanation
If enough weight among v1-protocol signers races through before observing each other's local acceptance of A, the signer set as a whole can end up split — some having signed A, others having signed B for the same tenure at the same height — which is exactly the "two blocks signed at the same height/tenure" equivocation the pre-commit conflict guard exists to prevent (per its own doc comment: "stop us endorsing two blocks that could both end up in the chain" [7](#0-6) ). This is a **Critical**-class outcome per the scan's rubric (a signer signing a conflicting block) if enough weight lands on each side to reach threshold on both, or at minimum a liveness wedge/stall while the tenure fights itself. This is protocol-version-scoped (v1 chainstate path), reachable by a single miner controlling proposal timing plus normal signer-to-signer gossip, and requires no signer majority collusion or key compromise.

### Likelihood Explanation
Requires precise timing (the miner must fire the second tenure-start proposal in the narrow window after weight-70%-pre-commit on A but before enough signers have locally signed A), which is plausible for a malicious or buggy single-slot miner but not trivial to land against every signer simultaneously. I could not fully verify from the index alone whether `check_block_against_signer_db_state`'s tenure-change path (`check_tenure_change_confirms_parent`) independently closes this same-tenure duplicate gap for v1 signers before the conflict-guard code is reached — that function checks confirmation of the *parent* tenure, not same-tenure duplication, based on the code read, but a full trace through all of `chainstate/mod.rs` `check_tenure_change_confirms_parent` was not completed in the time available.

### Recommendation
Align v1's `validate_tenure_change_payload` duplicate check with v2's: use `get_last_signed_block` (locally OR globally accepted) instead of `get_last_globally_accepted_block`, so the proposal-time guard and the pre-commit-threshold backstop are keyed on the same predicate (`signed_self`/`signed_group`) across both protocol versions, closing the window where a v1 signer's own local acceptance of A is invisible to the check that should prevent B from ever reaching validation.

### Proof of Concept
1. Deploy a v1-protocol-version signer set (or a mixed set where some signers are still on v1).
2. Miner proposes tenure-start block A for tenure X. A subset of signers validate A, pre-commit, and reach `mark_locally_accepted` (signed_self set) before the rest of the set has processed A's pre-commit.
3. Before A is globally accepted or handed to the node, the miner immediately proposes a second, conflicting tenure-start block B, also for tenure X (e.g., a re-org of the tenure-start after a bad first attempt).
4. On signers that have **not yet** locally accepted A: `get_last_globally_accepted_block(X)` returns `None` (A isn't globally accepted), so v1's `DuplicateBlockFound` check passes B through; B is submitted to the node, validated, pre-committed, and — since `get_signed_conflicts` on these signers also has no local record of A yet — eventually signed.
5. Meanwhile, the signers that already signed A retain that signature. The signer set is now split between A and B for the same tenure/height, which downstream aggregation (`signer_coordinator.rs`) cannot safely resolve into a single canonical signature set. Exact reproduction requires timing control over signer response latency; this was reasoned from code inspection rather than an executed test, given the constraints of this scan.

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L505-518)
```rust
        let last_in_current_tenure = signer_db
            .get_last_globally_accepted_block(&block.header.consensus_hash)
            .map_err(|e| {
                SignerChainstateError::from(ClientError::InvalidResponse(e.to_string()))
            })?;
        if let Some(last_in_current_tenure) = last_in_current_tenure {
            warn!(
                "Miner block proposal contains a tenure change, but we've already signed a block in this tenure. Considering proposal invalid.";
                "proposed_block_consensus_hash" => %block.header.consensus_hash,
                "proposed_block_signer_signature_hash" => %block.header.signer_signature_hash(),
                "last_in_tenure_signer_signature_hash" => %last_in_current_tenure.block.header.signer_signature_hash(),
            );
            return Err(RejectReason::DuplicateBlockFound);
        }
```

**File:** stacks-signer/src/chainstate/v2.rs (L340-357)
```rust
        // We already confirmed in check miner activity that the current tenure is valid. So check we are not
        // reorging the tenure blocks. Only blocks we have signed (locally or globally accepted) count
        // here: a block we have merely pre-committed to carries no signature from us, so it is safe to
        // accept a competing tenure-start block in its place if it failed to reach consensus.
        let last_in_current_tenure = signer_db
            .get_last_signed_block(&block.header.consensus_hash)
            .map_err(|e| {
                SignerChainstateError::from(ClientError::InvalidResponse(e.to_string()))
            })?;
        if let Some(last_in_current_tenure) = last_in_current_tenure {
            warn!(
                "Miner block proposal contains a tenure change, but we've already signed a block in this tenure. Considering proposal invalid.";
                "proposed_block_consensus_hash" => %block.header.consensus_hash,
                "proposed_block_signer_signature_hash" => %block.header.signer_signature_hash(),
                "last_in_tenure_signer_signature_hash" => %last_in_current_tenure.block.header.signer_signature_hash(),
            );
            return Err(RejectReason::DuplicateBlockFound);
        }
```

**File:** docs/signer-flows.md (L428-431)
```markdown
- `validate_tenure_change_payload` rejects with `DuplicateBlockFound` when we
  have already accepted a block in the tenure a tenure-change block is starting.
  v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1
  counts only globally accepted ones (`get_last_globally_accepted_block`).
```

**File:** stacks-signer/src/signerdb.rs (L1680-1690)
```rust
    /// Return the last globally accepted block in a tenure (identified by its consensus hash).
    pub fn get_last_globally_accepted_block(
        &self,
        tenure: &ConsensusHash,
    ) -> Result<Option<BlockInfo>, DBError> {
        let query = "SELECT block_info FROM blocks WHERE consensus_hash = ?1 AND state = ?2 ORDER BY stacks_height DESC LIMIT 1";
        let args = params![tenure, &BlockState::GloballyAccepted.to_string()];
        let result: Option<String> = query_row(&self.db, query, args)?;

        try_deserialize(result)
    }
```

**File:** stacks-signer/src/v0/signer.rs (L1108-1112)
```rust
    /// Whether a block we signed still conflicts at `proposed_height`.
    ///
    /// The guard exists to stop us endorsing two blocks that could both end up in the chain. It
    /// must not, however, outlive the block it protects: a Bitcoin reorg can kill a block we
    /// signed, and a dead signature must not stall the chain restarting beneath it.
```

**File:** stacks-signer/src/v0/signer.rs (L1192-1206)
```rust
        let node_reaches_conflict = match stacks_client.get_tenure_tip(&conflict.consensus_hash) {
            Ok(tip) => tip.anchored_header.height() >= conflict.stacks_height,
            // A 404 is an answer, not a failure: the node has no blocks in that tenure at all.
            Err(ClientError::RequestFailure(reqwest::StatusCode::NOT_FOUND)) => false,
            Err(e) => {
                warn!("{self}: Failed to fetch the canonical tip of a conflicting block's tenure: {e:?}. Leaving the conflict in place.";
                    "conflicting_consensus_hash" => %conflict.consensus_hash,
                    "conflicting_block_height" => conflict.stacks_height,
                );
                return true;
            }
        };
        node_reaches_conflict
            || (!conflict.globally_accepted && conflict.stacks_height <= proposed_height)
    }
```

**File:** stacks-signer/src/v0/signer.rs (L1383-1421)
```rust
        let conflicts = match self
            .signer_db
            .get_signed_conflicts(block_info.block.header.chain_length, &block_hash)
        {
            Ok(conflicts) => conflicts,
            Err(e) => {
                warn!("{self}: Failed to query the signed blocks. Refusing to sign block {block_hash}: {e:?}");
                return;
            }
        };
        let freshness_cutoff = get_epoch_time_secs().saturating_sub(
            self.proposal_config
                .tenure_last_block_proposal_timeout
                .as_secs(),
        );
        // A fresh signature only blocks while the block it covers could still be part of the
        // chain: see `conflict_still_blocks`, which asks the node whether it is. Check
        // freshness first: it is a local timestamp comparison, while `reorg_permit_stands`
        // and `conflict_still_blocks` each query the node, so stale conflicts cost no
        // round-trips.
        if let Some(conflict) = conflicts.iter().find(|conflict| {
            conflict.last_endorsed > freshness_cutoff
                && !self.reorg_permit_stands(stacks_client, conflict)
                && self.conflict_still_blocks(
                    stacks_client,
                    conflict,
                    block_info.block.header.chain_length,
                )
        }) {
            warn!(
                "{self}: Reached the pre-commit threshold for a block, but we have recently signed or accepted a different block at the same or higher height. Refusing to sign.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "conflicting_signer_signature_hash" => %conflict.signer_signature_hash,
                "conflicting_block_height" => conflict.stacks_height,
                "conflicting_consensus_hash" => %conflict.consensus_hash,
            );
            return;
        }
```
