### Title
Equivocation guard (`signed_group`) can be lost on a crash between `add_block_signature` and `mark_locally_accepted`/`insert_block`, enabling a signer to sign a conflicting sibling block after restart — ([File: stacks-signer/src/v0/signer.rs])

### Summary
`store_and_process_block_signature` persists a peer's raw signature bytes to the `block_signatures` table *before* it durably records that this signer has reached group consensus on the block (`signed_group`, via `mark_locally_accepted(true)` + `insert_block`). If the process crashes/restarts in the window between those two writes, the block's signatures can already meet the 70% threshold in the database, yet the `blocks` row (which is what the equivocation guard actually reads) shows no `signed_group`/`signed_self` for that block. On restart, `get_signed_conflicts`/`has_signed_block_in_tenure` won't see this block as "signed", so the signer can go on to sign a sibling block at the same height in a different tenure — a genuine equivocation.

### Finding Description
`store_and_process_block_signature` first stores the incoming signature: [1](#0-0) 

Only after tallying weight and confirming the threshold is reached does it call `mark_locally_accepted(true)` and then persist the block row: [2](#0-1) 

This is the opposite ordering from the one deliberately used on the "we sign it ourselves" path, where the code comment explicitly states the guard field must be persisted *before* the signature is recorded: [3](#0-2) 

The equivocation guard used at pre-commit and validate-ok time is built entirely from the persisted `blocks` table columns `signed_self`/`signed_group`, not from the `block_signatures` table: [4](#0-3) [5](#0-4) 

If a signer's process is killed (OOM, operator restart, disk-full triggering one of the `panic!`/`unwrap_or_else(|_| panic!(...))` calls in this same function at lines 2457, 2477, 2500, or 2535) after `add_block_signature` has already written a signature that pushes `total_signature_weight` past `min_weight`, but before `insert_block` commits the `signed_group` timestamp, the on-disk state is inconsistent: `block_signatures` shows quorum reached, but `blocks.signed_group` (and, if this signer had not separately signed itself, `blocks.signed_self`) is still `NULL`. This is directly analogous to the arenavec bug class: a panic partway through a multi-step mutation leaves a structure holding data that looks "uninitialized" from the perspective of code that later inspects only the guard field, even though the underlying storage already contains a valid, safety-relevant payload.

### Impact Explanation
On restart, `LocalStateMachine::new`/pending-response replay does not re-run `store_and_process_block_signature` for blocks whose signatures are already durably stored in `block_signatures` — nothing recomputes `signed_group` from the `block_signatures` table at startup. The signer's local state therefore believes it never endorsed this block. When a competing/sibling block proposal for the same height (e.g., from a different tenure) arrives and reaches its own pre-commit threshold, `handle_block_pre_commit`'s conflict check (`get_signed_conflicts`) will not find the already-quorum'd block as a conflict, and the signer will proceed to sign the new, conflicting block — producing two signer-signature-hash-valid signatures at the same height from the same signer key. This matches the explicitly accepted High/Critical impact: "losing the equivocation guard on restart" leading to a signer signing a conflicting block.

### Likelihood Explanation
This requires only an ordinary process crash/restart (OOM kill, operator restart, disk exhaustion causing rusqlite errors that this function turns into `panic!`) landing in a narrow window between two sequential DB writes inside a single function — no majority collusion, no other signer's key, and no StackerDB/flooding manipulation is needed. A single miner (or a normal signer restart during network operation) combined with a subsequent conflicting proposal at the same height is sufficient to trigger the unsafe branch. The likelihood is moderate: it depends on timing (crash exactly between the two writes) but such crashes are a normal operational occurrence (deploys, OOM, resource pressure), and the code path is on every peer-signature-received hot path.

### Recommendation
- Persist the guard-relevant `signed_group`/`signed_self` state to the `blocks` table atomically with (or before) writing to `block_signatures`, mirroring the ordering already used in the self-sign path (`handle_block_pre_commit`/lines 1466-1479).
- Alternatively, wrap the `add_block_signature` write and the subsequent `mark_locally_accepted`/`insert_block` write in a single SQLite transaction so a crash cannot leave one persisted without the other.
- On signer startup, recompute `signed_group` for any block whose `block_signatures` already meet the weight threshold, so a crash-induced gap is healed before the signer participates in any new pre-commit decision.

### Proof of Concept
1. Signer S is not the block's own signer for a given `signer_signature_hash` (or has not yet locally signed it) and receives peer acceptance signatures via `handle_block_response` → `handle_block_signature` → `store_and_process_block_signature`.
2. The incoming signature is the one that pushes cumulative weight ≥ 70% (`add_block_signature` at signer.rs:2454-2457 succeeds and returns `true`).
3. Kill the signer process (SIGKILL/OOM) after step 2 but before `insert_block` at signer.rs:2533-2536 commits (e.g., inject a delay or crash right after the `min_weight > total_signature_weight` check at line 2503).
4. Restart the signer. Inspect its `SignerDb`: `block_signatures` table contains a quorum of valid signatures for the block, but the `blocks` row's `signed_group`/`signed_self` remain `NULL`.
5. Propose a sibling block at the same `chain_length` in a different tenure and drive it to the pre-commit threshold. `get_signed_conflicts` (signerdb.rs:1606-1625) returns no conflict for the already-quorum'd block, so `handle_block_pre_commit` proceeds to `mark_locally_accepted`/sign the new block — completing the equivocation.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1466-1479)
```rust
        // It is only considered globally accepted IFF we receive a new block event confirming it OR see the chain tip of the node advance to it.
        if let Err(e) = block_info.mark_locally_accepted(false) {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally accepted: {e:?}",);
            }
        }
        self.signer_db
            .insert_block(&block_info)
            .unwrap_or_else(|e| self.handle_insert_block_error(e));
        let accepted = self.create_block_acceptance(&block_info.block);
        // have to save the signature _after_ the block info
        self.handle_block_signature(stacks_client, sortition_state, &accepted);
        self.send_block_response(&block_info.block, accepted.into());
    }
```

**File:** stacks-signer/src/v0/signer.rs (L2452-2460)
```rust
        // signature is valid! store it.
        // if this returns false, it means the signature already exists in the DB, so just return.
        if !self
            .signer_db
            .add_block_signature(block_hash, signer_address, signature)
            .unwrap_or_else(|_| panic!("{self}: Failed to save block signature"))
        {
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2525-2537)
```rust
        // have enough signatures to broadcast!
        // move block to LOCALLY accepted state.
        // It is only considered globally accepted IFF we receive a new block event confirming it OR see the chain tip of the node advance to it.
        if let Err(e) = block_info.mark_locally_accepted(true) {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally accepted: {e:?}");
            }
        }
        let _ = self.signer_db.insert_block(block_info).map_err(|e| {
            warn!("Failed to set group threshold signature timestamp for {block_hash}: {e:?}");
            panic!("{self} Failed to write block to signerdb: {e}");
        });
        self.broadcast_signed_block(stacks_client, block_info.block.clone(), &addrs_to_sigs);
```

**File:** stacks-signer/src/signerdb.rs (L1606-1625)
```rust
    pub fn get_signed_conflicts(
        &self,
        height: u64,
        excluded_signer_signature_hash: &Sha512Trunc256Sum,
    ) -> Result<Vec<SignedConflictInfo>, DBError> {
        let query = "SELECT b.consensus_hash, b.signer_signature_hash, b.stacks_height, b.state,
                MAX(COALESCE(b.signed_self, 0), COALESCE(b.signed_group, 0)) AS last_endorsed,
                st.superseded_by_consensus_hash, st.superseded_by_burn_block_hash
            FROM blocks b
            LEFT JOIN superseded_tenures st ON st.consensus_hash = b.consensus_hash
            WHERE (b.signed_self IS NOT NULL OR b.signed_group IS NOT NULL)
                AND b.stacks_height >= ?1
                AND b.signer_signature_hash != ?2
            ORDER BY b.stacks_height DESC";
        let args = params![
            u64_to_sql(height)?,
            excluded_signer_signature_hash.to_string(),
        ];
        query_rows(&self.db, query, args)
    }
```

**File:** stacks-signer/src/signerdb.rs (L2649-2671)
```rust
/// The identifying details of a signed block that conflicts with a block proposal, as
/// returned by [`SignerDb::get_signed_conflicts`].
#[derive(Debug)]
pub struct SignedConflictInfo {
    /// The consensus hash of the tenure containing the conflicting block
    pub consensus_hash: ConsensusHash,
    /// The signer signature hash of the conflicting block
    pub signer_signature_hash: Sha512Trunc256Sum,
    /// The Stacks height of the conflicting block
    pub stacks_height: u64,
    /// The most recent time (epoch seconds) at which we signed the block or observed the
    /// signer set accept it (0 if neither was recorded)
    pub last_endorsed: u64,
    /// Whether the block reached global acceptance, which is what decides if the node ever had
    /// it: a locally accepted block is not handed to the node until the whole signer set has
    /// signed it, so the node not having one says nothing about whether it is still live.
    pub globally_accepted: bool,
    /// The sortition of the tenure we permitted to reorg this block's tenure, if we recorded
    /// such a permit (see [`SignerDb::mark_tenure_superseded`]). The permit excludes this
    /// conflict only while that sortition is still canonical, which the caller must derive
    /// from the node.
    pub superseded_by: Option<SupersededBy>,
}
```
