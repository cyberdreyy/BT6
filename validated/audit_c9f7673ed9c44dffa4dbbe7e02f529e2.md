# No vulnerability found for this question.

**Analysis supporting this conclusion:**

The premise requires two `NakamotoBlock`s with different raw bytes but an *identical* `signer_signature_hash()`, where `block_lookup_by_reward_cycle` would nonetheless treat the second as "unknown." That premise contradicts how the lookup and the hash preimage are actually constructed.

`SignerDb::block_lookup` keys directly and exclusively on `signer_signature_hash` (`SELECT block_info FROM blocks WHERE signer_signature_hash = ?`), and `block_lookup_by_reward_cycle` in `stacks-signer/src/v0/signer.rs` just wraps that lookup with a reward-cycle filter. [1](#0-0) [2](#0-1)  So if two proposals genuinely hash to the same `signer_signature_hash`, the second lookup necessarily finds the first record — it lands in the "known" branch (`should_reevaluate_block`/`should_reevaluate_reject_reason`), never in the "fresh evaluation" branch. This is exactly the invariant the question's own proof idea describes, and it holds by construction of the query.

Second, `signer_signature_hash_inner` covers essentially every consensus-relevant header field — `version, chain_length, burn_spent, consensus_hash, parent_block_id, tx_merkle_root, state_index_root, timestamp, miner_signature, pox_treatment, problematic_txs` — omitting only the `signer_signature` vector itself. [3](#0-2)  Transaction content is bound in via `tx_merkle_root`, so an attacker cannot vary the block body without changing the hash. The only field excluded from the hash is `signer_signature`, which is also excluded from `block_hash()`/`block_id()` (`block_hash` reuses `signer_signature_hash_inner`). [4](#0-3)  Varying only that field produces no semantically "different" block and does not let an attacker smuggle a different consensus-relevant payload past the hash — it's the field signers *produce*, not one the proposing miner meaningfully crafts before signing.

Achieving two distinct byte-serializations with an identical `signer_signature_hash` over any of the hashed fields would require a `Sha512Trunc256Sum` (SHA-512/256) preimage/collision, which is a cryptographic-primitive break, explicitly out of scope ("secp256k1/serde/rusqlite defects with no path through the signer's logic" / "theoretical findings"). The repository's own regression test additionally confirms the field-gating behavior of the hash preimage is exact and version-gated, with no room for attacker-controlled "cosmetic" fields to diverge from what's hashed. [5](#0-4) 

Therefore the `SignerDb` key equality (`signer_signature_hash` identical ⇒ same tracked `BlockInfo`) holds before and after any attacker-controlled field variation that doesn't require breaking the hash function, and `should_reevaluate_reject_reason`'s sticky-rejection logic is not bypassable via this path. [6](#0-5) [7](#0-6)

### Citations

**File:** stacks-signer/src/signerdb.rs (L1469-1479)
```rust
    /// Fetch a block from the database using the block's
    /// `signer_signature_hash`
    pub fn block_lookup(&self, hash: &Sha512Trunc256Sum) -> Result<Option<BlockInfo>, DBError> {
        let result: Option<String> = query_row(
            &self.db,
            "SELECT block_info FROM blocks WHERE signer_signature_hash = ?",
            params![hash.to_string()],
        )?;

        try_deserialize(result)
    }
```

**File:** stacks-signer/src/v0/signer.rs (L1591-1604)
```rust
        let signer_signature_hash = block_proposal.block.header.signer_signature_hash();
        let prior_block_info = self.block_lookup_by_reward_cycle(&signer_signature_hash);
        if let Some(block_info) = &prior_block_info {
            // If we have already decided on this block, resend that decision (or ignore
            // the proposal) rather than evaluating it again.
            if !self.should_reevaluate_block(
                stacks_client,
                sortition_state,
                block_info,
                block_proposal,
            ) {
                return;
            }
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2666-2684)
```rust
    /// Helper for getting the block info from the db while accommodating for reward cycle
    pub fn block_lookup_by_reward_cycle(
        &self,
        block_hash: &Sha512Trunc256Sum,
    ) -> Option<BlockInfo> {
        let block_info = self
            .signer_db
            .block_lookup(block_hash)
            .inspect_err(|e| {
                error!("{self}: Failed to lookup block hash {block_hash} in signer db: {e:?}");
            })
            .ok()
            .flatten()?;
        if block_info.reward_cycle == self.reward_cycle {
            Some(block_info)
        } else {
            None
        }
    }
```

**File:** stacks-signer/src/v0/signer.rs (L2705-2739)
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
            RejectReason::ValidationFailed(_)
            | RejectReason::RejectedInPriorRound
            | RejectReason::SortitionViewMismatch
            | RejectReason::ReorgNotAllowed
            | RejectReason::InvalidBitvec
            | RejectReason::PubkeyHashMismatch
            | RejectReason::InvalidMiner
            | RejectReason::NotLatestSortitionWinner
            | RejectReason::InvalidParentBlock
            | RejectReason::DuplicateBlockFound
            | RejectReason::IrrecoverablePubkeyHash
            | RejectReason::ProblematicTransactions
            | RejectReason::ProposalTooOld => {
                // No need to re-validate these types of rejections.
                false
            }
        }
    } else {
        false
    }
}
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1026-1045)
```rust
    /// Inner calculation of the message digest for stackers to sign.
    /// This includes all fields _except_ the stacker signature.
    fn signer_signature_hash_inner(&self) -> Result<Sha512Trunc256Sum, CodecError> {
        let mut hasher = Sha512_256::new();
        let fd = &mut hasher;
        write_next(fd, &self.version)?;
        write_next(fd, &self.chain_length)?;
        write_next(fd, &self.burn_spent)?;
        write_next(fd, &self.consensus_hash)?;
        write_next(fd, &self.parent_block_id)?;
        write_next(fd, &self.tx_merkle_root)?;
        write_next(fd, &self.state_index_root)?;
        write_next(fd, &self.timestamp)?;
        write_next(fd, &self.miner_signature)?;
        write_next(fd, &self.pox_treatment)?;
        if Self::version_includes_problematic_txs(self.version) {
            write_next(fd, &self.problematic_txs)?;
        }
        Ok(Sha512Trunc256Sum::from_hasher(hasher))
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1058-1069)
```rust
    pub fn block_hash(&self) -> BlockHeaderHash {
        // same as sighash -- we don't commit to signatures
        BlockHeaderHash(
            self.signer_signature_hash_inner()
                .expect("BUG: failed to serialize block header hash struct")
                .0,
        )
    }

    pub fn block_id(&self) -> StacksBlockId {
        StacksBlockId::new(&self.consensus_hash, &self.block_hash())
    }
```

**File:** stackslib/src/chainstate/nakamoto/tests/mod.rs (L3596-3629)
```rust
    /// Regression guard for the Epoch-4.0 header format change. A version-0
    /// (pre-4.0) header must serialize and hash exactly as it did before the
    /// `problematic_txs` field existed: the field is gated on the header's
    /// `version` byte, so version-0 headers omit it entirely. If this ever
    /// regresses, the block_hash of every historical Nakamoto block changes and
    /// the node forks off mainnet.
    #[test]
    fn problematic_txs_serialization_is_version_gated() {
        let block = make_block(2);
        let marker = ProblematicTxMarker {
            tx_index: 2,
            category: 1,
        };

        // --- version 0: the field is invisible to serialization and hashing ---
        let mut v0 = block.header.clone();
        v0.version = 0;
        let mut v0_with_markers = v0.clone();
        v0_with_markers.problematic_txs = vec![marker];

        // Serialized bytes do not depend on `problematic_txs`...
        let v0_bytes = v0.serialize_to_vec();
        assert_eq!(
            v0_bytes,
            v0_with_markers.serialize_to_vec(),
            "version-0 header serialization must not depend on problematic_txs"
        );
        // ...nor do any of the signature / block hashes.
        assert_eq!(v0.block_hash(), v0_with_markers.block_hash());
        assert_eq!(
            v0.signer_signature_hash(),
            v0_with_markers.signer_signature_hash()
        );
        assert_eq!(
```
