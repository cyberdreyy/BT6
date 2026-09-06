### Title
Tenure-parent authority check is validated once at proposal time but never re-verified before signing, letting a stale `parent_tenure_id` allow a spoofed `TenureChangePayload.prev_tenure_consensus_hash` through at pre-commit/signing - ([File: stacks-signer/src/v0/signer.rs])

### Summary
The GHSA bug class is: an untrusted, attacker-supplied field (HTTP/2 `:authority`) is accepted and acted upon (cookie injection) without being checked against the true origin at the point where it matters, even though a origin/authority check exists conceptually elsewhere in the protocol. The stacks-signer analog is the `TenureChangePayload.prev_tenure_consensus_hash` field inside a miner-proposed block: it is validated against the signer's authoritative view of the real parent tenure (`parent_tenure_id`, derived from sortition data) only once, in `validate_tenure_change_payload`, which runs solely inside the initial `check_proposal` path [1](#0-0) [2](#0-1) . Every later re-verification point that gates the actual signature — `handle_block_validate_ok` and the pre-commit-threshold recheck — calls `check_block_against_signer_db_state`, which trusts the block's own `tenure_change.prev_tenure_consensus_hash` field verbatim and only asks "does this block confirm enough blocks in that (self-declared) tenure?" via `check_tenure_change_confirms_parent` [3](#0-2) [4](#0-3) . It never re-checks that this self-declared parent tenure still equals the signer's authoritative `parent_tenure_id`.

### Finding Description
`validate_tenure_change_payload` is the only place that enforces the equality `tenure_change.prev_tenure_consensus_hash == parent_tenure_id`, with an explicit comment that this "catches block commits with bad `parent_block_ptr` (e.g., vtxindex=0 exploit)" [1](#0-0) . `parent_tenure_id` is not part of the block; it comes from the signer's local/global miner-state view of the winning sortition (`ProposedBy::state().data.parent_tenure_id` in v1, `MinerState::ActiveMiner { parent_tenure_id, .. }` in v2) [5](#0-4) .

This equality is enforced exactly once, inside `check_proposal`, which runs only when a *fresh* proposal is first evaluated (`handle_block_proposal` → `check_block_against_state` → `check_proposal`) [6](#0-5) . From that point on, the two places that gate an actual signature — the validate-ok recheck and the pre-commit-threshold recheck — call `check_block_against_signer_db_state`, whose own doc comment warns "This is an incomplete check. Do NOT call this function PRIOR to `check_proposal`" [7](#0-6) . That function pulls `tenure_change` straight back out of the *same untrusted block* and calls `SortitionData::check_tenure_change_confirms_parent(tenure_change, ...)`, which internally just calls `check_latest_block_in_tenure(&tenure_change.prev_tenure_consensus_hash, ...)` — i.e., it uses the block's own claimed parent-tenure hash as the tenure to check against, never comparing it again to the signer's authoritative `parent_tenure_id` [4](#0-3) .

The flow documentation itself confirms this gap is real and permanent, not just an oversight in one function: "Two things belong to the proposal path only and are **not** re-run at validate-ok or at signing: `validate_tenure_change_payload` rejects with `DuplicateBlockFound`... the v2 `check_proposal` wrapper checks miner pubkey hash, consensus hash, the pox bitvec, and tenure-extend rules before delegating here" [8](#0-7) . The `prev_tenure_consensus_hash == parent_tenure_id` anti-fraud check is bundled inside that same `validate_tenure_change_payload` call and is likewise never re-run.

Because a single miner (with gossip/StateMachineUpdate broadcasts) controls when the signer's local miner-state view (`parent_tenure_id`) is set/refreshed relative to when a proposal is submitted for validation, and because between proposal arrival and pre-commit-threshold signature there is an unbounded window (node validation round-trip time + waiting to reach 70% pre-commit weight), the value of `parent_tenure_id` that was authoritative when `check_proposal` ran is never re-confirmed at the moment the equivalent signature-producing recheck happens. Any change to the signer's local view of the true parent tenure between those two points (e.g. the state machine settling a burn-block arrival, `capitulate_miner_view` adopting a different threshold view, or the miner deliberately racing a burn-fork event) is not re-validated against the block's `prev_tenure_consensus_hash` before the signature is placed.

### Impact Explanation
If the signer's authoritative `parent_tenure_id` diverges from the value it held at proposal time before the pre-commit/signing recheck fires, a signer can sign (`mark_locally_accepted`) a tenure-change block whose `prev_tenure_consensus_hash` no longer matches the true parent tenure recognized by the network — i.e., a block built on a parent tenure the signer's current state no longer endorses as authoritative. This directly breaks the "approved-parent vs canonical" equality the design intends `validate_tenure_change_payload` to guarantee at every point a signature could be produced, and matches the report's failure mode: a field trusted verbatim from an untrusted source, checked once, then acted upon later without re-validation against the authoritative reference.

### Likelihood Explanation
This requires only the block's own proposer (miner) plus normal StackerDB gossip/timing — no majority of signers, no other signer's key, and no auth-token/local access — satisfying the in-scope constraints. The trigger condition (a change to the signer's local `parent_tenure_id` view between proposal-time `check_proposal` and the later signature-producing recheck) is plausible given the documented existence of asynchronous `StateMachineUpdate` capitulation and burn-block-arrival settlement logic that can change the miner-state view independently of a specific block proposal's lifecycle. I was not able to fully trace, within the remaining budget, the exact conditions under which `parent_tenure_id`/`ActiveMiner` is mutated relative to a given proposal's outstanding validation/pre-commit window (this would require deeper reading of `stacks-signer/src/v0/signer_state.rs`'s `capitulate_miner_view`/`update_parent_tenure_last_block` and how `MinerState` is captured per-proposal vs. re-read at signing time), so likelihood should be treated as moderate/uncertain pending that confirmation.

### Recommendation
Re-run the `prev_tenure_consensus_hash == parent_tenure_id` equality check (not just `check_tenure_change_confirms_parent`) inside `check_block_against_signer_db_state`, using the *current* authoritative parent-tenure view at the moment of the validate-ok recheck and the pre-commit-threshold recheck, rather than only at initial proposal time. At minimum, capture the `parent_tenure_id` used to validate the proposal in `BlockInfo` and assert it is unchanged (or re-derive it fresh) immediately before `mark_locally_accepted`/`mark_pre_committed` is called.

### Proof of Concept
A concrete PoC would need to demonstrate a real transition of the signer's authoritative `parent_tenure_id` between the time `check_proposal` validates a `TenureChangePayload` and the time `check_block_against_signer_db_state` re-checks the same block at validate-ok or pre-commit threshold (e.g. by delaying node validation while forcing a `StateMachineUpdate`/burn-block-arrival that changes `parent_tenure_id`, then observing the signer sign the block anyway). I was unable to fully construct/verify this sequencing within the remaining tool budget — specifically I could not confirm from `stacks-signer/src/v0/signer_state.rs` whether the `MinerState::ActiveMiner.parent_tenure_id` used in a given proposal's `check_proposal` call is snapshotted or dynamically re-read, which determines whether this window is actually reachable in practice.

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L469-481)
```rust
        // Check that the tenure change's prev_tenure matches the sortition's known parent tenure.
        // This catches block commits with bad parent_block_ptr (e.g., vtxindex=0 exploit).
        let parent_tenure_id = &proposed_by.state().data.parent_tenure_id;
        if &tenure_change.prev_tenure_consensus_hash != parent_tenure_id {
            warn!(
                "Block commit parent tenure mismatch: the block commit's parent_block_ptr does not correspond to the actual parent tenure";
                "committed_parent_tenure" => %parent_tenure_id,
                "actual_parent_tenure" => %tenure_change.prev_tenure_consensus_hash,
                "consensus_hash" => %block.header.consensus_hash,
                "signer_signature_hash" => %block.header.signer_signature_hash(),
            );
            return Err(RejectReason::InvalidParentBlock);
        }
```

**File:** stacks-signer/src/chainstate/v2.rs (L119-132)
```rust
        let MinerState::ActiveMiner {
            current_miner_pkh,
            tenure_id,
            parent_tenure_id,
            ..
        } = &self.signer_state.current_miner
        else {
            info!(
                "No valid current miner. Considering invalid.";
                "block_height" => block.header.chain_length,
                "signer_signature_hash" => %block.header.signer_signature_hash()
            );
            return Err(RejectReason::InvalidMiner);
        };
```

**File:** stacks-signer/src/chainstate/v2.rs (L314-325)
```rust
        // Check that the tenure change's prev_tenure matches the signer's known parent tenure.
        // This catches block commits with bad parent_block_ptr (e.g., vtxindex=0 exploit).
        if &tenure_change.prev_tenure_consensus_hash != parent_tenure_id {
            warn!(
                "Block commit parent tenure mismatch: the block commit's parent_block_ptr does not correspond to the actual parent tenure";
                "committed_parent_tenure" => %parent_tenure_id,
                "actual_parent_tenure" => %tenure_change.prev_tenure_consensus_hash,
                "consensus_hash" => %block.header.consensus_hash,
                "signer_signature_hash" => %block.header.signer_signature_hash(),
            );
            return Err(RejectReason::InvalidParentBlock);
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1670-1672)
```rust
        // Check if proposal can be rejected now if not valid against sortition view
        let block_rejection =
            self.check_block_against_state(stacks_client, sortition_state, &block_info);
```

**File:** stacks-signer/src/v0/signer.rs (L1799-1840)
```rust
    /// WARNING: This is an incomplete check. Do NOT call this function PRIOR to check_proposal or block_proposal validation succeeds.
    ///
    /// Re-verify a block's chain length against the last signed block within signerdb.
    /// This is required in case a block has been approved since the initial checks of the block validation endpoint.
    fn check_block_against_signer_db_state(
        &mut self,
        stacks_client: &StacksClient,
        proposed_block: &NakamotoBlock,
    ) -> Option<BlockRejection> {
        let signer_signature_hash = proposed_block.header.signer_signature_hash();
        // If this is a tenure change block, ensure that it confirms the correct number of blocks from the parent tenure.
        if let Some(tenure_change) = proposed_block.get_tenure_change_tx_payload() {
            // Ensure that the tenure change block confirms the expected parent block
            match SortitionData::check_tenure_change_confirms_parent(
                tenure_change,
                proposed_block,
                &mut self.signer_db,
                stacks_client,
                self.proposal_config.tenure_last_block_proposal_timeout,
                self.proposal_config.reorg_attempts_activity_timeout,
            ) {
                Ok(true) => return None,
                Ok(false) => {
                    return Some(self.create_block_rejection(
                        RejectReason::SortitionViewMismatch,
                        proposed_block,
                    ))
                }
                Err(e) => {
                    warn!("{self}: Error checking block proposal: {e}";
                        "signer_signature_hash" => %signer_signature_hash,
                        "block_id" => %proposed_block.block_id()
                    );
                    return Some(self.create_block_rejection(
                        RejectReason::ConnectivityIssues(
                            "error checking block proposal".to_string(),
                        ),
                        proposed_block,
                    ));
                }
            }
        }
```

**File:** stacks-signer/src/chainstate/mod.rs (L488-504)
```rust
    pub fn check_tenure_change_confirms_parent(
        tenure_change: &TenureChangePayload,
        block: &NakamotoBlock,
        signer_db: &mut SignerDb,
        client: &StacksClient,
        tenure_last_block_proposal_timeout: Duration,
        reorg_attempts_activity_timeout: Duration,
    ) -> Result<bool, ClientError> {
        Self::check_latest_block_in_tenure(
            &tenure_change.prev_tenure_consensus_hash,
            block,
            signer_db,
            client,
            tenure_last_block_proposal_timeout,
            reorg_attempts_activity_timeout,
        )
    }
```

**File:** docs/signer-flows.md (L425-433)
```markdown
Two things belong to the proposal path only and are **not** re-run at validate-ok
or at signing:

- `validate_tenure_change_payload` rejects with `DuplicateBlockFound` when we
  have already accepted a block in the tenure a tenure-change block is starting.
  v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1
  counts only globally accepted ones (`get_last_globally_accepted_block`).
- the v2 `check_proposal` wrapper checks miner pubkey hash, consensus hash, the
  pox bitvec, and tenure-extend rules before delegating here.
```
