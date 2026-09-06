### Title
Signer can sign two conflicting tenure-start blocks for the same tenure when the second confirms a taller parent — the proposal-only `DuplicateBlockFound` guard is not re-checked at validate-ok/pre-commit, and its height-bound substitute misses lower-height conflicts - (File: `stacks-signer/src/chainstate/v1.rs`, `stacks-signer/src/chainstate/v2.rs`, `stacks-signer/src/v0/signer.rs`)

### Summary
The signer rejects a second `BlockFound` tenure-change proposal for a tenure it has already signed a block in — but only inside `validate_tenure_change_payload`, which runs exclusively on the **proposal** path (`check_proposal`). The corresponding re-checks that run later, at validate-ok and at pre-commit/signing time (`check_block_against_signer_db_state` and the own-tenure conflict guard in `handle_block_pre_commit`), never repeat this "already signed a block in this tenure" test. Instead they substitute a height-bounded query (`get_signed_conflicts(height, …)`, `stacks_height >= height`). If the second tenure-start proposal for the same tenure legitimately confirms a taller parent (a higher `chain_length`) than the block already signed, it is invisible to that height-bounded query, so nothing blocks the signer from placing a second signature over a conflicting tenure-start block in the same tenure.

### Finding Description
`validate_tenure_change_payload` is the only place that unconditionally rejects a duplicate tenure-change proposal for a tenure already signed: [1](#0-0) [2](#0-1) 

This function is only invoked from `check_proposal`, i.e. at proposal-arrival time: [3](#0-2) [4](#0-3) 

It is documented as proposal-only and explicitly *not* re-run later: [5](#0-4) 

At validate-ok and at pre-commit-threshold signing, the shared re-check `check_block_against_signer_db_state` handles a tenure-change block by checking only the **parent** tenure's confirmation, never the block's own tenure: [6](#0-5) 

The only remaining safeguard for a same-tenure duplicate at signing time is the own-tenure conflict guard in `handle_block_pre_commit`, which is bounded by height: [7](#0-6) [8](#0-7) 

and the underlying query only returns blocks whose height is `>=` the newly proposed block's `chain_length`: [9](#0-8) 

Because a `chain_length` for a tenure-start block is `parent_height + 1`, a second `BlockFound` tenure-change block for the *same tenure* that confirms a *taller* parent (parent grew between the two proposals) will have a strictly *higher* `chain_length` than the block the signer already signed. `get_signed_conflicts(new_chain_length, …)` will not return the already-signed block (its `stacks_height < new_chain_length`), so `conflicts` is empty, the same-tenure branch at line 1432 never fires, and the block is signed at line 1467 (`mark_locally_accepted`) with no further check.

Sequence:
1. Miner proposes tenure-start Block A for tenure T at `chain_length = N`. At proposal time nothing has been signed for T yet, so `validate_tenure_change_payload` passes for A.
2. A is submitted for validation, passes `check_block_against_signer_db_state` (checks parent tenure only), reaches pre-commit, and — assuming no fresher same-height conflict exists yet — crosses the 70% pre-commit threshold and gets signed (`signed_self` set, state `LocallyAccepted`).
3. Before A is *globally* accepted (pushed to and processed by the node), the parent tenure gains additional confirmed/signed blocks, and the miner proposes Block B — also a `BlockFound` tenure-change for the *same* tenure T, but confirming the now-taller parent, so `chain_length = N + k` (`k > 0`).
4. At proposal time, `validate_tenure_change_payload` for B queries for an already-signed block in tenure T: in v2 this is `get_last_signed_block` (locally or globally accepted) — this *would* catch a `LocallyAccepted` A and reject B with `DuplicateBlockFound`. In v1 the same guard only checks `get_last_globally_accepted_block`, so a merely `LocallyAccepted` A is invisible and B passes proposal-time checks.
5. B is submitted for validation and passes `check_block_against_signer_db_state` (only the parent-tenure confirmation is checked for a tenure-change block; A, being in the *same* tenure T, is never consulted here).
6. B reaches the pre-commit threshold. `handle_block_pre_commit` calls `get_signed_conflicts(N + k, hash_B)`. Because A's height `N < N + k`, A is not returned as a conflict, `conflicts` is empty, and B is signed.

Result: the signer has now signed two different, conflicting tenure-start blocks (A and B) for the same tenure — a direct equivocation, breaking the "one signed block per tenure-start" invariant the whole pre-commit/conflict machinery exists to enforce (`docs/signer-flows.md` sections 5 and 7 explicitly describe this invariant and rely on the proposal-time duplicate check plus the height-bounded conflict guard to jointly cover it — but they only jointly cover it when the second block's height is `<=` the first's).

### Impact Explanation
This is a Critical-class outcome per the scan rubric: the signer signs a conflicting block (a second, competing tenure-start block for a tenure it already signed a start block for). Depending on which of A or B the rest of the network/majority ultimately assembles into a valid multi-sig block, this can manifest as the signer contributing to two mutually exclusive tenure histories, undermining the single-tenure-start guarantee the signer protocol is built to preserve. It is exactly the kind of "signer signing a conflicting block" scenario the task's impact criteria call out as Critical.

### Likelihood Explanation
This requires only the actions of the block-producing miner (a single actor, matching the "one-slot miner plus gossip" constraint) exploiting the normal asynchronous validation window that already exists in the signer (explicitly documented in `docs/signer-flows.md` as the "async-validation window" covered by the sibling-race tests in `stacks-signer/src/v0/tests.rs`). No signer majority, no other signer's key, and no StackerDB/transport defect is needed — only a second proposal for the same tenure with a legitimately taller confirmed parent, timed to land after the first has already achieved a local signature but before the height-bounded conflict query would see it as a conflict.

### Recommendation
Re-run the tenure-level "already signed a block in this tenure" check (equivalent to `validate_tenure_change_payload`'s `DuplicateBlockFound` test) inside `check_block_against_signer_db_state` for tenure-change blocks, matching purely on `consensus_hash` (own tenure) with no height bound — mirroring how the same-tenure branch in `handle_block_pre_commit` (lines 1432-1457) already matches on `consensus_hash` for the *stale*-conflict case, but making sure the *fresh* conflict path (and the `get_signed_conflicts` height-bounded query feeding it) also treats any prior signed tenure-start block in the identical tenure as a conflict, regardless of relative height.

### Proof of Concept
1. Set up a signer tracking tenure T with `tenure_last_block_proposal_timeout` long enough that a signature stays fresh.
2. Propose tenure-start Block A for tenure T at `chain_length = N`; let it validate and cross the 70% pre-commit threshold so the signer signs it (`signed_self` set, state `LocallyAccepted`) — before it is pushed to/accepted by the node.
3. Advance the parent tenure so its last signed block height increases (simulating more blocks being signed in the parent tenure between A's and B's proposals).
4. Propose tenure-start Block B for the same tenure T, with `chain_length = N + k` (confirming the taller parent), `k > 0`.
5. Let B pass `check_proposal` (in v1 this passes trivially if A is only `LocallyAccepted`; in v2, delay B's proposal until A is at least momentarily not yet reflected, or use the v1 build where `get_last_globally_accepted_block` is used), pass validate-ok (`check_block_against_signer_db_state` only checks the parent tenure), and cross the pre-commit threshold.
6. Observe `handle_block_pre_commit(B)` call `get_signed_conflicts(N + k, hash_B)`; since A's `stacks_height = N < N + k`, the query does not return A, `conflicts` is empty, and B is signed via `mark_locally_accepted` — producing two signed, conflicting tenure-start blocks (A and B) for tenure T. [10](#0-9) [9](#0-8)

### Citations

**File:** stacks-signer/src/chainstate/v2.rs (L176-185)
```rust
        if let Some(tenure_change) = block.get_tenure_change_tx_payload() {
            Self::validate_tenure_change_payload(
                tenure_change,
                block,
                parent_tenure_id,
                signer_db,
                client,
                &self.config,
            )?;
        } else {
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

**File:** stacks-signer/src/chainstate/v1.rs (L317-327)
```rust
        };

        if let Some(tenure_change) = block.get_tenure_change_tx_payload() {
            self.validate_tenure_change_payload(
                &proposed_by,
                tenure_change,
                block,
                signer_db,
                client,
            )?;
        } else {
```

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

**File:** docs/signer-flows.md (L425-437)
```markdown
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

**File:** stacks-signer/src/v0/signer.rs (L1432-1457)
```rust
        if conflicts.iter().any(|conflict| {
            conflict.consensus_hash == block_info.block.header.consensus_hash
                && !self.reorg_permit_stands(stacks_client, conflict)
        }) {
            match stacks_client.get_tenure_tip(&block_info.block.header.consensus_hash) {
                Ok(tip) => {
                    let tip_height = tip.anchored_header.height();
                    if tip_height >= block_info.block.header.chain_length {
                        warn!(
                            "{self}: Reached the pre-commit threshold for a block that conflicts with previously signed or accepted blocks, and the canonical tip of its tenure is already at or above the proposed height. Refusing to sign.";
                            "signer_signature_hash" => %block_hash,
                            "block_height" => block_info.block.header.chain_length,
                            "canonical_tip_height" => tip_height,
                        );
                        return;
                    }
                }
                Err(e) => {
                    warn!(
                        "{self}: Failed to fetch the canonical tip of the proposed block's tenure: {e:?}. Treating the tenure as unconfirmed.";
                        "signer_signature_hash" => %block_hash,
                        "consensus_hash" => %block_info.block.header.consensus_hash,
                    );
                }
            }
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1458-1470)
```rust
        if !conflicts.is_empty() {
            info!(
                "{self}: Reached the pre-commit threshold for a block that conflicts with previously signed or accepted blocks, but none of those conflicts still blocks it. Signing the replacement.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "num_conflicts" => conflicts.len(),
            );
        }
        // It is only considered globally accepted IFF we receive a new block event confirming it OR see the chain tip of the node advance to it.
        if let Err(e) = block_info.mark_locally_accepted(false) {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally accepted: {e:?}",);
            }
```

**File:** stacks-signer/src/v0/signer.rs (L1803-1840)
```rust
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

**File:** stacks-signer/src/signerdb.rs (L1606-1625)
```rust
    pub fn get_signed_conflicts(
        &self,
        height: u64,
        excluded_signer_signature_hash: &Sha512Trunc256Sum,
    ) -> Result<Vec<SignedConflictInfo>, DBError> {
        let query = "SELECT b.consensus_hash, b.signer_signature_hash, b.stacks_height, b.state,
                MAX(COALESCE(b.signed_self, 0), COALESCE(b.signed_group, 0)) AS last_endorsed,
                st.superseded_by_consensus_hash, st.superseded_by_burn_block_hash
            FROM blocks b
            LEFT JOIN superseded_tenures st ON st.consensus_hash = b.consensus_hash
            WHERE (b.signed_self IS NOT NULL OR b.signed_group IS NOT NULL)
                AND b.stacks_height >= ?1
                AND b.signer_signature_hash != ?2
            ORDER BY b.stacks_height DESC";
        let args = params![
            u64_to_sql(height)?,
            excluded_signer_signature_hash.to_string(),
        ];
        query_rows(&self.db, query, args)
    }
```
