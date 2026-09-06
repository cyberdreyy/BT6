### Title
Rejection tally silently breaks across blocks due to `signer_addr`-only primary key in `block_rejection_signer_addrs` — ([File: stacks-signer/src/signerdb.rs])

### Summary
`stacks-signer`'s local rejection-vote table, `block_rejection_signer_addrs`, is created with `PRIMARY KEY (signer_addr)` alone rather than a composite key over `(signer_signature_hash, signer_addr)` [1](#0-0) . Because the primary key is scoped to the signer address only, a signer address can hold at most one row in the *entire* table, across *every* block ever evaluated. This is structurally the same class of bug as the Qwik City report: a value meant to be namespaced/indexed per-context (per `signer_signature_hash`, i.e. per block) is instead collapsed onto a single shared key (`signer_addr`), so a legitimate later write for a different context silently collides with — and is rejected in favor of — the first one.

### Finding Description
`add_block_rejection_signer_addr` looks up an existing row filtered by **both** `signer_signature_hash` and `signer_addr` to decide whether to insert, no-op, or update [2](#0-1) . That two-column lookup will correctly return `None` the first time a given signer rejects a *second, different* block, because no row exists for that exact `(hash, addr)` pair yet. The code then falls into the `None => ... INSERT INTO block_rejection_signer_addrs (signer_signature_hash, signer_addr, reject_code) VALUES (...)` branch [3](#0-2) . But because the table's primary key is `signer_addr` alone, and that address already owns a row for the *first* block it rejected, this plain `INSERT` (not `INSERT OR REPLACE`) will violate the primary-key constraint and return a `DBError` from SQLite instead of inserting the new row.

The caller, `handle_block_rejection`, treats any `Err` from `add_block_rejection_signer_addr` as a soft failure — it only logs a warning and returns, never retrying or falling back to an update [4](#0-3) . The practical effect: for any signer address that has already rejected one block in this signer's local database, all *subsequent* rejections of *different* blocks by that same address are dropped and never recorded in `block_rejection_signer_addrs`.

This corrupts the equality the whole rejection-threshold logic depends on: `get_block_rejection_signer_addrs(block_hash)` is used to recompute `total_reject_weight` and decide whether `total_reject_weight.saturating_add(min_weight) > total_weight` (i.e., whether the 30%+ blocking minority has been reached) [5](#0-4) . If peer rejections silently fail to persist for any block after the first one a given address rejected, that computed rejection weight is permanently understated for every subsequent block in the signer's local view, and can never reach the true weight held by that signer.

### Impact Explanation
This is a state-machine wedge triggerable by a normal, single, one-slot miner: simply propose more than one invalid/rejected block in sequence (a completely ordinary, permissionless miner action requiring no elevated capability, majority, or another signer's key). Once any signer address has one recorded rejection anywhere in the local DB, that signer's vote can never again be correctly tallied against a new invalid block, degrading (and potentially permanently blocking) the local rejection-consensus decision (`mark_globally_rejected`) for every later block. This fits the "wedged state machine"/liveness category: it does not require a majority to trigger, and it silently and permanently corrupts vote counting rather than merely causing a log noise, satisfying the "miscounted response" bar in the rules.

### Likelihood Explanation
High reachability: any block rejection message is routed through `handle_block_rejection` → `add_block_rejection_signer_addr` unconditionally for every proposal a signer's peers reject [4](#0-3) . Two rejected blocks from the same tenure (or even across tenures within the same signer-db lifetime) by the same peer address are enough to trigger the collision. No special crafting beyond ordinary miner/proposal flow is required.

### Recommendation
Change the schema so `block_rejection_signer_addrs` has a composite primary key of `(signer_signature_hash, signer_addr)` (matching the two-column lookup already used in the query logic), and add a migration to correct any pre-existing table created with the single-column key. Ensure the insert path uses `INSERT OR REPLACE`/`ON CONFLICT DO UPDATE` on that composite key so it mirrors the existing update-vs-insert branching logic in `add_block_rejection_signer_addr` correctly, and propagate/surface `DBError`s from this path more loudly (e.g., panic or explicit metric) rather than treating them as an ignorable warning, since a silent failure here permanently corrupts consensus-relevant tallying.

### Proof of Concept
1. Signer S runs with a fresh `SignerDb`.
2. Miner proposes invalid block A (sighash `H1`). Peer signer P rejects it; S receives the rejection and calls `add_block_rejection_signer_addr(H1, P, code)`. Table has no row for `signer_addr = P`; row `(H1, P, code)` is inserted successfully.
3. Miner (same or later) proposes a second, different invalid block B (sighash `H2`, `H2 != H1`). Peer signer P also rejects B; S receives it and calls `add_block_rejection_signer_addr(H2, P, code2)`.
4. The lookup `WHERE signer_signature_hash = H2 AND signer_addr = P` returns `None` (no such combined row), so the code proceeds to the plain `INSERT INTO block_rejection_signer_addrs (H2, P, code2)`.
5. Because `PRIMARY KEY (signer_addr)` already contains a row for `P` (from step 2), this insert violates the primary-key constraint and returns a `DBError`.
6. `handle_block_rejection` in `signer.rs` only logs `warn!("{self}: Failed to save block rejection signature: {e:?}")` and returns, so P's rejection of block B is never recorded [6](#0-5) .
7. `get_block_rejection_signer_addrs(H2)` therefore never includes P's weight, permanently understating `total_reject_weight` for block B (and any further block rejected by P) in S's local tally, wedging the local rejection-threshold decision.

Note: I was unable to fully verify, within the available search budget, whether a later schema migration in `signerdb.rs` alters this table's primary key after its initial creation (i.e., whether `CREATE_BLOCK_REJECTION_SIGNER_ADDRS_TABLE` reflects the table's *final* on-disk shape or is superseded). If a later migration adds the composite key, this finding does not apply to current releases; I recommend a Devin session with full repository access to confirm the final effective schema before treating this as confirmed-exploitable.

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

**File:** stacks-signer/src/v0/signer.rs (L2278-2288)
```rust
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
```

**File:** stacks-signer/src/v0/signer.rs (L2295-2325)
```rust
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
