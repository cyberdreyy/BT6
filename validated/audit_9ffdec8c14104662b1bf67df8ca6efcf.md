### Title
Global rejection-vote bookkeeping collapses across distinct blocks due to a signer-address-only primary key - (File: `stacks-signer/src/signerdb.rs`)

### Summary
The `block_rejection_signer_addrs` table, which is meant to record "this signer address rejected this specific block" (analogous in comment style to the compound `signer_signature_hash + signer_addr` keys used elsewhere in the same file, e.g. `block_signatures`/`add_block_signature`), is created with `PRIMARY KEY (signer_addr)` only, instead of a compound key over `(signer_signature_hash, signer_addr)`. [1](#0-0) 

Because the primary key is `signer_addr` alone, the table can hold at most **one row per signer address, globally**, no matter how many distinct blocks that signer has rejected. This directly contradicts the schema's stated purpose (the sighash comment explicitly says the hash "uniquely identif[ies] the block across all burnchain, PoX, and stacks forks", implying the row should be scoped per block+signer, exactly like the sibling `block_signatures` table is scoped per signature).

### Finding Description
The equality this table is supposed to maintain is: *"aggregated rejection weight for block X == sum of weights of signer addresses whose rejection-for-X row exists in `block_rejection_signer_addrs`."* Any lookup done with `WHERE signer_signature_hash = ?1` (as seen in the analogous `get_pending_rejection_responses` query pattern for the sibling `signer_pending_rejection_responses` table) depends on that row still being present for block X. [2](#0-1) 

Because the primary key is `signer_addr` only, once signer `S` rejects block `X` and a row `(X, S)` is inserted, if `S` later rejects a *different* block `Y` (a normal, expected sequence for a live one-slot miner producing a fresh proposal, or a signer processing a sibling/rival block at the same height), the second insert collides on the `signer_addr` primary key. Depending on how the insert is issued (`INSERT OR REPLACE` vs. plain `INSERT`), one of two safety-relevant outcomes occurs:

- If it is an upsert (`INSERT OR REPLACE`), the row for `(X, S)` is silently deleted and replaced by `(Y, S)`. Any subsequent read of "who rejected X" undercounts `S`'s rejection, reducing the aggregated rejection weight for `X` below what was actually cast.
- If it is a plain `INSERT`, the write for `(Y, S)` fails/aborts (a `PRIMARY KEY` STRICT-table conflict), meaning `S`'s vote against `Y` is never recorded even though `S` genuinely rejected it — while `S`'s rejection is falsely "pinned" to a stale block `X` that may already be moot.

Either way, the table's per-address bookkeeping does not reflect the actual per-block set of rejecting signers, corrupting the >30% rejection-weight tally that the flow document describes as making 70% acceptance impossible and finalizing a block as globally rejected. [3](#0-2) 

This is the signer-side analog of the reported bug class: instead of disk space silently growing from unmeasured/unbounded state, here the state that is supposed to grow per-(block, signer) is silently *collapsed* per-signer, causing the aggregated rejection weight to be miscounted for a block after a single signer address participates in more than one distinct block's rejection — exactly the "aggregated-weight vs verified-rejects" equality the analysis rules call out.

### Impact Explanation
A miscounted rejection tally is safety-relevant: if a block `X` had genuinely crossed the >30% blocking threshold and is later "recounted" downward because one of its rejecting signers subsequently rejects a different block `Y`, the aggregate view of `X`'s rejection weight can silently drop, undermining the guarantee that a globally-rejected block stays rejected. Conversely, a signer's legitimate rejection of `Y` can be lost entirely, meaning `Y`'s true rejection weight is undercounted, which could let `Y`'s acceptance-side threshold calculations proceed on an incomplete view of dissent. This falls under the "a rejection recounted as an accept" impact category defined in scope, since the practical effect is that a previously cast rejection vote silently vanishes from a block's aggregate tally.

### Likelihood Explanation
This requires no majority collusion, no key compromise, and no auth token — it only requires the ordinary, expected sequence of one-slot miners proposing distinct blocks over time (including sibling/rival proposals at forks or tenure changes), which is a normal part of chain operation that every signer's rejection logic must already handle. Any signer address that rejects more than one block over its lifetime — which is the common case, not an edge case — triggers the primary-key collision.

### Recommendation
Change `CREATE_BLOCK_REJECTION_SIGNER_ADDRS_TABLE`'s primary key to the compound key `(signer_signature_hash, signer_addr)`, matching the design intent stated in the table's own comment and mirroring the compound-key pattern already used for other per-block-per-signer bookkeeping in this file (e.g., `block_signatures`). A migration should also be added to preserve/re-key any existing single-row-per-address data, and all call sites that insert into this table should be audited to confirm they were not relying on the old single-row-per-signer semantics.

### Proof of Concept
1. Signer `S` receives block proposal `X` at height `h`, rejects it; the signer inserts `(signer_signature_hash = X, signer_addr = S)` into `block_rejection_signer_addrs`.
2. A one-slot miner (or a natural fork/tenure-change) later produces a distinct block proposal `Y` — no relation to `X` required beyond both being ordinary proposals `S` independently evaluates and rejects.
3. `S` rejects `Y` too, and the signer attempts to insert `(signer_signature_hash = Y, signer_addr = S)`.
4. Because `PRIMARY KEY (signer_addr)` only constrains on `S`, this second insert either overwrites the `(X, S)` row (deleting evidence that `S` rejected `X`) or fails outright (dropping evidence that `S` rejected `Y`).
5. Any later query such as `SELECT ... FROM block_rejection_signer_addrs WHERE signer_signature_hash = X` now omits `S`, undercounting `X`'s aggregated rejection weight versus what was actually cast — breaking the equality between "verified rejects recorded" and "aggregated rejection weight used for the >30% global-rejection determination." [1](#0-0)

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

**File:** stacks-signer/src/signerdb.rs (L2848-2867)
```rust
    fn get_pending_rejection_responses(
        db: &SignerDb,
        block_sighash: &Sha512Trunc256Sum,
    ) -> Result<Vec<(StacksAddress, RejectReasonPrefix)>, DBError> {
        let qry = "SELECT signer_addr, reject_code FROM signer_pending_rejection_responses WHERE signer_signature_hash = ?1 ORDER BY received_time DESC";
        let args = params![block_sighash.to_string()];

        let mut stmt = db.db.prepare(qry)?;
        let rows = stmt.query_map(args, |row| {
            let addr_str: String = row.get(0)?;
            let reject_code: u8 = row.get(1)?;
            let addr = StacksAddress::from_string(&addr_str).ok_or(
                SqliteError::InvalidColumnType(0, addr_str.clone(), rusqlite::types::Type::Text),
            )?;
            let reject_reason = RejectReasonPrefix::from(reject_code);
            Ok((addr, reject_reason))
        })?;

        rows.collect::<Result<Vec<_>, _>>().map_err(DBError::from)
    }
```

**File:** docs/signer-flows.md (L349-355)
```markdown
## 6. Responses from other signers

Peer acceptances and rejections drive the two consensus outcomes. Acceptances
tally toward the 70% signing threshold and reaching it makes _this_ signer
assemble the signature set and push the block to its node. Rejections tally
toward the blocking minority (>30%), which makes the 70% unreachable and
finalizes the block as globally rejected.
```
