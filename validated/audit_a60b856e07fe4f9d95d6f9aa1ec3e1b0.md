[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** stacks-signer/src/signerdb.rs (L1496-1516)
```rust
    /// Return whether this signer has signed a block, or observed the signer set sign a block,
    /// in a tenure (identified by its consensus hash). Used by `is_timed_out` to keep a tenure
    /// we are committed to from being timed out.
    ///
    /// Unlike [`SignerDb::has_approved_block_in_tenure`] this excludes blocks that were only
    /// pre-committed. A pre-commit does not put a signature over the block, so it does not
    /// represent a commitment that would be violated by abandoning the tenure.
    ///
    /// Rejection, even global rejection, does NOT clear the commitment. A rejection is a
    /// revocable opinion; a signature is a bearer instrument. Once ours is public, anyone can
    /// aggregate it toward the 70% threshold should enough rejecting signers change their
    /// minds, so a block we signed binds us to its tenure no matter what state it later fell
    /// to. This is deliberately a different predicate from
    /// [`SignerDb::get_last_signed_block`], which answers a tip question rather than a
    /// commitment question (see there).
    pub fn has_signed_block_in_tenure(&self, tenure: &ConsensusHash) -> Result<bool, DBError> {
        let query = "SELECT 1 FROM blocks WHERE consensus_hash = ? AND (signed_self IS NOT NULL OR signed_group IS NOT NULL) LIMIT 1;";
        let result: Option<u64> = query_row(&self.db, query, [tenure])?;

        Ok(result.is_some())
    }
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
