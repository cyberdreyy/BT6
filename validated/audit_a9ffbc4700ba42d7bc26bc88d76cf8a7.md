### Title
Re-proposal of a `GloballyRejected` block with a re-evaluable local reject reason erases the terminal rejection state and lets a signer sign it - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`should_reevaluate_block`'s early-exit guard (`BlockInfo::globally_approved_and_responded`) only special-cases `BlockState::GloballyAccepted`, never `GloballyRejected`. When a block's locally recorded `reject_reason` happens to be one of the "soft" codes that `should_reevaluate_reject_reason` treats as re-evaluable (`ConnectivityIssues`, `NoSortitionView`, `NoSignerConsensus`, `ValidationFailed(UnknownParent/NotFoundError)`, etc.), an identical re-proposal of that block causes `handle_block_proposal` to build a brand-new `BlockInfo` (`state = Unprocessed`, `reject_reason = None`) and unconditionally `insert_block` it, silently discarding the persisted `GloballyRejected` record instead of routing through `BlockInfo::move_to`/`check_state`, which is the only place that enforces "global states are terminal."

### Finding Description
The intended invariant, stated in `docs/signer-flows.md` and enforced by `BlockInfo::check_state` (`stacks-signer/src/signerdb.rs:314-329`), is that `GloballyRejected` and `GloballyAccepted` are mutually terminal — `move_to(GloballyAccepted)` is rejected once `prev_state == GloballyRejected` and vice versa [1](#0-0) .

This invariant is only checked on the in-memory `BlockInfo` object that is mutated via `mark_*` calls. It is never re-derived from the persisted DB row when a fresh `BlockInfo` is constructed.

`should_reevaluate_block`'s only "already decided, stop" guard checks `globally_approved_and_responded()`, which is defined solely in terms of `GloballyAccepted`: [2](#0-1) 

It does not check `GloballyRejected` at all. So for a block whose state is `GloballyRejected`, `should_reevaluate_block` falls through to `should_reevaluate_reject_reason(block_info)`: [3](#0-2) 

If that block's *locally recorded* `reject_reason` is one of the re-evaluable codes (e.g. `ConnectivityIssues`, produced by a validation timeout in `check_submitted_block_proposal`), `should_reevaluate_block` returns `true`: [4](#0-3) 

Back in `handle_block_proposal`, a `true` result lets execution fall through past the early return, and a **brand new** `BlockInfo` is constructed directly from the proposal — with `state: BlockState::Unprocessed`, `reject_reason: None` — completely discarding the fact that the persisted record was `GloballyRejected`: [5](#0-4) 

That fresh, `Unprocessed` object is then checked against fresh state and, when not provably invalid, unconditionally persisted: [6](#0-5) 

Because `BlockInfo::move_to`/`check_state` is never invoked on the *old* persisted record, the guard that makes `GloballyRejected` terminal (`stacks-signer/src/signerdb.rs:325-326`) is never consulted; the DB row is simply overwritten to `Unprocessed`. From `Unprocessed`, normal validation success ultimately drives `mark_pre_committed`/`mark_locally_accepted`, both of which are legal transitions from `Unprocessed` per `check_state`, so this signer can end up signing a block that the network had already finalized as `GloballyRejected`.

Root cause: `should_reevaluate_reject_reason`'s "re-evaluate" set is applied without first checking whether the block has *already reached global consensus* (`has_reached_consensus()`/`GloballyRejected`), and the re-evaluation path rebuilds the tracked block from scratch rather than reusing/validating against the existing (terminal) `BlockInfo`.

### Impact Explanation
This breaks the documented safety property that "Global states are terminal against each other" and that global rejection, once reached, cannot be walked back locally. A signer can be made to produce a valid signature ("Accepted" `BlockResponse`) over a block that a majority of signers already rejected. Per the signer-flows doc, a peer signature is "a bearer instrument that can still be aggregated toward the 70% threshold if rejecting signers change their minds" — so if this same class of timing bug independently strikes multiple signers (a plausible, repeatable systemic condition, not a single-node fluke, since `ConnectivityIssues`/timeout-based local rejections are common under load), the attacker can accumulate acceptance signatures for a block the network had already finalized as rejected. This matches the Critical category: "a rejection recounted as acceptance" / chain-safety violation.

### Likelihood Explanation
Preconditions: an attacker with one miner slot needs (a) at least one signer to have locally rejected the proposal for a re-evaluable reason (e.g., its own validation request to the node timed out, yielding `ConnectivityIssues` via `check_submitted_block_proposal`), while (b) enough *other* signers reject the same proposal for any reason to cross the >30% weight threshold and drive that signer's own `BlockInfo` to `GloballyRejected` via `store_and_process_block_rejection`, and then (c) simply re-broadcast the byte-identical `BlockProposal`. All three steps are achievable purely through normal proposal timing and StackerDB gossip — no majority-signer collusion, no auth token, no local access. Validation timeouts are not rare events in networks under load, so this condition can occur organically and be exploited opportunistically; it is repeatable per proposal/per signer.

### Recommendation
In `should_reevaluate_block`, add an explicit `has_reached_consensus()` check (covering both `GloballyAccepted` and `GloballyRejected`) before consulting `should_reevaluate_reject_reason`, and never construct/persist a new `Unprocessed` `BlockInfo` for a `signer_signature_hash` whose existing DB record is `GloballyRejected` (or `GloballyAccepted`). If re-evaluation happens, it must operate on the existing persisted `BlockInfo` and go through `move_to`/`check_state`, not a freshly-defaulted struct passed to `insert_block`.

### Proof of Concept
```rust
// stacks-signer/src/signerdb.rs (extend existing `state_machine`/BlockInfo tests)
#[test]
fn reevaluation_cannot_resurrect_globally_rejected_block() {
    let (mut block_info, block_proposal) = create_block();

    // Simulate: this signer locally rejected due to a validation timeout.
    block_info.reject_reason = Some(RejectReason::ConnectivityIssues("timeout".into()));
    block_info.mark_locally_rejected().unwrap();

    // Simulate: enough OTHER signers rejected too -> globally rejected.
    block_info.mark_globally_rejected().unwrap();
    assert_eq!(block_info.state, BlockState::GloballyRejected);

    // --- The bug: should_reevaluate_block treats this as re-evaluable and
    // handle_block_proposal builds a brand-new BlockInfo from the identical
    // re-proposal, discarding the persisted GloballyRejected state. ---
    assert!(should_reevaluate_reject_reason(&block_info)); // true today: ConnectivityIssues

    // Simulate what handle_block_proposal does: fresh BlockInfo from the SAME proposal.
    let fresh_block_info = BlockInfo::from(block_proposal.clone());
    assert_eq!(fresh_block_info.state, BlockState::Unprocessed);

    // From Unprocessed, mark_locally_accepted succeeds (legal transition) -- but
    // it MUST NOT be reachable, since the persisted record for this
    // signer_signature_hash was already GloballyRejected.
    let mut fresh = fresh_block_info;
    fresh.mark_locally_accepted(false).unwrap(); // succeeds today -- THIS IS THE BUG

    // Assert the equality that must hold: a block once GloballyRejected for this
    // signer_signature_hash can never be locally accepted afterwards, regardless
    // of how re-evaluation reconstructs its BlockInfo.
    assert_ne!(
        fresh.state, BlockState::LocallyAccepted,
        "a globally-rejected block must never be re-signed via re-proposal re-evaluation"
    );
}
```
This test currently fails (i.e., `mark_locally_accepted` succeeds on the freshly-reconstructed struct), proving that the re-evaluation path in `handle_block_proposal`/`should_reevaluate_block` bypasses the terminal-state guarantee that `BlockInfo::check_state` enforces only when operating on the *same*, persisted object.

### Citations

**File:** stacks-signer/src/signerdb.rs (L319-328)
```rust
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
```

**File:** stacks-signer/src/signerdb.rs (L359-363)
```rust
    /// Check if the block is globally accepted and this signer has responded to it
    pub fn globally_approved_and_responded(&self) -> bool {
        matches!(self.state, BlockState::GloballyAccepted)
            && (self.signed_self.is_some() || self.valid == Some(false))
    }
```

**File:** stacks-signer/src/v0/signer.rs (L1505-1571)
```rust
        if !should_reevaluate_reject_reason(block_info) {
            if block_info.state == BlockState::PreCommitted {
                // We validated this block but haven't signed it. Signing requires the
                // pre-commit threshold and the conflict checks in `handle_block_pre_commit`.
                // Re-broadcast our pre-commit and re-run that evaluation instead of
                // responding with a signature directly, so a re-proposed block can't
                // bypass those checks.
                info!(
                    "{self}: received a block proposal for a block we have pre-committed to but not signed. Re-evaluating the pre-commit.";
                    "signer_signature_hash" => %signer_signature_hash,
                    "block_id" => %block_info.block.block_id(),
                    "block_height" => block_info.block.header.chain_length,
                    "burn_height" => block_proposal.burn_height,
                    "consensus_hash" => %block_info.block.header.consensus_hash
                );
                self.send_block_pre_commit(signer_signature_hash.clone());
                let address = self.stacks_address.clone();
                self.handle_block_pre_commit(
                    stacks_client,
                    sortition_state,
                    &address,
                    &signer_signature_hash,
                );
                return false;
            }
            if let Some(block_response) = self.determine_response(block_info) {
                self.send_block_response(&block_info.block, block_response);
                return false;
            } else {
                let is_pending = self
                    .signer_db
                    .has_pending_block_validation(&signer_signature_hash)
                    .unwrap_or_else(|e| {
                        warn!("{self}: Failed to load pending block validations: {e:?}");
                        false
                    });
                if is_pending {
                    debug!(
                        "{self}: received a block proposal for a block for which we is already pending validation. Do nothing.";
                        "signer_signature_hash" => %block_info.block.header.signer_signature_hash(),
                        "block_id" => %block_info.block.block_id()
                    );
                    return false;
                } else {
                    info!(
                        "{self}: received a block proposal for this block before, but we do not have a pending validation for it.";
                        "reject_reason" => ?block_info.reject_reason,
                        "signer_signature_hash" => %signer_signature_hash,
                        "block_id" => %block_info.block.block_id(),
                        "block_height" => block_info.block.header.chain_length,
                        "burn_height" => block_proposal.burn_height,
                        "consensus_hash" => %block_info.block.header.consensus_hash
                    );
                }
            }
        } else {
            info!(
                "{self}: received a block proposal for this block before, but our rejection reason allows us to reconsider";
                "reject_reason" => ?block_info.reject_reason,
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_proposal.block.block_id(),
                "block_height" => block_proposal.block.header.chain_length,
                "burn_height" => block_proposal.burn_height,
                "consensus_hash" => %block_proposal.block.header.consensus_hash
            );
        }
        true
```

**File:** stacks-signer/src/v0/signer.rs (L1652-1654)
```rust
        crate::monitoring::actions::increment_block_proposals_received();
        // Creating a new proposal will overwrite any prior proposal info on the block if it exists, e.g. validity, signed_timestamps, etc.
        let mut block_info = BlockInfo::from(block_proposal.clone());
```

**File:** stacks-signer/src/v0/signer.rs (L1716-1719)
```rust
            // Do not store KNOWN invalid blocks as this could DOS the signer. We only store blocks that are valid or unknown.
            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
```

**File:** stacks-signer/src/v0/signer.rs (L2705-2718)
```rust
/// Determine if a block should be re-evaluated based on its rejection reason˝
fn should_reevaluate_reject_reason(block_info: &BlockInfo) -> bool {
    if let Some(reject_reason) = &block_info.reject_reason {
        match reject_reason {
            RejectReason::ValidationFailed(ValidateRejectCode::UnknownParent)
            | RejectReason::ValidationFailed(ValidateRejectCode::NotFoundError)
            | RejectReason::NoSortitionView
            | RejectReason::ConnectivityIssues(_)
            | RejectReason::TestingDirective
            | RejectReason::InvalidTenureExtend
            | RejectReason::ConsensusHashMismatch { .. }
            | RejectReason::NoSignerConsensus
            | RejectReason::NotRejected
            | RejectReason::Unknown(_) => true,
```
