### Title
`validate_tenure_change_payload` duplicate-block guard is protocol-version inconsistent - v1 signers miss a locally-accepted (not-yet-global) tenure-start block ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
The v1 chainstate's `validate_tenure_change_payload` rejects a duplicate tenure-start proposal only when a prior block in the same tenure is `GloballyAccepted`, using `signer_db.get_last_globally_accepted_block`, whereas v2's equivalent check uses `get_last_signed_block`, which covers both `LocallyAccepted` and `GloballyAccepted` states. This means a v1-configured signer that has already locally accepted (self-signed) a tenure-start block, but has not yet observed enough peers sign it to mark it globally accepted, will not raise `DuplicateBlockFound` against a second, competing tenure-change block for the same tenure.

### Finding Description
The broken equality: `last_in_current_tenure` as computed by v1's `validate_tenure_change_payload` (`signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)`, [1](#0-0) ) diverges from v2's equivalent (`signer_db.get_last_signed_block(&block.header.consensus_hash)`, [2](#0-1) ). `get_last_signed_block` explicitly includes `LocallyAccepted` and `GloballyAccepted` states, excluding only `PreCommitted`, while `get_last_globally_accepted_block` only matches `GloballyAccepted` [3](#0-2) [4](#0-3) .

Exploit flow: the attacker, holding the winning miner slot for tenure T, proposes a first tenure-start block B1 for T; the victim v1 signer locally accepts (signs) B1 but has not yet seen the group threshold to mark it globally accepted (e.g., other signers are slow, network delay, or the attacker withholds gossiping the aggregate). The attacker then crafts and gossips a second, different tenure-change block B2 also targeting tenure T (same `tenure_consensus_hash`/`prev_tenure_consensus_hash`, satisfying `check_tenure_change_confirms_parent`/`check_parent_tenure_choice`). Because `get_last_globally_accepted_block` returns `None` (B1 is only `LocallyAccepted`), the v1 `DuplicateBlockFound` guard is bypassed, and `validate_tenure_change_payload` returns `Ok(())` for B2 in a tenure the signer already locally signed.

This gap exists specifically because the tenure-change path never re-runs the general same-tenure conflict check `confirms_latest_block_in_same_tenure`/`check_latest_block_in_tenure` — that check only executes in the `else` branch for non-tenure-change blocks [5](#0-4) ; tenure-change blocks rely solely on the dedicated `last_in_current_tenure` lookup inside `validate_tenure_change_payload`. This asymmetry is explicitly documented as an intentional difference between the two chainstate versions in the design docs, not flagged there as a defect [6](#0-5) .

### Impact Explanation
If this proposal-time gap is not caught by any later backstop before the signer actually emits its signature over B2, a v1 signer could produce two valid signatures over two distinct tenure-start blocks (B1 and B2) in the same tenure T — a direct chain-safety violation (conflicting blocks signable at the same tenure/height), matching the Critical category ("a signer signing an invalid, non-canonical, or conflicting block"). This is repeatable across tenures wherever the attacker wins the miner slot and can create timing where the first block is only locally, not globally, accepted by the target v1 signer.

### Likelihood Explanation
Preconditions: the target signer must be running on the legacy v1 chainstate path (pre `GLOBAL_SIGNER_STATE_ACTIVATION_VERSION = 2`, i.e., negotiated `active_signer_protocol_version < 2` [7](#0-6) ), and the attacker's first tenure-start block must reach `LocallyAccepted` on that signer without yet reaching `GloballyAccepted` — an ordinary, easily-arrangeable timing window under normal network latency or by an attacker delaying propagation of aggregate acceptance. The attacker needs only their own miner slot plus the ability to gossip a second BlockProposal, matching the assumed unprivileged capability. However, I could not fully verify within the available context whether a downstream, version-independent backstop (e.g. a `get_signed_conflicts`-based check invoked immediately before the signer emits its signature, referenced in `docs/signer-flows.md` as covering "the same ground" for non-re-validated blocks) intercepts this specific tenure-change duplicate case before signing. That check exists for general same-tenure/height conflicts but its exact invocation site relative to the tenure-change signing path was not located in this investigation, so the ultimate exploitability (whether a signature over B2 is actually produced, versus merely accepted at the chainstate-validation layer) is not fully confirmed.

### Recommendation
Change v1's `validate_tenure_change_payload` to use `signer_db.get_last_signed_block` instead of `signer_db.get_last_globally_accepted_block` (mirroring v2's semantics), so that a locally-accepted tenure-start block in the current tenure is treated as a duplicate and blocks a competing tenure-change proposal, consistent with the v2 behavior and with the rationale already documented for `get_last_signed_block`.

### Proof of Concept
Rust test in `stacks-signer/src/chainstate/tests/v1.rs`:
1. Set up a v1 `SortitionsView` and `SignerDb` as in existing v1 tests (`setup_test_environment`).
2. Build block `B1` with a `TenureChangePayload` for tenure T (`cause: BlockFound`), insert it into `signer_db` and call `block_info.mark_locally_accepted(false)` (not `mark_globally_accepted`), then `signer_db.insert_block(&block_info_1)`.
3. Build a second, distinct block `B2` also carrying a `TenureChangePayload` targeting the same `tenure_consensus_hash`/`prev_tenure_consensus_hash` as B1, satisfying `check_tenure_change_confirms_parent`.
4. Call `sortitions_view.validate_tenure_change_payload(...)` (or `check_proposal`) with `B2`.
5. Assert `result == Err(RejectReason::DuplicateBlockFound)` is expected to hold (mirroring v2's existing test `check_tenure_change_accepts_when_only_pre_committed_block_exists`'s pattern) — and show it currently fails because `signer_db.get_last_globally_accepted_block(&T)` returns `None` even though `get_last_signed_block(&T)` would return `Some(B1)`, per the `get_accepted_blocks` unit test's own confirmation of this divergence [8](#0-7) .

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L319-339)
```rust
        if let Some(tenure_change) = block.get_tenure_change_tx_payload() {
            self.validate_tenure_change_payload(
                &proposed_by,
                tenure_change,
                block,
                signer_db,
                client,
            )?;
        } else {
            // check if the new block confirms the last block in the current tenure
            let confirms_latest_in_tenure = SortitionData::confirms_latest_block_in_same_tenure(
                block,
                signer_db,
                client,
                &self.config,
            )
            .map_err(SignerChainstateError::from)?;
            if !confirms_latest_in_tenure {
                return Err(RejectReason::InvalidParentBlock);
            }
        }
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

**File:** stacks-signer/src/signerdb.rs (L3560-3569)
```rust
        let block_info = db
            .get_last_signed_block(&consensus_hash_1)
            .unwrap()
            .unwrap();
        assert_eq!(block_info, block_info_3);
        let block_info = db
            .get_last_globally_accepted_block(&consensus_hash_1)
            .unwrap()
            .unwrap();
        assert_eq!(block_info, block_info_1);
```

**File:** docs/signer-flows.md (L425-434)
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

**File:** stacks-signer/src/v0/signer_state.rs (L50-53)
```rust
/// This is the latest supported protocol version for this signer binary
pub static SUPPORTED_SIGNER_PROTOCOL_VERSION: u64 = 2;
/// The version at which global signer state activates
pub static GLOBAL_SIGNER_STATE_ACTIVATION_VERSION: u64 = 2;
```
