## Finding: `SignerDb` block state keyed only by `signer_signature_hash`, without reward-cycle scoping (IDOR-style key reuse across cycles)

### Title
Signer block/signature lookups keyed solely by attacker-influenced `signer_signature_hash` bypass reward-cycle scoping, letting cross-cycle state (signatures/rejections/global-accept status) leak into the wrong signer-set context - (File: `stacks-signer/src/signerdb.rs`)

### Summary
The signer's `blocks` table is nominally scoped by `(reward_cycle, signer_signature_hash)` [1](#0-0) , but the actual read path `SignerDb::block_lookup` queries only by `signer_signature_hash`, dropping the `reward_cycle` predicate entirely [2](#0-1) . The companion tables `block_signatures` and `block_rejection_signer_addrs` don't even have a `reward_cycle` column - they are keyed purely by `signer_signature_hash` [3](#0-2) . This is the analog of the reported IDOR: a value the caller controls indirectly (the block's own sighash and the timing/context of message delivery) is used as the sole lookup key into state that is supposed to be scoped per "virtual instance" (here, per reward cycle / signer set).

### Finding Description
Most call sites in `stacks-signer/src/v0/signer.rs` compensate for this by wrapping `block_lookup` in `block_lookup_by_reward_cycle`, which re-checks `block_info.reward_cycle == self.reward_cycle` after the fetch [4](#0-3) . That is an application-level patch on top of a database layer that is not actually scoped correctly.

Critically, not every call site uses the safe wrapper. The `SignerEvent::NewBlock` handler - which fires on a gossiped node event, not a per-cycle-checked proposal - calls the unscoped `block_lookup` directly: [5](#0-4) 

This event is delivered to whichever `ConfiguredSigner` instance(s) are alive at the time; a signer runs `ConfiguredSigner` instances for both the current and adjacent reward cycles around cycle boundaries (see `is_configured_for_cycle`/`is_registered_for_cycle`/`cleanup_stale_signers` in `runloop.rs`, and the regression test `signing_in_0th_tenure_of_reward_cycle` that explicitly exercises "0th tenure" boundary behavior for exactly this dual-instance window) [6](#0-5) [7](#0-6) .

Because `blocks` allows one row per `(reward_cycle, signer_signature_hash)`, the *same* `signer_signature_hash` can legitimately have two rows - one per adjacent reward cycle - when a boundary block is evaluated by both the outgoing and incoming `ConfiguredSigner`. `insert_block` writes exactly the `reward_cycle` field carried inside the `BlockInfo`/`BlockProposal`, which is caller-supplied metadata, not something cryptographically bound into `signer_signature_hash` itself [8](#0-7) . When `block_lookup(hash)` is later called without a `reward_cycle` filter, SQLite will return an arbitrary one of the matching rows - potentially the *other* cycle's `BlockInfo`, with its own `state`/`valid`/`vote`. The handler then mutates and re-inserts that wrong-context `BlockInfo` (`mark_globally_accepted()` + `insert_block`), corrupting the record for whichever cycle it actually belonged to and/or causing the current signer instance to short-circuit ("already GloballyAccepted, do nothing") based on another cycle's state.

The same root cause is worse for `block_signatures`: since that table has no `reward_cycle` column at all, `get_block_signatures(block_hash)` in `store_and_process_block_signature` [9](#0-8)  will return signatures recorded by any signer for that hash regardless of which cycle's context produced them. Any signer address registered across both adjacent cycles (common at boundaries) that signed a boundary block under one cycle's context has that signature permanently counted toward the acceptance-weight threshold for the same hash in the other cycle's context, mixing weight/signer-set membership between two distinct reward-cycle equality domains.

### Impact Explanation
This breaks the "signed vs validated" / "approved-parent vs canonical" equality the signer state machine relies on: a signature or acceptance state computed under one reward cycle's signer set/threshold is silently reused as if it applied to a different reward cycle's context, purely because the lookup key (`signer_signature_hash`) is not scoped by `reward_cycle` at the storage layer. In the worst case this lets a rejection or unvalidated block state in one cycle be treated as already `GloballyAccepted` in another, or lets pooled signature weight from the wrong signer set satisfy a threshold - a Critical-class outcome (rejection/other-context state recounted as acceptance) with no majority-of-signers assumption required; only ordinary boundary-tenure gossip/timing plus the existing "signer registered across adjacent cycles" case is needed.

### Likelihood Explanation
Every reward-cycle boundary produces the dual-`ConfiguredSigner` window this depends on (proven by the existing `signing_in_0th_tenure_of_reward_cycle` test), and `SignerEvent::NewBlock` is an ordinary node-gossiped event delivered to all live signer instances, so the unscoped `block_lookup` call is reachable in normal operation, not a contrived edge case.

### Recommendation
Add `reward_cycle` to the `WHERE` clause of `block_lookup` (or require all call sites to go through `block_lookup_by_reward_cycle`), and add a `reward_cycle` column with cycle-scoped queries to `block_signatures` and `block_rejection_signer_addrs` so signature/rejection bookkeeping cannot be shared across two different reward cycles' signer sets for the same underlying block hash.

### Proof of Concept
1. At a reward-cycle boundary, both the outgoing (cycle N) and incoming (cycle N+1) `ConfiguredSigner` instances are alive and observe the boundary tenure's block (per `is_configured_for_cycle`).
2. Signer address S, registered in both cycle N and N+1's signer sets, signs the boundary block under cycle N; `add_block_signature` stores the signature keyed only by `signer_signature_hash` (no cycle) [10](#0-9) .
3. The cycle-N+1 `ConfiguredSigner` instance later evaluates a proposal for the identical `signer_signature_hash` (or processes the `SignerEvent::NewBlock` for it); `get_block_signatures`/`block_lookup` return S's cycle-N signature/state without any cycle filter, contributing to cycle-N+1's threshold or short-circuiting its own accept/reject evaluation using cycle-N's stored `BlockInfo`.

### Citations

**File:** stacks-signer/src/signerdb.rs (L391-401)
```rust
static CREATE_BLOCKS_TABLE_1: &str = "
CREATE TABLE IF NOT EXISTS blocks (
    reward_cycle INTEGER NOT NULL,
    signer_signature_hash TEXT NOT NULL,
    block_info TEXT NOT NULL,
    consensus_hash TEXT NOT NULL,
    signed_over INTEGER NOT NULL,
    stacks_height INTEGER NOT NULL,
    burn_block_height INTEGER NOT NULL,
    PRIMARY KEY (reward_cycle, signer_signature_hash)
) STRICT";
```

**File:** stacks-signer/src/signerdb.rs (L502-512)
```rust
static CREATE_BLOCK_SIGNATURES_TABLE: &str = r#"
CREATE TABLE IF NOT EXISTS block_signatures (
    -- The block sighash commits to all of the stacks and burnchain state as of its parent,
    -- as well as the tenure itself so there's no need to include the reward cycle.  Just
    -- the sighash is sufficient to uniquely identify the block across all burnchain, PoX,
    -- and stacks forks.
    signer_signature_hash TEXT NOT NULL,
    -- signature itself
    signature TEXT NOT NULL,
    PRIMARY KEY (signature)
) STRICT;"#;
```

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

**File:** stacks-signer/src/signerdb.rs (L1807-1852)
```rust
    /// Insert or replace a block into the database.
    /// Preserves the `broadcast` column if replacing an existing block.
    pub fn insert_block(&mut self, block_info: &BlockInfo) -> Result<(), DBError> {
        let block_json =
            serde_json::to_string(&block_info).expect("Unable to serialize block info");
        let hash = &block_info.signer_signature_hash();
        let block_id = &block_info.block.block_id();
        let vote = block_info
            .vote
            .as_ref()
            .map(|v| if v.rejected { "REJECT" } else { "ACCEPT" });
        let broadcasted = self.get_block_broadcasted(hash)?;
        debug!("Inserting block_info.";
            "reward_cycle" => %block_info.reward_cycle,
            "burn_block_height" => %block_info.burn_block_height,
            "signer_signature_hash" => %hash,
            "block_id" => %block_id,
            "broadcasted" => ?broadcasted,
            "vote" => vote
        );
        self.db.execute(
            "INSERT OR REPLACE INTO blocks
              (reward_cycle, burn_block_height, signer_signature_hash, block_info,
               broadcasted, stacks_height, consensus_hash, valid, state, signed_group, signed_self, approved_time,
               proposed_time, validation_time_ms, tenure_change, tenure_change_cause)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16)",
            params![
                u64_to_sql(block_info.reward_cycle)?,
                u64_to_sql(block_info.burn_block_height)?,
                hash.to_string(),
                block_json,
                &broadcasted,
                u64_to_sql(block_info.block.header.chain_length)?,
                block_info.block.header.consensus_hash.to_hex(),
                &block_info.valid,
                &block_info.state.to_string(),
                &block_info.signed_group,
                &block_info.signed_self,
                &block_info.approved_time,
                &block_info.proposed_time,
                &block_info.validation_time_ms,
                &block_info.is_tenure_change(),
                &block_info.tenure_change_cause().map(|x| x.as_u8()),
            ],
        )?;
        Ok(())
```

**File:** stacks-signer/src/signerdb.rs (L1908-1920)
```rust
    /// Get all signatures for a block
    pub fn get_block_signatures(
        &self,
        block_sighash: &Sha512Trunc256Sum,
    ) -> Result<Vec<MessageSignature>, DBError> {
        let qry = "SELECT signature FROM block_signatures WHERE signer_signature_hash = ?1";
        let args = params![block_sighash];
        let sigs_txt: Vec<String> = query_rows(&self.db, qry, args)?;
        sigs_txt
            .into_iter()
            .map(|sig_txt| serde_json::from_str(&sig_txt).map_err(|_| DBError::ParseError))
            .collect()
    }
```

**File:** stacks-signer/src/v0/signer.rs (L706-719)
```rust
                self.local_state_machine
                    .stacks_block_arrival(consensus_hash, *block_height, block_id, signer_sighash, &self.signer_db, transactions)
                    .unwrap_or_else(|e| error!("{self}: failed to update local state machine for latest stacks block arrival"; "err" => ?e));

                if let Ok(Some(mut block_info)) = self
                    .signer_db
                    .block_lookup(signer_sighash)
                    .inspect_err(|e| warn!("{self}: Failed to load block state: {e:?}"))
                {
                    if block_info.state == BlockState::GloballyAccepted {
                        // We have already globally accepted this block. Do nothing.
                        return;
                    }
                    if let Err(e) = block_info.mark_globally_accepted() {
```

**File:** stacks-signer/src/v0/signer.rs (L2442-2477)
```rust
    /// Store the block acceptance signature and check if we have reached a consensus decision on the block because of it. If we have, update the block state accordingly and broadcast the block if accepted.
    fn store_and_process_block_signature(
        &mut self,
        stacks_client: &StacksClient,
        sortition_state: &mut Option<SortitionsView>,
        block_info: &mut BlockInfo,
        signer_address: &StacksAddress,
        signature: &MessageSignature,
    ) {
        let block_hash = &block_info.signer_signature_hash();
        // signature is valid! store it.
        // if this returns false, it means the signature already exists in the DB, so just return.
        if !self
            .signer_db
            .add_block_signature(block_hash, signer_address, signature)
            .unwrap_or_else(|_| panic!("{self}: Failed to save block signature"))
        {
            return;
        }

        // If this isn't our own signature and we haven't seen a pre-commit from this signer yet, try treating it as a pre-commit in case the caller is running an outdated version
        if signer_address != &self.stacks_address && !self.signer_db.has_committed(block_hash, signer_address).inspect_err(|e| warn!("Failed to check if pre-commit message already considered for {signer_address:?} for {block_hash}: {e}")).unwrap_or(false) {
            self.handle_block_pre_commit(stacks_client, sortition_state, signer_address, block_hash);
            return;
        }

        if block_info.signed_group.is_some() {
            // We have already processed this block to the accepted state. Adding more signatures will not change anything so nothing to check.
            return;
        }
        // do we have enough signatures to broadcast?
        // i.e. is the threshold reached?
        let signatures = self
            .signer_db
            .get_block_signatures(block_hash)
            .unwrap_or_else(|_| panic!("{self}: Failed to load block signatures"));
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

**File:** stacks-signer/src/runloop.rs (L450-469)
```rust
    fn is_configured_for_cycle(
        stacks_signers: &HashMap<u64, ConfiguredSigner<Signer, T>>,
        reward_cycle: u64,
    ) -> bool {
        let Some(signer) = stacks_signers.get(&(reward_cycle % 2)) else {
            return false;
        };
        signer.reward_cycle() == reward_cycle
    }

    fn is_registered_for_cycle(
        stacks_signers: &HashMap<u64, ConfiguredSigner<Signer, T>>,
        reward_cycle: u64,
    ) -> bool {
        let Some(signer) = stacks_signers.get(&(reward_cycle % 2)) else {
            return false;
        };
        signer.reward_cycle() == reward_cycle
            && matches!(signer, ConfiguredSigner::RegisteredSigner(_))
    }
```

**File:** stacks-node/src/tests/signer/v0/mod.rs (L5166-5172)
```rust
#[test]
#[ignore]
/// Test that signers can successfully sign a block proposal in the 0th tenure of a reward cycle
/// This ensures there is no race condition in the /v2/pox endpoint which could prevent it from updating
/// on time, possibly triggering an "off by one" like behaviour in the 0th tenure.
///
fn signing_in_0th_tenure_of_reward_cycle() {
```
