## Title
V1 signer chainstate `DuplicateBlockFound` check uses `get_last_globally_accepted_block` instead of `get_last_signed_block`, widening the double-tenure-start-block window - (File: `stacks-signer/src/chainstate/v1.rs`)

### Summary
The v1 signer chainstate's tenure-change duplicate check (`validate_tenure_change_payload` in `stacks-signer/src/chainstate/v1.rs`) only rejects a second tenure-start proposal for a tenure once a prior block in that tenure has been **globally accepted**. The v2 chainstate performs the equivalent check with `get_last_signed_block`, which also catches a block that is merely **locally accepted** (i.e. already signed by this signer, but not yet globally accepted). This asymmetry is confirmed by the codebase's own regression test comment describing exactly this class of bug as having been fixed for v2 but the fix was not applied to v1.

### Finding Description
In `stacks-signer/src/chainstate/v1.rs`: [1](#0-0) 

```
let last_in_current_tenure = signer_db
    .get_last_globally_accepted_block(&block.header.consensus_hash)
    ...
if let Some(last_in_current_tenure) = last_in_current_tenure {
    ...
    return Err(RejectReason::DuplicateBlockFound);
}
```

Compare with the v2 equivalent, which the project's own tests treat as the corrected behavior: [2](#0-1) 

The v2 code comment and the regression test explicitly document that using `get_last_globally_accepted_block` here is a known-bad pattern: [3](#0-2) 

`get_last_globally_accepted_block` only returns a tenure's tip once a full 70% signature threshold has been observed by *this* signer and the block reached `GloballyAccepted`. It misses a block that this same signer has already **locally accepted and signed** (`BlockState::LocallyAccepted`, `signed_self` set) — as documented in `signerdb.rs`: [4](#0-3) 

This matters because `DuplicateBlockFound` from `validate_tenure_change_payload` is checked **only at proposal arrival**, and is never re-run afterward, per the documented design: [5](#0-4) 

The only later backstop against signing two conflicting blocks at the same height is the pre-commit-threshold "signed conflicts" check in `handle_block_pre_commit`, which explicitly only considers blocks that already carry a signature (`signed_self`/`signed_group`) — a merely `PreCommitted` block is invisible to it: [6](#0-5) [7](#0-6) 

Putting these together for a v1-protocol signer: a miner (a single slot) can propose two competing tenure-start blocks A and B for the same tenure. Because `get_last_globally_accepted_block` returns `None` until a block is fully globally accepted, **both** A and B pass `validate_tenure_change_payload`'s `DuplicateBlockFound` check as long as neither has yet reached global acceptance — this is true even after A has already been *locally accepted and signed* by this very signer. Both A and B are independently validated by the node and pre-committed by the signer set. Whichever of A/B first crosses the 70% pre-commit weight threshold gets signed (`get_signed_conflicts` finds no conflict yet, since the other candidate carries no signature at that point). If network/gossip timing lets disjoint ≥70%-weight signer subsets converge on A and B separately before either signature set is broadcast and observed by the other subset, the signer set as a whole can produce two independently and validly signed conflicting tenure-start blocks at the same height — breaking the "one signed block per tenure/height" equality that `get_signed_conflicts`/the pre-commit round is designed to guarantee.

### Impact Explanation
This falls under "Critical - a signer signing an invalid, non-canonical, or conflicting block." The v1 signer's proposal-time defense against a duplicate tenure-start block is weaker than intended (and weaker than the already-fixed v2 defense for the identical scenario), widening the timing window in which two conflicting tenure-start proposals for the same tenure can simultaneously be alive in the pre-commit pipeline. The only remaining backstop (`get_signed_conflicts`) is signature-based and blind to `PreCommitted`-only state, so it cannot prevent the race described above; it can only stop a signer from later re-signing on top of an already-signed conflicting block.

### Likelihood Explanation
Reachable by a single miner slot alone (no majority-signer collusion or key compromise needed): the miner simply proposes two tenure-start block variants for the same tenure before either is globally accepted. Success of the resulting equivocation additionally depends on realistic signer-network gossip delay causing two different ≥70%-weight subsets to complete their pre-commit rounds on A and B before cross-observing each other's signatures — a timing-dependent but not implausible condition given the documented latency-sensitive design of the pre-commit round.

### Recommendation
Change `stacks-signer/src/chainstate/v1.rs`'s `validate_tenure_change_payload` to use `SignerDb::get_last_signed_block` (as v2 already does) instead of `get_last_globally_accepted_block`, so that a tenure-start proposal is rejected as `DuplicateBlockFound` as soon as this signer has locally accepted (signed) any other block in that tenure, closing the widened race window for v1-protocol signers.

### Proof of Concept
1. Signer runs under the v1 (pre-`GLOBAL_SIGNER_STATE_ACTIVATION_VERSION`) protocol.
2. Miner proposes tenure-start block A for tenure T. All signers validate and pre-commit; suppose weight crosses 70% quickly and this signer signs A (`LocallyAccepted`, `signed_self` set) before it is globally accepted.
3. Before A is globally accepted, the miner (or a relay) proposes a second tenure-start block B for the same tenure T (different tx ordering/parent choice within the same tenure).
4. `validate_tenure_change_payload` (`stacks-signer/src/chainstate/v1.rs:505-518`) calls `get_last_globally_accepted_block(T)`, which returns `None` (A is only `LocallyAccepted`), so B passes the `DuplicateBlockFound` check and is pre-committed by the signer set.
5. A disjoint ≥70%-weight subset of signers (who have not yet observed A's signature via gossip) crosses B's pre-commit threshold; at that point `get_signed_conflicts` for B finds no conflict (A carries a signature only at the signers who already processed it, and cross-signer signature propagation lags), so this subset signs B.
6. The network now has two independently and validly signer-authenticated blocks (A and B) at the same tenure height — a conflicting-block safety violation.

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

**File:** stacks-signer/src/chainstate/tests/v2.rs (L748-756)
```rust
/// Test that a tenure change proposal is rejected when a locally-accepted
/// (but not globally-accepted) block already exists in the same tenure.
///
/// This is a regression test: previously, the check used
/// `get_last_globally_accepted_block`, which would miss blocks in
/// `LocallyAccepted` or `PreCommitted` state and incorrectly allow
/// a duplicate tenure change.
#[test]
fn check_tenure_change_rejects_when_locally_accepted_block_exists() {
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

**File:** stacks-signer/src/signerdb.rs (L1587-1601)
```rust
    /// Return every signed block at or above the given Stacks height, in ANY tenure, excluding
    /// the block with the given signer signature hash, ordered by height (highest first). A
    /// block is considered signed if a signature was ever put over it, ours (`signed_self`)
    /// or the observed group's (`signed_group`). Blocks that were only pre-committed carry no
    /// signature and are never returned. Each row carries the most recent endorsement time
    /// (`signed_self`/`signed_group`, whichever is later) so the caller can judge freshness per
    /// conflict.
    ///
    /// The search deliberately spans all tenures: two blocks at the same height are siblings
    /// no matter which tenure they belong to (e.g. a tenure-start block conflicts with the
    /// previous tenure's block at the same height), so a signature over either may conflict
    /// with a fresh signature over the other.
    ///
    /// Blocks in tenures whose reorg we sanctioned under the reorg-timing rules (see
    /// [`SignerDb::mark_tenure_superseded`]) are still returned, but annotated with the
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

**File:** stacks-signer/src/v0/signer.rs (L1316-1338)
```rust
        if block_info.signed_self.is_some() {
            debug!(
                "{self}: Received pre-commit for a block that we have already signed. Doing nothing...",
            );
            return;
        }

        if !block_info.valid.unwrap_or(false) {
            // We received a pre-commit for a block that we have not validated or we have already marked this block as invalid.
            // We should not do anything further as we do not know what our response should be and we do not change our votes on rejected
            // blocks unless we receive a new block proposal for it and the reject reason allows us to reconsider.
            debug!(
                "{self}: Received a pre-commit for a block that we have not determined to be valid: {:?}. Doing nothing...", block_info.valid
            );
            return;
        }

        if min_weight > commit_weight {
            debug!(
                "{self}: Not enough pre-committed to block {block_hash} (have {commit_weight}, need at least {min_weight}/{total_weight})"
            );
            return;
        }
```
