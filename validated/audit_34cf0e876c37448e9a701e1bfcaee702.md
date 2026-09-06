### Title
Pending block-response cache silently evicts legitimate signer votes after only 3 entries per peer, causing a lagging signer to lose peers' pre-commits/signatures/rejections for the block that is actually proposed - (File: `stacks-signer/src/signerdb.rs`)

### Summary
`stacks-signer` parks a peer's pre-commit, signature, or rejection message for a block it hasn't yet received a proposal for in three SQLite tables (`signer_pending_pre_commit_responses`, `signer_pending_signature_responses`, `signer_pending_rejection_responses`). Each table has an `AFTER INSERT` trigger that keeps at most 3 rows per `signer_addr`, silently deleting the oldest ones once a fourth distinct block hash is recorded for that peer. This is the same bug class as the ZetaChain report's hardcoded 2-hash tracker: a fixed, small cap on a structure that must track an unbounded number of distinct, evolving identifiers, with silent, irreversible discarding once the cap is exceeded.

### Finding Description
The eviction triggers are keyed only by `signer_addr`, not by block/tenure/height, so they cap *all* of a peer's outstanding "early" votes across every unresolved proposal at 3: [1](#0-0) 

`add_pending_block_pre_commit_response`, `add_pending_block_signature_response`, and `add_pending_block_rejection_response` are the insert paths feeding this cap: [2](#0-1) 

A peer's vote lands in one of these tables whenever our signer receives a pre-commit/signature/rejection for a block it hasn't stored yet, e.g. in `handle_block_pre_commit`: [3](#0-2) 

Only when the corresponding `BlockProposal` finally arrives does the signer collect ("drain") whatever survived for that specific hash and replay it: [4](#0-3) [5](#0-4) [6](#0-5) 

A single miner controlling one slot can drive a peer signer address to accumulate votes for more than 3 distinct, not-yet-locally-known block hashes before our target signer catches up (e.g. by repeatedly re-proposing/timing out and issuing several distinct proposals in a burst, or by any ordinary sequence of tenure activity combined with normal gossip latency skew that leaves one signer briefly behind on `BlockProposal` messages while ahead on `BlockPreCommit`/`BlockResponse` gossip from others). Once a 4th distinct hash for that signer address is recorded, the eviction trigger deletes the oldest row for that address — including, potentially, the row for the block that ultimately becomes the one our lagging signer is asked to evaluate. When that proposal is finally received and `drain_pending_block_responses` runs, that peer's vote is gone and can never be replayed, because peers do not re-broadcast an already-sent pre-commit/signature/rejection except when the *same* proposal (identical `signer_signature_hash`) is literally re-sent by the miner — which does not happen for a block that was never re-proposed.

### Impact Explanation
This silently and permanently drops a legitimate peer's tallied weight (pre-commit, acceptance signature, or rejection) from the affected signer's local view of the block it is currently evaluating. Because the local pre-commit/signature-threshold tally (`handle_block_pre_commit`, `store_and_process_block_signature`, `store_and_process_block_rejection`) is exactly the mechanism the rules flag ("aggregated-weight vs verified-accepts" equality), losing one peer's vote unrecoverably wedges that signer's own tally: it can be stuck below the 70% pre-commit/signature threshold it would otherwise have reached, or fail to notice a 30% global-rejection threshold that the rest of the network already observed, purely due to a bookkeeping cap rather than an actual absence of votes. This matches the "signer wedged into never signing valid blocks" / liveness-wedge class called out in the rules — reachable with only a single miner's proposal cadence plus ordinary gossip timing, no majority collusion, no other signer's key, and no StackerDB transport exploitation.

### Likelihood Explanation
Requires only: (1) a single miner (one slot) issuing more than 3 distinct block proposals in quick succession (routine during retries/timeouts/tenure churn), and (2) one signer being briefly behind on proposal delivery relative to pre-commit/signature/rejection gossip from peers — an ordinary network-timing condition, not an adversarial requirement beyond miner behavior already permitted by the protocol. The eviction cap of "3" was clearly sized for a small number of concurrently-outstanding unknown proposals; any burst exceeding that during normal chain activity triggers the loss.

### Recommendation
Scope the per-peer pending-response cap to the currently-live proposal set (e.g. by tenure/height/reward-cycle window) instead of an unconditional global count of 3 per `signer_addr`, or increase/eliminate the cap and instead prune entries by age/tenure once the corresponding block is known to be settled (globally accepted/rejected) or provably stale, mirroring the ZetaChain fix recommendation of tracking all relevant identifiers rather than a small fixed number.

### Proof of Concept
1. Signer `S` is briefly behind on `BlockProposal` stackerdb messages from the miner but current on `BlockPreCommit`/`BlockResponse` gossip from peer `P` (ordinary network skew).
2. Miner (one slot) produces 4 distinct block proposals in a short window (e.g. across retried tenure-start attempts). Peer `P` processes each proposal in order and broadcasts its pre-commit/signature for each; `S` has not yet seen any of the 4 proposals, so each of `P`'s messages is recorded via `add_pending_block_pre_commit_response`/`add_pending_block_signature_response` (`signerdb.rs:2496-2572`).
3. On the 4th insert for `P`, the trigger in `signerdb.rs:826-859` deletes the oldest row for `P` — the entry for proposal #1.
4. `S` finally receives proposal #1 (the one that is ultimately canonical/accepted by the rest of the network) and calls `drain_pending_block_responses` (`signerdb.rs:2574-2636`) via `handle_block_proposal` (`signer.rs:1630-1651`); `P`'s vote for proposal #1 is absent from the result.
5. `S`'s local tally for proposal #1 permanently lacks `P`'s weight, even though `P` did vote for it, wedging `S`'s local threshold computation in `handle_block_pre_commit` (`signer.rs:1250-1345` uses `get_block_pre_committers`/`compute_signature_signing_weight`, which never sees `P`'s entry since it was never replayed into `block_pre_commits`).

### Citations

**File:** stacks-signer/src/signerdb.rs (L826-859)
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
```

**File:** stacks-signer/src/signerdb.rs (L2496-2572)
```rust
    /// Record a pending block pre-commit response for an untracked block proposal
    /// Automatically evicts oldest entries if this signer has more than 3 entries
    pub fn add_pending_block_pre_commit_response(
        &self,
        block_sighash: &Sha512Trunc256Sum,
        signer_addr: &StacksAddress,
    ) -> Result<(), DBError> {
        let received_time = get_epoch_time_secs();
        let qry = "INSERT OR REPLACE INTO signer_pending_pre_commit_responses (signer_signature_hash, signer_addr, received_time) VALUES (?1, ?2, ?3);";
        let args = params![
            block_sighash.to_string(),
            signer_addr.to_string(),
            u64_to_sql(received_time)?
        ];

        debug!("Recording pending pre-commit response for untracked block.";
            "signer_signature_hash" => %block_sighash,
            "signer_addr" => %signer_addr,
            "received_time" => received_time);

        self.db.execute(qry, args)?;
        Ok(())
    }

    /// Record a pending block signature response for an untracked block proposal
    /// Automatically evicts oldest entries if this signer has more than 3 entries
    pub fn add_pending_block_signature_response(
        &self,
        block_sighash: &Sha512Trunc256Sum,
        signer_addr: &StacksAddress,
        signature: &MessageSignature,
    ) -> Result<(), DBError> {
        let received_time = get_epoch_time_secs();
        let qry = "INSERT OR REPLACE INTO signer_pending_signature_responses (signer_signature_hash, signer_addr, signature, received_time) VALUES (?1, ?2, ?3, ?4);";
        let args = params![
            block_sighash.to_string(),
            signer_addr.to_string(),
            serde_json::to_string(signature).map_err(DBError::SerializationError)?,
            u64_to_sql(received_time)?
        ];

        debug!("Recording pending signature response for untracked block.";
            "signer_signature_hash" => %block_sighash,
            "signer_addr" => %signer_addr,
            "received_time" => received_time);

        self.db.execute(qry, args)?;
        Ok(())
    }

    /// Record a pending block rejection response for an untracked block proposal
    /// Automatically evicts oldest entries if this signer has more than 3 entries
    pub fn add_pending_block_rejection_response(
        &self,
        block_sighash: &Sha512Trunc256Sum,
        signer_addr: &StacksAddress,
        reject_reason: RejectReasonPrefix,
    ) -> Result<(), DBError> {
        let received_time = get_epoch_time_secs();
        let reject_code = reject_reason as i64;
        let qry = "INSERT OR REPLACE INTO signer_pending_rejection_responses (signer_signature_hash, signer_addr, reject_code, received_time) VALUES (?1, ?2, ?3, ?4);";
        let args = params![
            block_sighash.to_string(),
            signer_addr.to_string(),
            reject_code,
            u64_to_sql(received_time)?
        ];

        debug!("Recording pending rejection response for untracked block.";
            "signer_signature_hash" => %block_sighash,
            "signer_addr" => %signer_addr,
            "reject_code" => reject_code,
            "received_time" => received_time);

        self.db.execute(qry, args)?;
        Ok(())
    }
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

**File:** stacks-signer/src/v0/signer.rs (L1258-1273)
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

**File:** stacks-signer/src/v0/signer.rs (L1729-1780)
```rust
    /// Process pending responses for a block proposal that we may have received late.
    fn process_pending_responses_for_block(
        &mut self,
        stacks_client: &StacksClient,
        sortition_state: &mut Option<SortitionsView>,
        block_info: &mut BlockInfo,
        pending_responses: PendingBlockResponses,
    ) {
        let signer_signature_hash = block_info.block.header.signer_signature_hash();
        for stacker_address in pending_responses.pre_commits {
            debug!("{self}: Processing pending pre-commit.";
                "stacker_address" => %stacker_address,
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_info.block.block_id(),
            );
            self.handle_block_pre_commit(
                stacks_client,
                sortition_state,
                &stacker_address,
                &signer_signature_hash,
            );
        }
        for (stacker_address, reject_reason) in pending_responses.rejections {
            debug!("{self}: Processing pending rejection.";
                "stacker_address" => %stacker_address,
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_info.block.block_id(),
                "reject_reason" => ?reject_reason,
            );
            self.store_and_process_block_rejection(
                sortition_state,
                block_info,
                &stacker_address,
                reject_reason,
            );
        }
        let block_id = block_info.block.block_id();
        for (stackers_address, signature) in pending_responses.signatures {
            debug!("{self}: Processing pending signature.";
                "stacker_address" => %stackers_address,
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_id,
            );
            self.store_and_process_block_signature(
                stacks_client,
                sortition_state,
                block_info,
                &stackers_address,
                &signature,
            );
        }
    }
```
