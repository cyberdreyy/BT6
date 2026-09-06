### Title
Undercounted block rejections due to malformed `PRIMARY KEY` on `block_rejection_signer_addrs` — a signer's rejection weight tally silently drops after the first distinct block it rejects - (File: `stacks-signer/src/signerdb.rs`)

### Summary
The `block_rejection_signer_addrs` table, which every signer uses to persist per-signer rejections and recompute the rejection-weight tally that decides whether *it, locally,* treats a block as globally rejected (and, on pre-global-state protocol versions, whether to invalidate a reorging miner), is created with `PRIMARY KEY (signer_addr)` instead of the composite key `(signer_signature_hash, signer_addr)` that every query against the table assumes.

### Finding Description
The table is declared as: [1](#0-0) 

```
CREATE TABLE IF NOT EXISTS block_rejection_signer_addrs (
    signer_signature_hash TEXT NOT NULL,
    signer_addr TEXT NOT NULL,
    PRIMARY KEY (signer_addr)
) STRICT;
```

Only `signer_addr` is the primary key, so SQLite enforces **at most one row per signer address in the entire table**, regardless of which block it refers to. Every call site, however, treats the table as if it were keyed by `(signer_signature_hash, signer_addr)`: [2](#0-1) 

`add_block_rejection_signer_addr` first checks for an existing row with `WHERE signer_signature_hash = ?1 AND signer_addr = ?2`. If a signer previously rejected a *different* block, this lookup returns `None` (no row matches both columns), so the function falls into the `None => { INSERT INTO block_rejection_signer_addrs ... }` branch. That plain `INSERT` (not `INSERT OR REPLACE`) collides with the existing row for that `signer_addr` from the earlier block and raises a `SQLITE_CONSTRAINT` violation, which propagates out of `self.db.execute(insert_qry, insert_args)?` as `Err(DBError)`.

The caller in `store_and_process_block_rejection` does not treat this as fatal: [3](#0-2) 

```
match self.signer_db.add_block_rejection_signer_addr(...) {
    Err(e) => {
        warn!("{self}: Failed to save block rejection signature: {e:?}",);
    }
    Ok(false) => return,
    Ok(true) => (),
}
...
let rejection_addrs = match self.signer_db.get_block_rejection_signer_addrs(block_hash) { ... };
```

Note that the `Err(e)` arm does **not** `return`; execution falls through to recompute the rejection tally from `get_block_rejection_signer_addrs(block_hash)` anyway, using data that is now missing this signer's vote for the current block (because the INSERT failed and the persisted row still points at the older block).

Net effect: for any given signer address, only the *first* block it ever rejects (across the lifetime of its signerdb) is durably recorded in `block_rejection_signer_addrs`. Every subsequent rejection of a different block by that same address fails to persist, and the failure is swallowed as a warning rather than aborting the tally computation. Once most/all signers have rejected at least one block over the node's lifetime, this table becomes permanently useless for tallying rejections of *new* blocks: `compute_signature_signing_weight` / `compute_reject_code_signing_weight` in `stacks-signer/src/v0/signer.rs` (lines 2175-2199, used at line 2306) will consistently undercount the rejecting weight for any new block.

### Impact Explanation
This breaks the equality "aggregated-weight vs verified-accepts" on the *rejection* side, and constitutes a liveness wedge on the signer's local decision-making:

- `mark_globally_rejected()` at signer.rs:2335 is only reached if `total_reject_weight` (derived from the corrupted table) crosses the blocking-minority threshold. Because rejecting signers' votes for new blocks silently fail to persist once they've rejected any prior block, a signer can perpetually fail to reach this threshold in its own local view even when actual rejecting weight in the network exceeds 30%, per [4](#0-3) .
- On protocol versions that don't use global state, the same undercounted `rejection_addrs` feeds `compute_reject_code_signing_weight(..., RejectReasonPrefix::ReorgNotAllowed)` at signer.rs:2354-2358, which decides whether the signer marks the current miner `InvalidatedBeforeFirstBlock` for an illegitimate reorg attempt. An undercounted tally means a signer can fail to invalidate a miner that is actually rejected by a blocking (>30%) minority for reorg reasons — a signer failing to act on a rejection that should have crossed the safety threshold, i.e., "a rejection recounted as accept" for that signer's own local safety check.
- This is a per-signer local-state corruption (not a network-wide forged signature), but it directly undermines the correctness of the reject-tally equality every signer relies on for both marking a block globally rejected and for invalidating a reorging miner.

### Likelihood Explanation
Trivially reachable with normal signer operation and no attacker privileges beyond being an ordinary participating signer (or observing the natural behavior of the network): any signer that rejects two different blocks over its lifetime (a routine occurrence — competing/duplicate proposals, tenure-start races, malformed proposals, etc., are exercised throughout `stacks-signer/src/v0/tests.rs` and `stacks-node/src/tests/signer/v0/*`) will trip this bug the second time it rejects a distinct block. No majority of colluding signers, no key compromise, and no crafted malicious message are required — it is a self-inflicted schema bug reachable purely by normal chain activity (competing proposals, forks, reorgs) that a one-slot miner can trivially trigger by causing a signer to reject two different blocks in sequence.

### Recommendation
Change the table's primary key to the composite `(signer_signature_hash, signer_addr)` (matching every query in `add_block_rejection_signer_addr` / `get_block_rejection_signer_addrs`), add a migration to correct existing deployed databases, and make `store_and_process_block_rejection` treat `Err` from `add_block_rejection_signer_addr` as fatal for that rejection (i.e., `return` instead of falling through to recompute the tally on stale data), so silent persistence failures cannot corrupt the rejection-weight decision.

### Proof of Concept
1. Start a signer with a fresh `signerdb`.
2. Signer rejects block A (sighash `H_A`) from address `S`: `add_block_rejection_signer_addr(H_A, S, reason1)` succeeds, inserting `(H_A, S, reason1)`.
3. A different, unrelated block B (sighash `H_B`, e.g., a competing/duplicate tenure-start proposal — routinely produced, as exercised in `run_sibling_scenario` in `stacks-signer/src/v0/tests.rs`) is proposed and the same signer `S` rejects it too.
4. `add_block_rejection_signer_addr(H_B, S, reason2)` queries `WHERE signer_signature_hash = H_B AND signer_addr = S` → no match → falls into the `None` branch → attempts `INSERT INTO block_rejection_signer_addrs (H_B, S, reason2)` → violates `PRIMARY KEY (signer_addr)` since `S` already owns a row for `H_A` → returns `Err(DBError)`.
5. `store_and_process_block_rejection` logs a warning but continues, calling `get_block_rejection_signer_addrs(H_B)`, which does **not** include signer `S`'s vote (the insert never happened) — under-tallying the rejection weight for block B, and repeating for every block after the signer's first-ever rejection. [5](#0-4) 
The existing unit test `reject_then_accept` only exercises reject→accept transitions on the *same* block hash and does not cover a signer rejecting two *different* block hashes, which is why this schema defect went uncaught.

### Citations

**File:** stacks-signer/src/signerdb.rs (L514-524)
```rust
static CREATE_BLOCK_REJECTION_SIGNER_ADDRS_TABLE: &str = r#"
CREATE TABLE IF NOT EXISTS block_rejection_signer_addrs (
    -- The block sighash commits to all of the stacks and burnchain state as of its parent,
    -- as well as the tenure itself so there's no need to include the reward cycle.  Just
    -- the sighash is sufficient to uniquely identify the block across all burnchain, PoX,
    -- and stacks forks.
    signer_signature_hash TEXT NOT NULL,
    -- the signer address that rejected the block
    signer_addr TEXT NOT NULL,
    PRIMARY KEY (signer_addr)
) STRICT;"#;
```

**File:** stacks-signer/src/signerdb.rs (L1922-1985)
```rust
    /// Record an observed block rejection_signature
    pub fn add_block_rejection_signer_addr(
        &self,
        block_sighash: &Sha512Trunc256Sum,
        addr: &StacksAddress,
        reject_reason: RejectReasonPrefix,
    ) -> Result<bool, DBError> {
        // If this signer/block already has a signature, do not allow a rejection
        let sig_qry = "SELECT EXISTS(SELECT 1 FROM block_signatures WHERE signer_signature_hash = ?1 AND signer_addr = ?2)";
        let sig_args = params![block_sighash, addr.to_string()];
        let exists = self.db.query_row(sig_qry, sig_args, |row| row.get(0))?;
        if exists {
            warn!("Cannot add block rejection because a signature already exists.";
                "signer_signature_hash" => %block_sighash,
                "signer_address" => %addr,
                "reject_reason" => ?reject_reason
            );
            return Ok(false);
        }

        // Check if a row exists for this sighash/signer combo
        let qry = "SELECT reject_code FROM block_rejection_signer_addrs WHERE signer_signature_hash = ?1 AND signer_addr = ?2 LIMIT 1";
        let args = params![block_sighash, addr.to_string()];
        let existing_code: Option<i64> =
            self.db.query_row(qry, args, |row| row.get(0)).optional()?;

        let reject_code = reject_reason as i64;

        match existing_code {
            Some(code) if code == reject_code => {
                // Row exists with same reject_reason, do nothing
                debug!("Duplicate block rejection.";
                    "signer_signature_hash" => %block_sighash,
                    "signer_address" => %addr,
                    "reject_reason" => ?reject_reason
                );
                Ok(false)
            }
            Some(_) => {
                // Row exists but with different reject_reason, update it
                let update_qry = "UPDATE block_rejection_signer_addrs SET reject_code = ?1 WHERE signer_signature_hash = ?2 AND signer_addr = ?3";
                let update_args = params![reject_code, block_sighash, addr.to_string()];
                self.db.execute(update_qry, update_args)?;
                debug!("Updated block rejection reason.";
                    "signer_signature_hash" => %block_sighash,
                    "signer_address" => %addr,
                    "reject_reason" => ?reject_reason
                );
                Ok(true)
            }
            None => {
                // Row does not exist, insert it
                let insert_qry = "INSERT INTO block_rejection_signer_addrs (signer_signature_hash, signer_addr, reject_code) VALUES (?1, ?2, ?3)";
                let insert_args = params![block_sighash, addr.to_string(), reject_code];
                self.db.execute(insert_qry, insert_args)?;
                debug!("Inserted block rejection.";
                    "signer_signature_hash" => %block_sighash,
                    "signer_address" => %addr,
                    "reject_reason" => ?reject_reason
                );
                Ok(true)
            }
        }
    }
```

**File:** stacks-signer/src/signerdb.rs (L3234-3263)
```rust
    #[test]
    fn reject_then_accept() {
        let db_path = tmp_db_path();
        let db = SignerDb::new(db_path).expect("Failed to create signer db");

        let block_id = Sha512Trunc256Sum::from_data("foo".as_bytes());
        let address = StacksAddress::burn_address(false);
        let sig1 = MessageSignature([0x11; 65]);

        assert_eq!(db.get_block_signatures(&block_id).unwrap(), vec![]);

        assert!(db
            .add_block_rejection_signer_addr(
                &block_id,
                &address,
                RejectReasonPrefix::InvalidParentBlock
            )
            .unwrap());
        assert_eq!(
            db.get_block_rejection_signer_addrs(&block_id).unwrap(),
            vec![(address.clone(), RejectReasonPrefix::InvalidParentBlock)]
        );

        assert!(db.add_block_signature(&block_id, &address, &sig1).unwrap());
        assert_eq!(db.get_block_signatures(&block_id).unwrap(), vec![sig1]);
        assert!(db
            .get_block_rejection_signer_addrs(&block_id)
            .unwrap()
            .is_empty());
    }
```

**File:** stacks-signer/src/v0/signer.rs (L2274-2296)
```rust
    ) {
        let block_hash = &block_info.signer_signature_hash();
        // We should still store signatures even on consensus reached blocks for auditing purposes.
        // signature is valid! store it
        match self.signer_db.add_block_rejection_signer_addr(
            block_hash,
            signer_address,
            reject_reason,
        ) {
            Err(e) => {
                warn!("{self}: Failed to save block rejection signature: {e:?}",);
            }
            Ok(false) => return, // We already have this signature, do not process it again.
            Ok(true) => (),
        }

        if block_info.has_reached_consensus() {
            // Checking the rejection signatures is pointless. We have already reached consensus on this block.
            return;
        }

        // do we have enough signatures to mark a block a globally rejected?
        // i.e. is (set-size) - (threshold) + 1 reached.
```

**File:** stacks-signer/src/v0/signer.rs (L2305-2325)
```rust
        let total_reject_weight =
            self.compute_signature_signing_weight(rejection_addrs.iter().map(|(addr, _)| addr));
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
        if total_reject_weight.saturating_add(min_weight) <= total_weight {
            // Not enough rejection signatures to make a decision
            info!("{self}: Have not yet received enough block rejections to reach a consensus decision on this block";
                "signer_signature_hash" => %block_hash,
                "signature_weight" => signature_weight,
                "consensus_hash" => %block_info.block.header.consensus_hash,
                "block_height" => block_info.block.header.chain_length,
                "total_weight_rejected" => total_reject_weight,
                "total_weight" => total_weight,
                "percent_rejected" => (total_reject_weight as f64 / total_weight as f64 * 100.0),
            );
            return;
        }
```
