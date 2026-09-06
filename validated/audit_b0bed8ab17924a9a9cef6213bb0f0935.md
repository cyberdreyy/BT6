### Title
Signer re-checks tenure-confirmation state before signing but never re-verifies miner identity/consensus-hash/bitvec, letting a since-superseded proposal be signed - ([File: stacks-signer/src/v0/signer.rs])

### Summary
Analogous to the Teller `lenderAcceptBid` bug (a value checked at decision time can change before the value is actually acted on, so the action executes against stale terms), the v0 signer validates a block's miner-legitimacy (`ConsensusHashMismatch`, `PubkeyHashMismatch`, `InvalidBitvec`, tenure-extend legitimacy) exactly once, at proposal time, inside `GlobalStateView::check_proposal`. That verdict is cached as the block's stored validity and is never re-derived against the signer's *current* view before the signature is actually emitted at the pre-commit threshold, even though the local miner-view (`local_state_machine.current_miner`) can legitimately change in the intervening window.

### Finding Description
`GlobalStateView::check_proposal` (`stacks-signer/src/chainstate/v2.rs:113-197`) performs the checks that decide whether a proposal's *miner is the one the signer currently believes is active*:
- consensus-hash match against `tenure_id` (`ConsensusHashMismatch`, lines 133-145)
- miner pubkey-hash match against `current_miner_pkh` (`PubkeyHashMismatch`, lines 146-163)
- bitvec (`InvalidBitvec`, lines 164-174)
- tenure-change/tenure-extend legitimacy against the *current* `MinerState::ActiveMiner`

This all runs once, from `handle_block_proposal` → `check_block_against_state` (`stacks-signer/src/v0/signer.rs:1671-1672`), against the local state machine's view of the active miner *at that instant*. The result (valid/invalid) is stored on the `BlockInfo` and the block moves to `PreCommitted`.

Per `docs/signer-flows.md:425-433` (confirmed against the code): "Two things belong to the proposal path only and are **not** re-run at validate-ok or at signing: ... the v2 `check_proposal` wrapper checks miner pubkey hash, consensus hash, the pox bitvec, and tenure-extend rules before delegating here." The re-check that *is* run before the signature leaves the box — `check_block_against_signer_db_state`, invoked from `handle_block_pre_commit` at `stacks-signer/src/v0/signer.rs:1345-1366` once the ≥70% pre-commit weight threshold (`NakamotoBlockHeader::compute_voting_weight_threshold`) is reached — only re-derives `check_latest_block_in_tenure` (tenure-tip confirmation) and the signed-conflict/reorg-permit logic (lines 1368-1465). It does **not** call back into `GlobalStateView::check_proposal`, so it never re-checks `current_miner_pkh`, `tenure_id`, or the bitvec against the signer's live `local_state_machine`.

Between the moment a proposal passes `check_proposal` and the moment the pre-commit weight threshold is reached (this window can be long — it's gated on gossip of pre-commits across the whole signer set), the signer's own view of "who the active miner is" can change: `capitulate_viewpoint`, `check_miner_inactivity` (marking a miner `InvalidatedBeforeFirstBlock`/timed out), or a new `NewBurnBlock`/`bitcoin_block_arrival` event can all update `local_state_machine.current_miner` to a different `ActiveMiner`, `NoValidMiner`, or a different `tenure_id`/`current_miner_pkh`. None of these transitions re-run `check_block_against_global_state` on already-`PreCommitted` blocks; they only affect the *next* fresh proposal evaluation. The pre-commit-threshold re-check path is blind to this drift.

Consequently a signer can end up broadcasting a signature (`mark_locally_accepted` at line 1467, `handle_block_signature`/`send_block_response` at 1475-1478) over a block whose miner identity/consensus-hash it would, at the moment of signing, itself judge `InvalidMiner`/`ConsensusHashMismatch`/`PubkeyHashMismatch` if `check_proposal` were re-run — i.e. the equality "validated-miner-identity == signed-miner-identity" is broken. This is the direct analog of Teller's stale-fee-at-execution bug: a value (miner legitimacy) is locked in at decision time and used to gate an irreversible action (signature) taken later, without re-confirming the value is still current.

### Impact Explanation
This breaks the "signed vs validated" equality called out as Critical in scope: a signer produces its cryptographic signature over a block it has independently concluded — via its own, more current, state-machine view — comes from a non-canonical or already-superseded miner/tenure. Since block validity in Nakamoto is a threshold-weighted vote over exactly these signatures, a subset of signers signing under stale miner-legitimacy assumptions contributes weight toward finalizing a block that conflicts with the network's canonical miner selection, undermining the signer set's core safety guarantee (only sign blocks from the currently valid/canonical miner).

### Likelihood Explanation
This requires only ordinary, permissionless conditions already assumed adversarial in scope: a one-slot miner or normal chain activity (a burn-chain re-org, another miner's sortition win, a timed-out/invalidated miner) occurring in the gap between proposal broadcast and pre-commit-threshold gossip completing — no majority collusion is needed, since the flaw is in a single signer's own re-check logic being incomplete, not in the vote-counting itself. The window is bounded by pre-commit gossip latency, which under network delay or bursts of pre-commit traffic can be long enough for a legitimate state transition to land.

### Recommendation
Before emitting a signature at the pre-commit threshold in `handle_block_pre_commit`, re-run the full `GlobalStateView::check_proposal` (or at minimum its miner-pkh/consensus-hash/bitvec/tenure-change legitimacy sub-checks) against the signer's current `local_state_machine`, not just `check_block_against_signer_db_state`'s tenure-tip/conflict logic. If the block no longer matches the live miner view, treat it the same as a chainstate-check failure: `mark_locally_rejected` and broadcast a rejection instead of signing.

### Proof of Concept
1. Miner A wins the sortition for tenure T and proposes tenure-start block B (`consensus_hash = T`, signed by A's key).
2. Signer S receives B; `local_state_machine.current_miner = ActiveMiner{ current_miner_pkh: A, tenure_id: T, .. }`. `check_proposal` passes (no `ConsensusHashMismatch`/`PubkeyHashMismatch`/`InvalidBitvec`). Node validation returns Ok; S calls `check_block_against_signer_db_state` (tenure-tip check only) → passes → `mark_pre_committed`, broadcasts pre-commit (`stacks-signer/src/v0/signer.rs:1250-1366`, section 4 of `docs/signer-flows.md`).
3. Before enough peer pre-commits arrive to cross the ≥70% `compute_voting_weight_threshold`, a burn-chain event occurs (e.g., miner A is judged timed out via `check_miner_inactivity`, or a fork changes the sortition winner) and S's `local_state_machine.capitulate_viewpoint`/`bitcoin_block_arrival` updates `current_miner` to a different `ActiveMiner` (miner A' with a different `tenure_id`/`current_miner_pkh`), or to `NoValidMiner`. This update does not touch B's stored `BlockInfo` or reject it.
4. Enough peer pre-commits for B (from signers who haven't yet processed the same update, or via delayed gossip) arrive at S, crossing the threshold. `handle_block_pre_commit` re-runs only `check_block_against_signer_db_state` (tenure-tip/conflict checks), which B still passes since it is not derived from `current_miner`. S signs B (`mark_locally_accepted`, `handle_block_signature`, broadcast acceptance) even though, per S's own current `local_state_machine`, B would now fail `check_proposal` with `ConsensusHashMismatch`/`PubkeyHashMismatch`/`InvalidMiner`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** stacks-signer/src/chainstate/v2.rs (L111-163)
```rust
impl GlobalStateView {
    /// Apply checks from the signer state machine on the block proposal.
    pub fn check_proposal(
        &self,
        client: &StacksClient,
        signer_db: &mut SignerDb,
        block: &NakamotoBlock,
    ) -> Result<(), RejectReason> {
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
        if &block.header.consensus_hash != tenure_id {
            info!("Miner block proposal consensus hash does not match the current miner's tenure id. Considering invalid.";
                "block_height" => block.header.chain_length,
                "signer_signature_hash" => %block.header.signer_signature_hash(),
                "block_consensus_hash" => %block.header.consensus_hash,
                "active_miner_tenure_id" => %tenure_id,
                "active_miner_parent_tenure_id" => %parent_tenure_id,
            );
            return Err(RejectReason::ConsensusHashMismatch {
                actual: block.header.consensus_hash.clone(),
                expected: tenure_id.clone(),
            });
        }
        let Some(miner_pk) = block.header.recover_miner_pk() else {
            warn!("Failed to recover miner pubkey";
                  "signer_signature_hash" => %block.header.signer_signature_hash(),
                  "consensus_hash" => %block.header.consensus_hash);
            return Err(RejectReason::IrrecoverablePubkeyHash);
        };
        let miner_pkh = Hash160::from_data(&miner_pk.to_bytes_compressed());
        if current_miner_pkh != &miner_pkh {
            warn!(
                "Miner block proposal pubkey does not match the winning pubkey hash for its sortition. Considering invalid.";
                "proposed_block_consensus_hash" => %block.header.consensus_hash,
                "signer_signature_hash" => %block.header.signer_signature_hash(),
                "proposed_block_pubkey" => &miner_pk.to_hex(),
                "proposed_block_pubkey_hash" => %miner_pkh,
                "active_miner_pubkey_hash" => %current_miner_pkh,
            );
            return Err(RejectReason::PubkeyHashMismatch);
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1345-1366)
```rust
        if let Some(block_rejection) =
            self.check_block_against_signer_db_state(stacks_client, &block_info.block)
        {
            warn!(
                "{self}: Reached the pre-commit threshold for a block, but it no longer passes the chainstate checks. Rejecting.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "reject_code" => %block_rejection.reason_code,
                "reject_reason" => &block_rejection.reason,
            );
            if let Err(e) = block_info.mark_locally_rejected() {
                if !block_info.has_reached_consensus() {
                    warn!("{self}: Failed to mark block as locally rejected: {e:?}");
                }
            };
            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
            self.handle_block_rejection(&block_rejection, sortition_state);
            self.send_block_response(&block_info.block, block_rejection.into());
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1466-1478)
```rust
        // It is only considered globally accepted IFF we receive a new block event confirming it OR see the chain tip of the node advance to it.
        if let Err(e) = block_info.mark_locally_accepted(false) {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally accepted: {e:?}",);
            }
        }
        self.signer_db
            .insert_block(&block_info)
            .unwrap_or_else(|e| self.handle_insert_block_error(e));
        let accepted = self.create_block_acceptance(&block_info.block);
        // have to save the signature _after_ the block info
        self.handle_block_signature(stacks_client, sortition_state, &accepted);
        self.send_block_response(&block_info.block, accepted.into());
```

**File:** docs/signer-flows.md (L420-437)
```markdown
A failed check becomes a different rejection depending on who asked.
`check_block_against_signer_db_state` returns `SortitionViewMismatch`, or
`ConnectivityIssues` when the lookup itself errored rather than answering; the v2
`check_proposal` path returns `InvalidParentBlock`.

Two things belong to the proposal path only and are **not** re-run at validate-ok
or at signing:

- `validate_tenure_change_payload` rejects with `DuplicateBlockFound` when we
  have already accepted a block in the tenure a tenure-change block is starting.
  v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1
  counts only globally accepted ones (`get_last_globally_accepted_block`).
- the v2 `check_proposal` wrapper checks miner pubkey hash, consensus hash, the
  pox bitvec, and tenure-extend rules before delegating here.

Because the duplicate check never runs again, a block that crosses the pre-commit
threshold long after it was proposed relies on section 5's own-tenure conflict
guard to cover the same ground.
```
