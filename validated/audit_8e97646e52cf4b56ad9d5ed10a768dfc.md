### Title
Block-rejection ledger keyed only by `signer_addr` collapses cross-block rejection tracking, wedging the reject-threshold state machine - (File: `stacks-signer/src/signerdb.rs`)

### Summary
The `block_rejection_signer_addrs` table, which every signer uses to persist and tally the rejections it observes from all signers (including its own), is declared with `PRIMARY KEY (signer_addr)` only, instead of the composite `(signer_signature_hash, signer_addr)` that the surrounding query logic assumes. Once any signer address has a rejection row recorded for one block, any later `INSERT` recording that same address's rejection of a *different* block violates the primary key and fails. The failure is only logged, not propagated as a fatal error, so the caller proceeds to tally the reject weight for the new block *without* that signer's vote — permanently, for the lifetime of the signerdb, unless that specific old row happens to get deleted by the address later signing the *original* block it was tied to.

### Finding Description
The table is defined as: [1](#0-0) 

```
CREATE TABLE IF NOT EXISTS block_rejection_signer_addrs (
    signer_signature_hash TEXT NOT NULL,
    signer_addr TEXT NOT NULL,
    PRIMARY KEY (signer_addr)
) STRICT;
```

`signer_addr` alone is the primary key/unique constraint — there is no unique index on `(signer_signature_hash, signer_addr)`.

`add_block_rejection_signer_addr` however queries and inserts as if the key were the pair `(signer_signature_hash, signer_addr)`: [2](#0-1) 

It looks up `existing_code` by `WHERE signer_signature_hash = ?1 AND signer_addr = ?2`. If that specific `(hash, addr)` pair is not found (which is the normal case for a *different* block than any previous rejection by that address), it falls into the `None` branch and executes a plain `INSERT INTO block_rejection_signer_addrs (signer_signature_hash, signer_addr, reject_code) VALUES (?1, ?2, ?3)`. Because the real primary key is `signer_addr` alone, this `INSERT` fails with a `PRIMARY KEY` constraint violation as soon as that `signer_addr` already owns a row for *any other* block hash, and the `?` operator turns that into an `Err(DBError)`.

The only code path that clears a row is `add_block_signature`, which deletes by the exact `(signer_signature_hash, signer_addr)` pair when that address later *signs* that specific block: [3](#0-2) 

If the address never signs the original block (e.g., it stays rejected, or the block is abandoned), its row is never cleared, and it permanently blocks storage of that address's rejections for every other block for the rest of the signerdb's life.

The caller, `store_and_process_block_rejection`, treats the `Err` case as non-fatal and keeps going, meaning it silently tallies rejection weight *without* the signer's vote that just failed to persist: [4](#0-3) 

```
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
...
let rejection_addrs = match self.signer_db.get_block_rejection_signer_addrs(block_hash) { ... };
let total_reject_weight =
    self.compute_signature_signing_weight(rejection_addrs.iter().map(|(addr, _)| addr));
```

Because there is no early `return` in the `Err(e)` branch, the function continues to compute `total_reject_weight` and potentially decide `mark_globally_rejected` using an incomplete/undercounted set of rejection addresses.

### Impact Explanation
Every signer address's *first-ever* recorded rejection (of any block, at any height, in any reward cycle, since this table has no reward-cycle scoping) permanently "claims" that address's single row in the table. From that point forward, whenever that address rejects any *other* block, the write silently fails everywhere (on every peer signer's local signerdb that processes that `BlockResponse::Rejected` message), and that address's vote is dropped from the reject tally for the new block. Aggregated across the signer set, the true 30%+1 blocking-minority weight for later invalid/conflicting blocks becomes systematically under-counted, which can prevent the `GloballyRejected` transition from ever being reached (`BlockInfo::move_to` in `check_state`, `mark_globally_rejected`) even though the real signer population has in fact voted to reject. This wedges the block state machine in a non-terminal state (`Unprocessed`/`LocallyRejected` forever) for bad blocks, and denies the miner/coordinator/node a definitive rejection signal, degrading the liveness of the reject-consensus mechanism described in the signer-flows.md tallying diagram (section 6, `handle_block_rejection` / `store_and_process_block_rejection`). This matches the "High" impact class: a wedge of the signing/rejection state machine caused by mundane, un-coordinated signer activity via ordinary StackerDB gossip, requiring no majority or key compromise — it triggers organically the moment any two blocks are ever rejected by the same signer address across the node's lifetime.

### Likelihood Explanation
This does not require a majority, malicious miner, or key compromise — it fires under completely normal signer operation. Any single signer address that rejects more than one distinct block over the lifetime of its signerdb (a routine occurrence — reward cycle changes, tenure forks, sibling blocks, aborted proposals, etc., are all in the reject flow described in `docs/signer-flows.md`) will have all its *subsequent* rejections across different block hashes silently fail to persist on every peer that ingests its `BlockResponse::Rejected` gossip message. Given block rejections are common in normal chain operation (timeouts, reorg denials, duplicate blocks, invalid transactions), this is highly likely to occur in practice, not merely a theoretical edge case.

### Recommendation
Change the primary key of `block_rejection_signer_addrs` to the composite `(signer_signature_hash, signer_addr)` (matching the intent already encoded in every query against it), add a migration to correct existing databases, and make `store_and_process_block_rejection` treat a genuine DB error from `add_block_rejection_signer_addr` as fatal to further processing of that message (i.e., `return` on `Err`, not just `warn!` and continue), so that a persistence failure cannot silently corrupt the reject-weight tally.

### Proof of Concept
1. Signer `S` rejects block `A` (hash `H_A`) for any legitimate reason. Every peer signer calls `add_block_rejection_signer_addr(H_A, S, reason)`, which inserts row `(H_A, S, reason)` — succeeds, because the table has no existing row for `S`.
2. Time passes; block `A` is superseded/abandoned (never signed by `S`), so the row is never deleted (deletion only happens via `add_block_signature(H_A, S, sig)`, which never runs for an abandoned block).
3. Later, `S` legitimately rejects an unrelated block `B` (hash `H_B`, different tenure/height). Every peer signer receives the `BlockResponse::Rejected` gossip and calls `add_block_rejection_signer_addr(H_B, S, reason)`. The lookup `WHERE signer_signature_hash = H_B AND signer_addr = S` returns no row (existing row is keyed to `H_A`), so it goes to the `INSERT` branch: `INSERT INTO block_rejection_signer_addrs (H_B, S, reason)` — this violates the `PRIMARY KEY (signer_addr)` constraint because `S` already owns a row (for `H_A`), returning `Err`.
4. In `store_and_process_block_rejection`, the `Err(e)` branch only logs a warning and does not return; execution proceeds to `get_block_rejection_signer_addrs(H_B)`, which does **not** include `S`'s vote (the insert failed), so `total_reject_weight` for block `B` is computed short by `S`'s weight on every peer signer that has ever seen `S` reject a different block before.
5. Repeated across the signer set as more signers accumulate one-time rejections over the network's lifetime, the aggregate ability to reach the 30%+1 rejection threshold for any subsequent block degrades, and previously-rejecting signer addresses become permanently unable to have further rejections recorded/tallied — wedging the `BlockState` reject path.

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

**File:** stacks-signer/src/signerdb.rs (L1870-1889)
```rust
    /// Record an observed block signature
    pub fn add_block_signature(
        &self,
        block_sighash: &Sha512Trunc256Sum,
        signer_addr: &StacksAddress,
        signature: &MessageSignature,
    ) -> Result<bool, DBError> {
        // Remove any block rejection entry for this signer and block hash
        let del_qry = "DELETE FROM block_rejection_signer_addrs WHERE signer_signature_hash = ?1 AND signer_addr = ?2";
        let del_args = params![block_sighash, signer_addr.to_string()];
        self.db.execute(del_qry, del_args)?;

        // Insert the block signature
        let qry = "INSERT OR IGNORE INTO block_signatures (signer_signature_hash, signer_addr, signature) VALUES (?1, ?2, ?3);";
        let args = params![
            block_sighash,
            signer_addr.to_string(),
            serde_json::to_string(signature).map_err(DBError::SerializationError)?
        ];
        let rows_added = self.db.execute(qry, args)?;
```

**File:** stacks-signer/src/signerdb.rs (L1942-1984)
```rust
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
```

**File:** stacks-signer/src/v0/signer.rs (L2267-2306)
```rust
    // Store the block rejection signature and check if we have reached a consensus decision on the block because of it. If we have, update the block state accordingly.
    fn store_and_process_block_rejection(
        &mut self,
        sortition_state: &mut Option<SortitionsView>,
        block_info: &mut BlockInfo,
        signer_address: &StacksAddress,
        reject_reason: RejectReasonPrefix,
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
        let rejection_addrs = match self.signer_db.get_block_rejection_signer_addrs(block_hash) {
            Ok(addrs) => addrs,
            Err(e) => {
                warn!("{self}: Failed to load block rejection addresses: {e:?}.",);
                return;
            }
        };
        let signature_weight = self.signer_weights.get(signer_address).unwrap_or(&0);
        let total_reject_weight =
            self.compute_signature_signing_weight(rejection_addrs.iter().map(|(addr, _)| addr));
```
