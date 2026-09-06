### Title
A single equivocating miner can evict a peer signer's early pre-commit/signature/rejection vote for the real block from the 3-entry pending-response cache, causing a liveness wedge on threshold aggregation - (File: `stacks-signer/src/signerdb.rs`)

### Summary
`SignerDb` caches "early" votes (pre-commits, signatures, rejections) that arrive for a block the local signer has not yet tracked, in three SQLite tables, each capped at 3 entries **per remote signer address** via an eviction trigger. When the real proposal later arrives, `drain_pending_block_responses` replays whatever is still cached for that exact block hash. Because the cap is keyed only by `signer_addr` and evicts by recency regardless of which block hash the entries belong to, a single miner who equivocates with more than 3 distinct block proposals for the same height in a short window can cause a peer's early, legitimate vote about the *real* block to be silently evicted before the real proposal is processed — exactly analogous to the reported auction bug where a party spamming many new entries in a capped, most-recent-first structure wipes out earlier legitimate entries.

### Finding Description
Pending votes for an as-yet-unseen block are stored per remote signer address, capped at 3, with oldest-first eviction on every insert: [1](#0-0) 

The insert paths that populate these tables are reached whenever this signer receives another signer's pre-commit, acceptance, or rejection for a block hash it has not yet tracked in its `blocks` table: [2](#0-1) [3](#0-2) 

and analogous logic for pre-commits in `handle_block_pre_commit`: [4](#0-3) 

Once the local signer finally processes the real proposal, it drains and replays whatever is still present under that exact hash: [5](#0-4) [6](#0-5) 

The eviction trigger's `WHERE signer_addr = NEW.signer_addr` clause and `ORDER BY received_time DESC ... OFFSET 3` scope is *global per signer address*, not per block hash. So if a single miner (one sortition slot) proposes more than 3 distinct blocks for the same height in quick succession — a capability entirely within a miner's normal permissions and exactly the scenario used in the source report (one actor submitting a burst of entries) — then any signer S who is momentarily behind on ingesting the real proposal (normal network jitter is enough) will have the real block's early vote from peer signer X evicted by X's votes on the 3+ junk/equivocating proposals, before S ever calls `drain_pending_block_responses` for the real hash. The vote is deleted from SQL (`DELETE ... RETURNING`) with no fallback: peers do not re-transmit a pre-commit/signature/rejection once sent, so the vote is permanently lost from S's perspective.

This breaks the equality "aggregated weight (as tallied by signer S) == the set of votes actually cast by the signer set" — S's local tally for the real block will be silently missing votes it should have counted, exactly as `_settleAuction`'s hard 200-count in the report silently drops legitimate earlier bids in favor of the newest 200.

### Impact Explanation
This is a liveness issue on the affected signer: its locally observed pre-commit/signature weight for the real, valid, canonical block can be permanently short of what peers actually cast, because the record of those cast votes was silently evicted by a burst of unrelated proposals from a single miner. If enough peer votes are lost this way, signer S may never reach the 70% pre-commit or signature threshold from its own perspective and thus never signs (or signs unnecessarily late) — matching the "signer wedged into never signing valid blocks" High-impact category. This requires no cooperation from other signers and no majority — only a single miner producing several conflicting proposals within the propagation-delay window, which is squarely a one-slot miner + gossip capability.

### Likelihood Explanation
The trigger condition (a miner equivocating with >3 distinct proposals for one height, and a target signer lagging slightly behind in proposal ingestion relative to the vote broadcasts of others) is plausible under ordinary network jitter and is trivial for a miner to manufacture deliberately (submit 4+ block variants for the same height back-to-back). The 3-entry cap is small and was explicitly documented as bounding "untracked blocks per signer address" (see `stacks-signer/CHANGELOG.md`), making it easy to exceed with a short burst.

### Recommendation
Scope the eviction to be per `(signer_addr, block_signer_signature_hash)` pair combined with some upper bound on distinct *tracked-pending* block hashes per height/tenure rather than a flat, hash-agnostic recency window per signer address; alternatively, raise/adjust the cap dynamically or bound it by tenure/height rather than pure insertion order, so that an unrelated burst of proposals for other block hashes cannot evict a still-relevant pending vote for a legitimately in-flight block. At minimum, log/alert when eviction occurs so operators can detect the condition, and consider re-requesting/reconciling votes for a newly tracked block from peers rather than relying solely on a possibly-evicted local cache.

### Proof of Concept
1. Miner (one slot) at height H rapidly proposes 4 distinct blocks B0, B1, B2, B3 (only B0 being the block it intends to finalize; B1–B3 can be minimally-different junk/equivocating variants, e.g. differing timestamp) before B0's proposal message reaches signer S (simulating normal propagation delay/jitter).
2. Peer signer X quickly evaluates each proposal as it arrives (via its own copy of the gossip) and sends a pre-commit or rejection for B0 immediately, followed by rejections for B1, B2, B3 once those are seen as invalid/duplicate.
3. Because S has not yet inserted a `BlockInfo` for B0's hash, S's `handle_block_pre_commit`/`handle_block_rejection` route X's message for B0 into `add_pending_block_pre_commit_response`/`add_pending_block_rejection_response` (`stacks-signer/src/v0/signer.rs:1258-1274`, `2240-2248`).
4. X's subsequent rejections for B1, B2, B3 (three more inserts for the same `signer_addr = X`) each fire the eviction trigger (`stacks-signer/src/signerdb.rs:826-876`), and since the cap is 3, X's original entry for B0 is deleted before S ever consumes it.
5. When B0's real proposal reaches S and S finally calls `drain_pending_block_responses(&B0_hash)` (`stacks-signer/src/v0/signer.rs:1641-1651`), X's vote is gone — S's tally for B0 permanently omits X's vote, delaying/denying the pre-commit or signature threshold from S's perspective.

This scenario, and the exact table-level eviction mechanics, are directly verifiable via the existing unit tests `test_signer_pre_commit_responses_eviction`, `test_signer_signature_responses_eviction`, and `test_signer_rejection_responses_eviction` in `stacks-signer/src/signerdb.rs:4669-4888`, which already demonstrate that inserting a 4th distinct block entry for one signer address evicts the oldest entry regardless of block hash.

### Citations

**File:** stacks-signer/src/signerdb.rs (L826-876)
```rust
// Triggers to auto-evict responses when a signer exceeds 3 entries
static CREATE_PENDING_PRE_COMMIT_RESPONSES_EVICTION_TRIGGER: &str = r#"
CREATE TRIGGER IF NOT EXISTS evict_old_pending_pre_commit_responses
AFTER INSERT ON signer_pending_pre_commit_responses
FOR EACH ROW
BEGIN
    DELETE FROM signer_pending_pre_commit_responses
    WHERE signer_addr = NEW.signer_addr
    AND (signer_signature_hash, received_time) IN (
        SELECT signer_signature_hash, received_time
        FROM signer_pending_pre_commit_responses
        WHERE signer_addr = NEW.signer_addr
        ORDER BY received_time DESC
        LIMIT -1 OFFSET 3
    );
END;
"#;

static CREATE_PENDING_SIGNATURE_RESPONSES_EVICTION_TRIGGER: &str = r#"
CREATE TRIGGER IF NOT EXISTS evict_old_pending_signature_responses
AFTER INSERT ON signer_pending_signature_responses
FOR EACH ROW
BEGIN
    DELETE FROM signer_pending_signature_responses
    WHERE signer_addr = NEW.signer_addr
    AND (signer_signature_hash, received_time) IN (
        SELECT signer_signature_hash, received_time
        FROM signer_pending_signature_responses
        WHERE signer_addr = NEW.signer_addr
        ORDER BY received_time DESC
        LIMIT -1 OFFSET 3
    );
END;
"#;

static CREATE_PENDING_REJECTION_RESPONSES_EVICTION_TRIGGER: &str = r#"
CREATE TRIGGER IF NOT EXISTS evict_old_pending_rejection_responses
AFTER INSERT ON signer_pending_rejection_responses
FOR EACH ROW
BEGIN
    DELETE FROM signer_pending_rejection_responses
    WHERE signer_addr = NEW.signer_addr
    AND (signer_signature_hash, received_time) IN (
        SELECT signer_signature_hash, received_time
        FROM signer_pending_rejection_responses
        WHERE signer_addr = NEW.signer_addr
        ORDER BY received_time DESC
        LIMIT -1 OFFSET 3
    );
END;
"#;
```

**File:** stacks-signer/src/signerdb.rs (L2574-2636)
```rust
    /// Retrieve and clear all pending block response entries matching the given block signer_signature_hash
    /// Returns PendingBlockResponses containing all matching pre-commits, approval signatures, and rejections
    pub fn drain_pending_block_responses(
        &self,
        block_sighash: &Sha512Trunc256Sum,
    ) -> Result<PendingBlockResponses, DBError> {
        let hash_str = block_sighash.to_string();

        // Delete and return pre-commits in one operation
        let pre_commits_qry = "DELETE FROM signer_pending_pre_commit_responses WHERE signer_signature_hash = ?1 RETURNING signer_addr";
        let mut stmt = self.db.prepare(pre_commits_qry)?;
        let pre_commits_rows = stmt.query_map(params![&hash_str], |row| {
            let addr_str: String = row.get(0)?;
            let addr = StacksAddress::from_string(&addr_str).ok_or(
                SqliteError::InvalidColumnType(0, addr_str.clone(), rusqlite::types::Type::Text),
            )?;
            Ok(addr)
        })?;
        let pre_commits: Vec<_> = pre_commits_rows.collect::<Result<Vec<_>, _>>()?;

        // Delete and return signatures in one operation
        let signatures_qry = "DELETE FROM signer_pending_signature_responses WHERE signer_signature_hash = ?1 RETURNING signer_addr, signature";
        let mut stmt = self.db.prepare(signatures_qry)?;
        let signatures_rows = stmt.query_map(params![&hash_str], |row| {
            let addr_str: String = row.get(0)?;
            let sig_str: String = row.get(1)?;
            let addr = StacksAddress::from_string(&addr_str).ok_or(
                SqliteError::InvalidColumnType(0, addr_str.clone(), rusqlite::types::Type::Text),
            )?;
            let signature: MessageSignature = serde_json::from_str(&sig_str).map_err(|_| {
                SqliteError::InvalidColumnType(1, sig_str.clone(), rusqlite::types::Type::Text)
            })?;
            Ok((addr, signature))
        })?;
        let signatures: Vec<_> = signatures_rows.collect::<Result<Vec<_>, _>>()?;

        // Delete and return rejections in one operation
        let rejections_qry = "DELETE FROM signer_pending_rejection_responses WHERE signer_signature_hash = ?1 RETURNING signer_addr, reject_code";
        let mut stmt = self.db.prepare(rejections_qry)?;
        let rejections_rows = stmt.query_map(params![&hash_str], |row| {
            let addr_str: String = row.get(0)?;
            let reject_code: u8 = row.get(1)?;
            let addr = StacksAddress::from_string(&addr_str).ok_or(
                SqliteError::InvalidColumnType(0, addr_str.clone(), rusqlite::types::Type::Text),
            )?;
            let reject_reason = RejectReasonPrefix::from(reject_code);
            Ok((addr, reject_reason))
        })?;
        let rejections: Vec<_> = rejections_rows.collect::<Result<Vec<_>, _>>()?;

        let pending_block_responses = PendingBlockResponses {
            pre_commits,
            signatures,
            rejections,
        };
        if !pending_block_responses.is_empty() {
            debug!("Drained pending block responses for block {block_sighash}";
                "pre_commits_count" => pending_block_responses.pre_commits.len(),
                "signatures_count" => pending_block_responses.signatures.len(),
                "rejections_count" => pending_block_responses.rejections.len());
        }
        Ok(pending_block_responses)
    }
```

**File:** stacks-signer/src/v0/signer.rs (L1258-1274)
```rust
        let Some(mut block_info) = self.block_lookup_by_reward_cycle(block_hash) else {
            // A pre-commit for a block we have not seen proposed yet means the proposal
            // has not reached us. Log it at INFO: it is a direct signal that our view of
            // the proposal stream is behind the rest of the signer set.
            info!("{self}: Received block pre-commit for an unknown block, storing as pending";
                "signer_address" => %stacker_address,
                "signer_signature_hash" => %block_hash,
                "signer_weight" => self.signer_weights.get(stacker_address).copied().unwrap_or(0),
            );
            if let Err(e) = self
                .signer_db
                .add_pending_block_pre_commit_response(block_hash, stacker_address)
            {
                warn!("{self}: Failed to save pending block pre-commit response: {e:?}");
            }
            return;
        };
```

**File:** stacks-signer/src/v0/signer.rs (L1630-1651)
```rust
        let pending_responses = if prior_block_info.is_some() {
            PendingBlockResponses::empty()
        } else {
            info!(
                "{self}: received a block proposal for a new block.";
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_proposal.block.block_id(),
                "block_height" => block_proposal.block.header.chain_length,
                "burn_height" => block_proposal.burn_height,
                "consensus_hash" => %block_proposal.block.header.consensus_hash,
            );
            self.signer_db
                .drain_pending_block_responses(&signer_signature_hash)
                .unwrap_or_else(|e| {
                    warn!(
                        "{self}: Failed to drain pending block responses for block proposal: {e:?}";
                        "signer_signature_hash" => %signer_signature_hash,
                        "block_id" => %block_proposal.block.block_id(),
                    );
                    PendingBlockResponses::empty()
                })
        };
```

**File:** stacks-signer/src/v0/signer.rs (L2240-2248)
```rust
        let Some(mut block_info) = self.block_lookup_by_reward_cycle(block_hash) else {
            if let Err(e) = self.signer_db.add_pending_block_rejection_response(
                block_hash,
                &signer_address,
                (&rejection.response_data.reject_reason).into(),
            ) {
                warn!("{self}: Failed to add pending block rejection response: {e:?}");
            }
            return;
```

**File:** stacks-signer/src/v0/signer.rs (L2412-2421)
```rust
        let Some(mut block_info) = self.block_lookup_by_reward_cycle(block_hash) else {
            if let Err(e) = self.signer_db.add_pending_block_signature_response(
                block_hash,
                &signer_address,
                signature,
            ) {
                warn!("{self}: Failed to add pending block signature response: {e:?}");
            }
            return;
        };
```
