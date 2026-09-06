### Title
v1 `validate_tenure_change_payload` allows two signed tenure-change blocks per tenure via `get_last_globally_accepted_block` blind spot - ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
The v1 chainstate's `validate_tenure_change_payload` checks for a duplicate tenure-change block using `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)`, which only returns blocks in `BlockState::GloballyAccepted`. A block the signer has already locally signed (`LocallyAccepted`/signed_self) but that has not yet crossed the group threshold is invisible to this check, so a second, competing tenure-change block B2 for the same tenure passes `check_proposal` and can be signed.

### Finding Description
`SortitionsView::validate_tenure_change_payload` (v1) ends with: [1](#0-0) 
This queries only `GloballyAccepted` state: [2](#0-1) 

Compare with v2's equivalent, which was already fixed to close exactly this gap by using `get_last_signed_block` (covers both locally and globally accepted/signed blocks): [3](#0-2) 

The project's own documentation and regression test explicitly acknowledge the v1/v2 divergence and the exact bug class: [4](#0-3) [5](#0-4) 

Exploit flow: an attacker with a single miner slot proposes B1 (tenure-change, cause=BlockFound) for tenure T. The signer locally signs B1 (`mark_locally_accepted`), moving it to `LocallyAccepted`, but the group threshold is not reached (no majority collusion required — just insufficient aggregate weight yet, a normal network-timing condition). The attacker then proposes B2, a second tenure-change block for the same tenure T (e.g., a different set of transactions/miner key). `check_proposal(B2)` calls `validate_tenure_change_payload`, which calls `get_last_globally_accepted_block(T)` — this returns `None` because B1 is only `LocallyAccepted`, not `GloballyAccepted`. The `DuplicateBlockFound` check is therefore skipped, and B2 proceeds through the rest of validation and can be signed by the same signer, producing two distinct signer-signed tenure-change blocks (B1 and B2) for tenure T. This breaks the uniqueness invariant "at most one block signed per tenure" for tenure-change blocks under v1.

Existing guards do not close this: `check_tenure_change_confirms_parent` and `check_parent_tenure_choice` validate the *parent* tenure relationship, not same-tenure duplication; the `DuplicateBlockFound` check is the only same-tenure duplicate guard and it is proposal-time-only (never re-run at validate-ok or signing time per the documented flow), so once it is bypassed there is no second net.

### Impact Explanation
This breaks block uniqueness/safety: a single signer can be induced to sign two conflicting tenure-change blocks for the same tenure. If enough signers hit the same timing window (each locally signs B1 without having gathered global threshold, and each independently sees B2 pass the duplicate check), this can contribute to two conflicting blocks accumulating signatures for the same tenure — a Critical chain-safety issue (conflicting blocks signed at the same tenure/height). This matches the Critical severity bucket: "a signer signing an invalid, non-canonical, or conflicting block."

### Likelihood Explanation
Preconditions: v1 protocol version active (`SortitionStateVersion::V1`, i.e., signer protocol version below `GLOBAL_SIGNER_STATE_ACTIVATION_VERSION`) [6](#0-5) ; a window in which a signer has locally accepted/signed B1 but the tenure has not yet crossed the global (aggregate) signature threshold — a routine timing window during normal signature aggregation, not a rare edge case. The attacker needs only one miner slot (to craft two competing tenure-change block proposals) and standard gossip of BlockProposals; no majority of signers, no privileged role, and no auth_token are required. Repeatable in every tenure where global aggregation lags behind an individual signer's local signature.

### Recommendation
Change v1's `validate_tenure_change_payload` to use `signer_db.get_last_signed_block(&block.header.consensus_hash)` (or equivalent that covers `LocallyAccepted` and `GloballyAccepted`/signed states) instead of `get_last_globally_accepted_block`, mirroring the fix already applied in v2's `validate_tenure_change_payload`.

### Proof of Concept
Add to `stacks-signer/src/chainstate/tests/v1.rs`, mirroring the existing v2 regression test `check_tenure_change_rejects_when_locally_accepted_block_exists` (`stacks-signer/src/chainstate/tests/v2.rs:755-850`):
1. Set up the v1 `SortitionsView` test environment (`setup_test_environment`).
2. Insert into `signer_db` a `BlockInfo` for a tenure-change block B1 with `consensus_hash = cur_sortition.data.consensus_hash`, calling `mark_locally_accepted(false)` (not global) so it lands in `LocallyAccepted` state only.
3. Build a second tenure-change block B2 with the same `consensus_hash`/tenure but different content, sign the miner header.
4. Call `sortitions_view.check_proposal(&stacks_client, &mut signer_db, &b2)` (v1 signature).
5. Assert `matches!(result, Err(RejectReason::DuplicateBlockFound))`.
   - Under current v1 code, this assertion **fails** (returns `Ok(())`) because `get_last_globally_accepted_block` misses the `LocallyAccepted` B1, proving the vulnerability.
   - After applying the recommended fix (swap to `get_last_signed_block`), the assertion **passes**, matching v2's already-fixed behavior.

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

**File:** stacks-signer/src/chainstate/tests/v2.rs (L748-754)
```rust
/// Test that a tenure change proposal is rejected when a locally-accepted
/// (but not globally-accepted) block already exists in the same tenure.
///
/// This is a regression test: previously, the check used
/// `get_last_globally_accepted_block`, which would miss blocks in
/// `LocallyAccepted` or `PreCommitted` state and incorrectly allow
/// a duplicate tenure change.
```

**File:** stacks-signer/src/chainstate/mod.rs (L532-540)
```rust
impl SortitionStateVersion {
    /// Convert the protocol version to a sortition state version
    pub fn from_protocol_version(version: u64) -> Self {
        if version < GLOBAL_SIGNER_STATE_ACTIVATION_VERSION {
            Self::V1
        } else {
            Self::V2
        }
    }
```
