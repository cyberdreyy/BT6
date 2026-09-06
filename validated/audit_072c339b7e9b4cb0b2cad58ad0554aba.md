### Title
V1 chainstate's tenure-duplicate check uses `get_last_globally_accepted_block` instead of `get_last_signed_block`, letting a signer sign two conflicting tenure-start blocks - ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
Under the pre-global-state ("v1") signer protocol path, `SortitionsView::validate_tenure_change_payload` rejects a competing tenure-start proposal as `DuplicateBlockFound` only if the signer has already **globally** accepted a block in that tenure. It does not consider a block that the signer itself already **locally accepted (and thus signed)** in the same tenure. The equivalent v2 check was fixed to use `get_last_signed_block` (locally- or globally-accepted) specifically to close this gap, per the regression test `check_tenure_change_rejects_when_locally_accepted_block_exists`, but v1 was left with the old, weaker semantics.

### Finding Description
`SortitionsView::validate_tenure_change_payload` (v1) queries only `get_last_globally_accepted_block`: [1](#0-0) 

Compare with the v2 path, which deliberately uses `get_last_signed_block` (locally OR globally accepted) to close exactly this gap: [2](#0-1) 

The v2 fix is validated by an explicit regression test documenting the prior bug: [3](#0-2) 

`get_last_signed_block` vs `get_last_globally_accepted_block` are two different SignerDb queries with materially different acceptance sets (locally accepted implies `signed_self` is set, i.e., the signer already put its signature on a block): [4](#0-3) 

Which validation path (v1 vs v2) is active is a signer-set-wide "protocol version" decision derived from gossiped `StateMachineUpdate`s and cached locally; it can legitimately be v1 during rolling upgrades or if global-state consensus hasn't been reached: [5](#0-4) [6](#0-5) 

Because v1's proposal-time duplicate check does not fire on a locally-accepted-only conflict, a second, different tenure-start block for the same tenure passes `check_proposal` and gets submitted for node validation and locally accepted. The only remaining backstop is the pre-commit-time, cross-tenure, height-based conflict check (`get_signed_conflicts`), and the docs explicitly acknowledge this dependency: [7](#0-6) 

That backstop, however, is itself time-bounded: once the first (locally-accepted, never globally-confirmed) block's signature goes stale (`tenure_last_block_proposal_timeout`), and the node has never observed that tenure advance to that height (because the first block was never pushed to the node, since it never reached global acceptance), the signer is allowed to sign the replacement: [8](#0-7) 

The net effect: a single signer can end up producing signatures over two *different* tenure-start blocks for the *same* tenure (a real equivocation), purely because the miner (re)proposed a second block after the first stalled, with v1 (the weaker, non-global-state chainstate) active for that signer.

### Impact Explanation
This breaks the "one-per-slot" equality that the signer state machine is designed to enforce (a signer must never place two conflicting signatures over blocks in the same tenure/height). Since a signature is a cryptographic commitment used to assemble the aggregate group signature that the node treats as consensus, a signer double-signing conflicting tenure-start blocks is a **Critical** finding under the given rubric ("a signer signing an invalid, non-canonical, or conflicting block"). It can be leveraged by a byzantine or simply slow/flaky miner (no majority of signers or any special access required) to obtain two valid signature sets over mutually exclusive tenure-start blocks, which can subsequently be exploited to force a chain reorg/fork once enough individual signers replicate this sequence.

### Likelihood Explanation
Requires: (1) the signer-set's negotiated `active_signer_protocol_version` to resolve to the v1 (non-global-state) path — a legitimate, reachable state during rolling upgrades or any period where a weight-majority hasn't converged on the newer protocol version, and (2) a miner proposing a first tenure-start block that gets locally accepted by some signers but stalls short of global acceptance, followed later (after `tenure_last_block_proposal_timeout`) by a second, different tenure-start proposal for the same tenure. Both conditions are ordinary operational scenarios (network hiccups, partial signer set participation, miner retries) rather than adversarial majority collusion, so likelihood is non-trivial though it depends on the v1 path still being reachable in the deployed fleet.

### Recommendation
Change `stacks-signer/src/chainstate/v1.rs`'s `validate_tenure_change_payload` to use `SignerDb::get_last_signed_block` (as v2 already does) instead of `get_last_globally_accepted_block`, so that a locally-accepted (self-signed) block in the tenure is also treated as a duplicate-block conflict at proposal time, closing the gap between the two chainstate versions and removing the dependency on the time-bounded pre-commit conflict check as the sole backstop.

### Proof of Concept
1. Signer set's negotiated protocol version resolves to v1 (`determine_active_signer_protocol_version` returns a version where `uses_global_state()` is false) — `stacks-signer/src/v0/signer.rs:783-807,865-870`.
2. Miner proposes tenure-start block A for tenure T. Signer's `check_proposal` (v1) passes because no block in T is yet `GloballyAccepted` — `stacks-signer/src/chainstate/v1.rs:505-518`. Node validates OK; signer marks A `PreCommitted` then, on reaching pre-commit threshold locally, `mark_locally_accepted` (sets `signed_self`) — `stacks-signer/src/v0/signer.rs:1467`.
3. Block A never reaches the group's 70% signature threshold (e.g., other signers are offline/slow), so it is never pushed to the node and never becomes `GloballyAccepted`.
4. After `tenure_last_block_proposal_timeout` elapses, miner proposes a different tenure-start block B for the same tenure T. `check_proposal` (v1) again checks only `get_last_globally_accepted_block(T)`, finds none, and passes — same code path as step 2.
5. At B's pre-commit threshold, `get_signed_conflicts` finds A as a same-tenure conflict, but its `last_endorsed` is now older than `freshness_cutoff`, so the fresh-conflict check is skipped; the fallback "own tenure tip" check via `get_tenure_tip` reports the tenure never reached that height (A was never pushed to the node) — `stacks-signer/src/v0/signer.rs:1423-1465`.
6. The signer proceeds to `mark_locally_accepted` and sign B, producing a second signature over a different block in the same tenure T, alongside its earlier signature over A.

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

**File:** stacks-signer/src/signerdb.rs (L1564-1585)
```rust
    /// Return the last signed block in a tenure (identified by its consensus hash).
    /// A block is considered signed if it is locally or globally accepted. Blocks that
    /// have only been pre-committed are excluded, because a pre-commit does not put a
    /// signature over the block and may be safely superseded by a competing proposal.
    ///
    /// This answers "what is the tenure's signed tip?", a different question from
    /// [`SignerDb::has_signed_block_in_tenure`]'s "does a signature bind us to this tenure?",
    /// which is why the predicates deliberately differ on rejected blocks (see there).
    pub fn get_last_signed_block(
        &self,
        tenure: &ConsensusHash,
    ) -> Result<Option<BlockInfo>, DBError> {
        let query = "SELECT block_info FROM blocks WHERE consensus_hash = ?1 AND state IN (?2, ?3) ORDER BY stacks_height DESC LIMIT 1";
        let args = params![
            tenure,
            &BlockState::GloballyAccepted.to_string(),
            &BlockState::LocallyAccepted.to_string(),
        ];
        let result: Option<String> = query_row(&self.db, query, args)?;

        try_deserialize(result)
    }
```

**File:** stacks-signer/src/v0/signer.rs (L782-807)
```rust
    /// Get the global signer protocol version
    fn determine_active_signer_protocol_version(&mut self) -> Option<SortitionStateVersion> {
        let local_version = self.get_signer_protocol_version();
        if let Ok(update) = self
            .local_state_machine
            .try_into_update_message_with_version(local_version)
        {
            self.global_state_evaluator
                .insert_update(self.stacks_address.clone(), update);
        };
        let local_state_version = SortitionStateVersion::from_protocol_version(local_version);
        self
            .global_state_evaluator
            .determine_latest_supported_signer_protocol_version().map(|version| {
                SortitionStateVersion::from_protocol_version(version)
            })
            .or_else(|| {
                // Don't default if we are in a global consensus activation state as its pointless
                if local_state_version.uses_global_state() {
                    None
                } else {
                    warn!("{self}: No consensus on signer protocol version. Defaulting to local state version: {local_version}.");
                    Some(local_state_version)
                }
            })
    }
```

**File:** stacks-signer/src/v0/signer.rs (L865-870)
```rust
        if state_version.uses_global_state() {
            self.check_block_against_global_state(stacks_client, &block_info.block)
        } else {
            self.check_block_against_local_state(stacks_client, sortition_state, &block_info.block)
        }
    }
```

**File:** stacks-signer/src/v0/signer.rs (L1423-1471)
```rust
        // No conflict is both fresh and still live. A conflict that no longer matters, i.e.
        // stale, or provably dead per `conflict_still_blocks`, cannot veto on its own. A
        // stale conflict in another tenure in particular no longer speaks for us: whether this
        // block may replace what another tenure built is settled by the chainstate checks above.
        // A stale conflict in this block's own tenure still blocks if the node already has that
        // tenure at or above the proposed height, since the proposal then duplicates state the
        // node has already built on. (The chainstate checks don't cover this for tenure-change
        // blocks: those check the parent tenure instead of their own.)
        // The permit check is deferred to here so that only same-tenure conflicts pay for it.
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
