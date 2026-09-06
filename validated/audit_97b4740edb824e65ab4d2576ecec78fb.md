Confirmed: `stacks-signer/src/chainstate/v1.rs::validate_tenure_change_payload` (lines 505-518) still checks only `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)` to detect a duplicate tenure-start block, whereas the v2 path (`stacks-signer/src/chainstate/v2.rs::validate_tenure_change_payload`, lines 344-357) was fixed to use `get_last_signed_block`, which additionally covers `LocallyAccepted` blocks. This is documented as an explicit regression fix in v2 (test `check_tenure_change_rejects_when_locally_accepted_block_exists` in `stacks-signer/src/chainstate/tests/v2.rs`), whose comment states: "Before the fix, this would have incorrectly passed because `get_last_globally_accepted_block` would not find the locally-accepted block."

### Title
Duplicate-tenure-start check in v1 signer chainstate only queries globally-accepted blocks, missing locally-accepted ones - (File: stacks-signer/src/chainstate/v1.rs)

### Summary
`SortitionsView::validate_tenure_change_payload` in the v1 chainstate module checks for an existing block in the current tenure using `signer_db.get_last_globally_accepted_block`, a single, narrower data source. It does not also check for blocks that reached only `LocallyAccepted` state (i.e., signed by this signer as part of a 70%-weight local quorum but not yet confirmed by the node). This mirrors the `UniswapV2TokenAdapter.supports` bug class: checking one liquidity/data source and returning early without checking the second, equally valid source, producing a false negative that lets an attacker-controlled path slip through.

### Finding Description
`validate_tenure_change_payload` (v1) is invoked from `check_proposal` (`stacks-signer/src/chainstate/v1.rs`) whenever a miner's block proposal contains a `TenureChangePayload`. Its purpose is to reject a *second*, competing tenure-start proposal for a tenure the signer has already committed a signature to, via: [1](#0-0) 

`get_last_globally_accepted_block` only returns blocks in `BlockState::GloballyAccepted` [2](#0-1) . It does **not** return a block that is `LocallyAccepted` — i.e., one this very signer has already added its own signature to as part of reaching the local signing quorum (70% weight), but which the stacks-node has not yet processed and confirmed as globally accepted.

By contrast, `get_last_signed_block` is documented as: "A block is considered signed if it is locally or globally accepted... This answers 'what is the tenure's signed tip?'" [3](#0-2) , and the v2 equivalent of this exact check was already fixed to use it: [4](#0-3) 

The v2 fix is backed by an explicit regression test documenting the exact failure mode: [5](#0-4) 

The v1 code path (still reachable, since protocol version is negotiated per-signer and tests explicitly pin signers to v1, e.g. `TEST_PIN_SUPPORTED_SIGNER_PROTOCOL_VERSION`) retains the old, narrower check: [6](#0-5) 

### Impact Explanation
A miner (a single one-slot block-producer, no majority of signers required) can exploit this on any signer still running protocol v1: propose tenure-start block A, get it signed and locally accepted by the v1 signer (crossing the local 70%-weight threshold and thus obtaining that signer's signature) but never surface it to the node/get it globally accepted (e.g., by not gathering enough other votes, or by withholding broadcast). The miner then proposes a *second*, different tenure-start block B for the same tenure (different transactions/coinbase — a conflicting block at the same height). Because `validate_tenure_change_payload` only checks `get_last_globally_accepted_block`, it finds nothing for tenure A and does not raise `DuplicateBlockFound`; the check passes and the v1 signer will go on to sign block B as well. The signer now holds two valid signatures over two different, conflicting blocks for the same tenure/height — a signer signing a conflicting block, matching the Critical impact category ("a signer signing an invalid, non-canonical, or conflicting block").

### Likelihood Explanation
This requires no majority of signers or extra secrets — a single miner controlling block proposals for a v1-pinned signer can produce this sequence deterministically, matching the exact "locally accepted, not globally accepted" precondition already reproduced and regression-tested for the v2 code path. Any deployment (or partial rollout / mixed-version fleet) where a signer is still negotiated to protocol v1 remains exposed, since the fix was applied only to `chainstate/v2.rs` and not backported to `chainstate/v1.rs`.

### Recommendation
Update `stacks-signer/src/chainstate/v1.rs::validate_tenure_change_payload` to use `signer_db.get_last_signed_block(&block.header.consensus_hash)` instead of `get_last_globally_accepted_block`, matching the v2 fix, so both `LocallyAccepted` and `GloballyAccepted` blocks in the tenure are treated as conflicts with a new tenure-start proposal.

### Proof of Concept
1. Miner proposes tenure-start block A for consensus hash `CH`.
2. v1-pinned signer processes the proposal, block A reaches `LocallyAccepted` in `signer_db` (signed by ≥70% weight of signers, including this one) but the node never adopts it as canonical (e.g., miner withholds final broadcast or a network partition delays propagation).
3. Miner proposes tenure-start block B (different txs) also for `CH`.
4. v1 signer's `check_proposal` → `validate_tenure_change_payload` calls `signer_db.get_last_globally_accepted_block(CH)`, which returns `None` because A is only `LocallyAccepted`.
5. No `DuplicateBlockFound` rejection is raised; the signer proceeds to validate and sign block B.
6. The signer now has valid signatures over two conflicting tenure-start blocks (A and B) at the same tenure/height.

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L483-519)
```rust
        // Ensure that the tenure change block confirms the expected parent block
        let confirms_expected_parent = SortitionData::check_tenure_change_confirms_parent(
            tenure_change,
            block,
            signer_db,
            client,
            self.config.tenure_last_block_proposal_timeout,
            self.config.reorg_attempts_activity_timeout,
        )
        .map_err(SignerChainstateError::from)?;
        if !confirms_expected_parent {
            return Err(RejectReason::InvalidParentBlock);
        }
        // now, we have to check if the parent tenure was a valid choice.
        let is_valid_parent_tenure = proposed_by.state().data.check_parent_tenure_choice(
            signer_db,
            client,
            &self.config.first_proposal_burn_block_timing,
        )?;
        if !is_valid_parent_tenure {
            return Err(RejectReason::ReorgNotAllowed);
        }
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
        Ok(())
```

**File:** stacks-signer/src/signerdb.rs (L1543-1562)
```rust
    /// Return the last accepted block in a tenure (identified by its consensus hash).
    ///
    /// Note: this includes blocks that were only pre-committed. A pre-commit does not put a
    /// signature over the block, so this must NOT be used to determine the tenure's tip for
    /// validation purposes -- use [`SignerDb::get_last_signed_block`] for that.
    pub fn get_last_accepted_block(
        &self,
        tenure: &ConsensusHash,
    ) -> Result<Option<BlockInfo>, DBError> {
        let query = "SELECT block_info FROM blocks WHERE consensus_hash = ?1 AND state IN (?2, ?3, ?4) ORDER BY stacks_height DESC LIMIT 1";
        let args = params![
            tenure,
            &BlockState::GloballyAccepted.to_string(),
            &BlockState::LocallyAccepted.to_string(),
            &BlockState::PreCommitted.to_string(),
        ];
        let result: Option<String> = query_row(&self.db, query, args)?;

        try_deserialize(result)
    }
```

**File:** stacks-signer/src/signerdb.rs (L1564-1572)
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

**File:** stacks-signer/src/chainstate/tests/v2.rs (L843-850)
```rust
    // The proposal should be rejected because there's already a locally-accepted
    // block in this tenure. Before the fix, this would have incorrectly passed
    // because get_last_globally_accepted_block would not find the locally-accepted block.
    assert!(
        matches!(result, Err(RejectReason::DuplicateBlockFound)),
        "Expected DuplicateBlockFound rejection when a locally-accepted block exists in the tenure, got: {result:?}"
    );
}
```
