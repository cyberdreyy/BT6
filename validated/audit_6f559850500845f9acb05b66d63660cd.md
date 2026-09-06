### Title
Rejection tracking keyed only on `signer_addr` instead of `(signer_signature_hash, signer_addr)` — same identifier-collision bug class as the reported "canonical asset keyed on id, not domain+id" issue - (File: `stacks-signer/src/signerdb.rs`)

### Summary
The `block_rejection_signer_addrs` table is declared with `PRIMARY KEY (signer_addr)` only, while every read/write path (`add_block_rejection_signer_addr`, `get_block_rejection_signer_addrs`) is written as if the table were keyed on the compound tuple `(signer_signature_hash, signer_addr)`. This mirrors the report's root cause exactly: a mapping meant to be keyed on a compound identifier (domain+id / block-hash+signer) is actually keyed on only one component (id / signer_addr), so distinct contexts (different canonical assets / different blocks) collide on the same storage slot.

### Finding Description
The table is created as: [1](#0-0) 

but `add_block_rejection_signer_addr` always looks up and writes using the pair `(signer_signature_hash, signer_addr)`: [2](#0-1) 

and `get_block_rejection_signer_addrs` filters by `signer_signature_hash` alone, implicitly assuming several distinct `signer_addr` rows can coexist per hash and that a given `signer_addr` can have independent rows for different hashes: [3](#0-2) 

Because the actual `PRIMARY KEY` constraint is `signer_addr` alone, SQLite enforces global uniqueness on `signer_addr` across *all* blocks, not per-block. The code's own existence check (`SELECT ... WHERE signer_signature_hash = ?1 AND signer_addr = ?2`) will return `None` whenever the same signer has previously rejected a *different* block (different `signer_signature_hash`), so the code falls into the `INSERT INTO` branch. That plain `INSERT` (not `INSERT OR REPLACE`) then collides with the pre-existing row for that `signer_addr` from the earlier, unrelated block and violates the `PRIMARY KEY` constraint, causing the insert to fail with a SQLite constraint-violation error rather than the intended per-block bookkeeping.

This is the same bug class as the audited report: an identifier meant to disambiguate two independent contexts (canonical asset domain+id vs. rejection's block-hash+signer) is collapsed to a single component, so the second context's write silently collides with/overwrites (or here, is rejected because of) state belonging to the first context.

### Impact Explanation
`add_block_rejection_signer_addr` is called whenever this signer records any signer's rejection of a block proposal (including its own), which is core to the rejection-tallying/bookkeeping used by `get_block_rejection_signer_addrs` (used to decide re-evaluation behavior and reporting, see `should_reevaluate_reject_reason` referenced in `docs/signer-flows.md`). Once a given signer address has rejected any one block, ordinary chain progress (a subsequent, unrelated block from that same address being rejected for any reason — reorg, timeout, different tenure, etc.) causes the write for the *new* rejection to fail. Depending on how the caller propagates this `DBError` (not fully traceable within the available tool budget), this either:
- silently drops the new rejection record, corrupting per-block rejection bookkeeping used to decide whether to re-evaluate a proposal, or
- propagates as an unhandled error into the signer's event-processing loop, which is a liveness risk (a wedge preventing further correct processing until intervention).

Both outcomes fall inside the requested impact categories: a miscounted/lost rejection response, or a signer wedged from correctly processing further block votes. I was not able to fully trace the exact downstream error-handling of the `Result` returned by `add_block_rejection_signer_addr` within this pass, so the precise severity (silent bookkeeping corruption vs. hard failure) needs confirmation by tracing all call sites in `stacks-signer/src/v0/signer.rs` (`handle_block_rejection`) and `stacks-node/src/tests/signer/v0/reorg.rs`.

### Likelihood Explanation
This requires no majority and no privileged access — it is triggered purely by the normal, expected behavior of a single signer rejecting two different block proposals over the lifetime of the signer process (which will happen routinely as tenures/forks proceed), i.e., exactly a "one-slot miner plus gossip" scenario as scoped.

### Recommendation
Change the `block_rejection_signer_addrs` table's primary key to the compound `(signer_signature_hash, signer_addr)` (matching how the code already queries it), and use `INSERT OR REPLACE`/`ON CONFLICT` semantics consistent with the existing "update if different reason" logic in `add_block_rejection_signer_addr`.

### Proof of Concept
1. Call `SignerDb::add_block_rejection_signer_addr(hash_A, addr_X, reason)` — row inserted successfully (`signer_addr = addr_X` is the only row for that address).
2. Call `SignerDb::add_block_rejection_signer_addr(hash_B, addr_X, reason)` for a different block hash `hash_B` and the same `addr_X`.
3. The existence check at [4](#0-3)  finds no row for `(hash_B, addr_X)` and falls through to the `INSERT INTO` at [5](#0-4) , which fails with a `PRIMARY KEY` (`signer_addr`) constraint violation because a row for `addr_X` already exists from step 1.

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

**File:** stacks-signer/src/signerdb.rs (L1942-1976)
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
```

**File:** stacks-signer/src/signerdb.rs (L1987-1994)
```rust
    /// Get all signer addresses that rejected the block (and their reject codes)
    pub fn get_block_rejection_signer_addrs(
        &self,
        block_sighash: &Sha512Trunc256Sum,
    ) -> Result<Vec<(StacksAddress, RejectReasonPrefix)>, DBError> {
        let qry =
            "SELECT signer_addr, reject_code FROM block_rejection_signer_addrs WHERE signer_signature_hash = ?1";
        let args = params![block_sighash];
```
