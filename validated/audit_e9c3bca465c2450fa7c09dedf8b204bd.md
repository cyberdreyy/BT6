### Title
Rejection-tally corruption via primary-key collision in `block_rejection_signer_addrs` - ([File: stacks-signer/src/signerdb.rs])

### Summary
The Keycloak advisory is a class of bug where an attacker-controlled identifier (`organization.alias`) is stored and later reused in a context whose invariants assume it is inert/scoped, letting one write corrupt behavior for unrelated future readers. The structural analog here is a SQLite table whose primary key is scoped too coarsely: `block_rejection_signer_addrs` is keyed only by `signer_addr`, not by `(signer_signature_hash, signer_addr)`, even though the table stores a per-block rejection record. A signer address can only ever have one row in this table system-wide, so a signer rejecting a second, unrelated block silently collides with (and can overwrite) its rejection record for a previously rejected block, corrupting the aggregated rejection weight the signer set relies on to reach `GloballyRejected` consensus on the earlier block.

### Finding Description
The table is declared with a global primary key on the voter address alone: [1](#0-0) 

Every other per-block vote/commitment table in this file scopes its primary key to the block hash (e.g. `block_signatures` keys on the signature itself, which is unique per act of signing), but `block_rejection_signer_addrs` keys only on `signer_addr`. This means the table can hold at most one rejection row per signer address for the *entire database*, regardless of how many distinct blocks (different heights, different tenures, or competing sibling blocks at the same height) that signer has rejected over its lifetime.

The tally logic that depends on this table computes the rejection weight used to decide whether a block should be marked `GloballyRejected`: [2](#0-1) 

The call site treats the insert as an idempotency check — `Ok(false)` means "already recorded, skip"; any other outcome falls through to re-tally `get_block_rejection_signer_addrs(block_hash)` and compare against `min_weight`/`total_weight` to decide `mark_globally_rejected`. Because the row is keyed only on `signer_addr`, when the *same* signer later rejects a *different* block, the insert either collides and is ignored (silently dropping the new block's vote from its own tally) or replaces the existing row's `signer_signature_hash` (silently erasing that voter from the earlier block's tally). Either way, the equality the state machine assumes — "the rejection weight I compute now for block X equals the sum of every distinct rejection actually cast for block X" — breaks the moment one signer address rejects two different blocks, which is an ordinary, single-miner-triggerable event (a miner proposing two blocks that a rejecting signer independently rejects, e.g. an invalid block followed by a legitimate re-proposal, or two competing forks at the same height).

### Impact Explanation
This corrupts the aggregated-weight-vs-verified-accepts equality (here, aggregated-weight-vs-verified-rejects) that gates `mark_globally_rejected`. A block that genuinely collected enough independent rejection weight to be globally rejected can have that weight silently understated because one voter's row got reassigned to a later block, delaying or preventing `GloballyRejected` from ever being reached and leaving the block state machine wedged waiting on a threshold that in reality was already met — a liveness wedge in the rejection path, and it also causes the *other* block's tally to be inflated with a phantom carried-over vote it never separately verified for that specific block hash, i.e., a rejection recorded against the wrong block. This is triggerable without any majority collusion — one signer, having rejected one block earlier in its normal operation, is enough to desynchronize the tally the next time it rejects any other block.

### Likelihood Explanation
High: any signer that rejects more than one block during its runtime (a routine occurrence — invalid proposals, reorg attempts, or competing forks are rejected regularly) will trigger this primary-key collision. No coordination, majority, or special privileges are required; only that the same signer address has previously rejected a different block.

### Recommendation
Change the primary key of `block_rejection_signer_addrs` to `(signer_signature_hash, signer_addr)` so each signer's rejection is scoped per block, matching the pattern used by other per-block vote tables, and add a migration to repair/re-key existing data.

### Proof of Concept
1. Signer `S` rejects block `A` (hash `H_A`) → row `(signer_signature_hash=H_A, signer_addr=S)` inserted, `add_block_rejection_signer_addr` returns `Ok(true)`.
2. Later, `S` rejects a different block `B` (hash `H_B`, e.g. a re-proposal or competing sibling at the same height) → the `signer_addr=S` primary key collides with the row from step 1; the insert either silently no-ops (row remains pointing at `H_A`, so `H_B`'s tally never counts `S`'s rejection) or overwrites the row to point at `H_B` (so `H_A`'s tally silently loses `S`'s rejection on the next `get_block_rejection_signer_addrs(H_A)` read).
3. Any subsequent re-tally of `H_A` (e.g., on signer restart, replay of pending responses, or another late rejection triggering the check in `handle_block_rejection` at [3](#0-2)  ) undercounts `H_A`'s true rejection weight, potentially keeping a block that already crossed the 30%+ rejection threshold from ever being marked `GloballyRejected`.

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

**File:** stacks-signer/src/v0/signer.rs (L2278-2325)
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
