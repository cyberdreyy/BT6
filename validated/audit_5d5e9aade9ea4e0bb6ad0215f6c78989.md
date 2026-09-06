## Title
Miner-triggered decoy proposals can evict a legitimate signer's early vote from the fixed 3-slot pending-response cache, causing that vote to be silently lost - (File: `stacks-signer/src/signerdb.rs`)

### Summary
The signer stores votes (pre-commits, signatures, rejections) received from other signers for a block it has not yet locally tracked in three per-signer-address tables (`signer_pending_pre_commit_responses`, `signer_pending_signature_responses`, `signer_pending_rejection_responses`), each capped at 3 entries per `signer_addr` via SQL triggers that evict the *oldest* row once a 4th is inserted. [1](#0-0)  These "early votes" are replayed only when the corresponding block proposal is finally received and tracked, via `drain_pending_block_responses`. [2](#0-1)  This mirrors the H-13 bug class: a fixed-capacity slot for "the request/vote we actually care about" can be silently displaced by adversary-triggered dust/decoy entries before the real one is claimed.

### Finding Description
When a signer receives a peer's pre-commit, signature, or rejection for a block hash it does not yet have in its local DB (e.g. because it hasn't yet processed the corresponding `BlockProposal`), it parks that vote keyed by `(signer_signature_hash, signer_addr)`:
- `add_pending_block_pre_commit_response` [3](#0-2) 
- `add_pending_block_signature_response` [4](#0-3) 
- `add_pending_block_rejection_response` [5](#0-4) 

Each table's eviction trigger deletes rows for a given `signer_addr` beyond the 3 most-recent-by-`received_time`, e.g. `evict_old_pending_pre_commit_responses` (mirrored for signatures and rejections). [6](#0-5)  When the real proposal finally arrives, `handle_block_proposal` calls `drain_pending_block_responses` to replay the parked votes for that exact hash — but only if they are still present. [7](#0-6) 

A miner is the actor who decides how many block proposals to broadcast and when. By broadcasting a burst of ≥4 distinct sibling/decoy block proposals (differing in timestamp/nonce, trivially miner-controlled) ahead of the block it actually wants processed, it induces honest peer signers to independently validate and broadcast genuine, correctly-signed votes for each decoy. If, due to ordinary gossip/propagation timing ("plus gossip"), one specific victim signer receives a given peer's vote for the *intended* (real) block before it receives/tracks the block proposal itself, that vote is stored as an "early" pending entry. If that same peer's votes for 3 more decoy hashes then arrive at the victim before the real proposal is delivered to the victim, the SQL eviction trigger deletes the oldest row — the genuine early vote for the real block — because eviction is purely `received_time`-oldest-first per `signer_addr`, with no concept of "this hash is the one that matters." This exactly parallels `KelpCooldownHolder._finalizeCooldown`'s hardcoded 0-th-slot claim: a fixed, small, index/order-based slot for "the thing we care about" that an adversary can fill with cheap decoys to permanently displace the legitimate entry before it is consumed.

When the real proposal is finally received, `drain_pending_block_responses` returns nothing for the evicted peer's vote, and that peer's endorsement is silently dropped from the victim's local tally rather than being replayed. [8](#0-7) 

### Impact Explanation
This degrades — but for a single victim signer and a single peer's vote per attack round — the accuracy of the pre-commit/signature weight the victim signer locally reconstructs for the real block. If an attacker (miner) repeats this pattern for many peer addresses simultaneously (broadcasting decoys is cheap and the miner fully controls this timing), it can cause a specific signer to systematically undercount pre-commits/signatures for legitimate blocks it eventually receives, delaying that signer's local threshold computation and, in aggregate across many honest instances hitting the same race, degrading how quickly the network-wide pre-commit/signing threshold is reached. There is no equivocation or invalid-signature outcome here — peers' votes remain authentic and are simply dropped from one signer's bookkeeping — so this is a liveness degradation (delay in threshold accumulation for that signer), not a safety break. It does not, by itself, cause a signer to sign an invalid/non-canonical block, nor does it require a majority of signers or another signer's key: it only requires ordinary honest peer voting behavior plus miner-controlled proposal timing/volume.

### Likelihood Explanation
Requires (a) the attacker to be the block-proposing miner (in-scope, one-slot-miner capability) generating several distinct valid decoy block proposals in a tight window, and (b) a race condition where a specific peer's vote for the "real" block reaches a specific victim signer strictly before that victim's own copy of the real proposal — a timing condition dependent on normal gossip/StackerDB propagation delays rather than attacker-controlled delivery order. This makes the exploit non-trivial to land deterministically and its effect bounded (loses at most one vote per attack window per targeted signer address, self-limited by the size-3 cache), which is why this is assessed as a likelihood-limited liveness nuisance rather than a guaranteed high-impact wedge.

### Recommendation
Do not evict a pending vote purely on `received_time`-oldest-first heuristics blind to which block the local proposal-tracking system is about to need. Options:
- Track pending-response capacity per `(signer_addr)` but prioritize keeping entries for hashes that correspond to the signer's currently-active tenure/height rather than pure FIFO, or
- Increase capacity and/or key eviction off of `(signer_addr, tenure/height bucket)` so that unrelated decoy hashes at other heights cannot evict a vote for a block at the height the signer is actively awaiting, or
- On receiving a `BlockProposal`, explicitly query all three pending tables for `(hash, addr)` combinations by broadcasting a resync/re-vote request to peers if `drain_pending_block_responses` returns fewer entries than expected weight, rather than assuming completeness.

### Proof of Concept
Conceptual PoC (cannot be executed without full network harness access):
1. Signer network is running; Miner M controls the sole active miner slot.
2. M broadcasts block proposal `P_real` (the block it truly wants confirmed) at height `h`.
3. Due to network scheduling, peer signer `S` validates and signs/pre-commits `P_real` and broadcasts its `BlockAccepted`/`BlockPreCommit` before the victim signer `V` has received/tracked `P_real` locally — `V` stores this as a pending entry keyed `(hash(P_real), S)` via `add_pending_block_signature_response`. [4](#0-3) 
4. M immediately broadcasts 3 more distinct decoy proposals `P1, P2, P3` (different timestamps) at the same/adjacent height. Peer `S` independently evaluates and casts votes on each, all reaching `V` before `V` tracks `P_real`.
5. Each new vote insertion for `S` triggers `evict_old_pending_pre_commit_responses`/`evict_old_pending_signature_responses`, which evicts the row for `hash(P_real)` since it is now the oldest of `S`'s 4 pending rows. [9](#0-8) 
6. When `V` finally receives and tracks `P_real`, `handle_block_proposal` calls `drain_pending_block_responses(hash(P_real))`, which no longer contains `S`'s vote. [7](#0-6) , [10](#0-9) 
7. `V`'s locally reconstructed weight for `P_real` is missing `S`'s (valid, honestly cast) vote, delaying `V`'s local threshold computation for that block.

This PoC is based on code-path analysis of the cited functions; I was not able to execute it against a live multi-signer test harness, so the precise timing window required to reliably trigger the race is unverified.

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

**File:** stacks-signer/src/signerdb.rs (L2496-2518)
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
```

**File:** stacks-signer/src/signerdb.rs (L2520-2544)
```rust
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
```

**File:** stacks-signer/src/signerdb.rs (L2546-2572)
```rust
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
