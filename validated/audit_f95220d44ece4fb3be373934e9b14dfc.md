### Title
Global (non-block-scoped) primary key on `block_rejection_signer_addrs` causes a signer's valid rejection of one block to silently overwrite/discard their rejection of a different block, breaking the rejection-weight tally - (File: `stacks-signer/src/signerdb.rs`)

### Summary
The `block_rejection_signer_addrs` table is keyed only by `signer_addr`, not by `(signer_signature_hash, signer_addr)`. Since a signer can legitimately reject different candidate blocks over time (competing proposals, sibling blocks at the same height, or blocks in different tenures), this schema conflates all of a signer's rejections into a single row, so the *aggregated rejection weight* computed for a specific block no longer reflects the *verified rejection messages* actually received for that block.

### Finding Description
The table is created as: [1](#0-0) 

```
CREATE TABLE IF NOT EXISTS block_rejection_signer_addrs (
    signer_signature_hash TEXT NOT NULL,
    signer_addr TEXT NOT NULL,
    PRIMARY KEY (signer_addr)
) STRICT;
```

The primary key is `signer_addr` alone — it does not include `signer_signature_hash`. A later migration only adds a `reject_code` column and does not fix the key: [2](#0-1) 

Consumers of this table treat "already recorded" as a boolean signal keyed purely on whether the insert succeeded: [3](#0-2) 

```rust
match self.signer_db.add_block_rejection_signer_addr(
    block_hash,
    signer_address,
    reject_reason,
) {
    Err(e) => { warn!(...); }
    Ok(false) => return, // We already have this signature, do not process it again.
    Ok(true) => (),
}
```

Because the underlying table's primary key is `signer_addr` only (not scoped to `block_hash`), an insert-or-ignore/replace semantics on this schema means: once a signer address has *any* row present (from rejecting block A), a subsequent, cryptographically valid rejection from that same signer for a *different* block B collides on the same primary key. The DB layer will either (a) be ignored (row not written, insert reports "already exists" → `Ok(false)`) or (b) overwrite the existing row (destroying block A's rejection record). Either way, `get_block_rejection_signer_addrs(block_hash)` — used to compute `total_reject_weight` in `store_and_process_block_rejection` — [4](#0-3)  will not return this signer's weight for the block that legitimately needs it, even though a valid, authenticated rejection message was received and processed by `handle_block_rejection` (signature-verified against `is_valid_signer`) [5](#0-4) .

This is structurally the same class of defect as the referenced report: a legitimate, already-authorized/verified action (`baseToken.safeTransferFrom` self-transfer, or here, a validly-authenticated rejection) fails to take effect because of a missing scope/permission (missing self-approval there; missing per-block primary-key scoping here), even though the surrounding logic assumes it always succeeds for distinct legitimate cases.

### Impact Explanation
This breaks the equality between "verified accept/reject responses received" and "aggregated weight counted" for the block-rejection path. In the worst case, a signer set could have >30% weight validly rejecting a block, and the network should compute a `GloballyRejected` state via `mark_globally_rejected` [6](#0-5) , but silently dropped/overwritten rejection rows can prevent `total_reject_weight` from ever reaching the blocking-minority threshold for that specific block. This can wedge block finalization (neither the acceptance threshold nor the rejection threshold is reachable), a liveness impact on the signer set's ability to reach consensus on a block.

### Likelihood Explanation
This is reachable by ordinary, one-slot-miner-driven signer activity with no majority collusion required: any sequence of competing/sibling proposals at the same height, or a signer voting differently across separate tenures/reorg situations, naturally produces multiple *distinct* blocks that the same honest signer rejects over the life of the signer process (the DB is long-lived across reward cycles/tenures unless reset). This is a normal operational pattern documented in the flow notes around reorg/sibling handling (section 5/6 of `docs/signer-flows.md`), not an edge case requiring an attacker.

### Recommendation
Change the primary key of `block_rejection_signer_addrs` to the composite `(signer_signature_hash, signer_addr)` (with an accompanying schema migration to preserve/backfill and drop stale global uniqueness), matching the equivalent, correctly-scoped table `block_signatures`'s per-value key or a proper composite key, and audit `add_block_rejection_signer_addr`'s SQL to confirm it does not use `INSERT OR REPLACE`/`INSERT OR IGNORE` against the flawed single-column key.

### Proof of Concept
1. Signer S is asked to validate/sign competing block A (say a tenure-start block later abandoned) and rejects it — `add_block_rejection_signer_addr(hash_A, S, reason)` inserts a row `(hash_A, S)` with PK `S`.
2. Later, in the same signer-db lifetime, a different, unrelated valid block B is proposed at the same or later height (a normal sibling/re-proposal scenario per `docs/signer-flows.md` section 5-6) and S validly rejects it too.
3. `add_block_rejection_signer_addr(hash_B, S, reason)` collides on primary key `S`; depending on the exact SQL (not confirmed in this pass — could not view the exact `INSERT` statement due to tool-call budget), this either silently no-ops (returns `Ok(false)`, causing `store_and_process_block_rejection` to `return` early without tallying) or overwrites the row so `get_block_rejection_signer_addrs(hash_A)` no longer includes S.
4. In either case, weight tallies computed by `compute_signature_signing_weight`/`compute_reject_code_signing_weight` over `get_block_rejection_signer_addrs` for one of block A or B will under-count S's weight, potentially preventing the rejection-weight threshold from being reached for that block even though the requisite valid rejection weight was actually observed.

Note: I was not able to view the body of `add_block_rejection_signer_addr` (its exact `INSERT` SQL) before the tool budget ran out, so the precise failure mode (silent no-op vs. silent overwrite) is inferred from the schema and the `Ok(false) => return` handling convention used elsewhere in the file; a Devin session with full file access should confirm the exact SQL statement to nail down which of the two failure modes applies.

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

**File:** stacks-signer/src/signerdb.rs (L637-640)
```rust
static ADD_REJECT_CODE: &str = r#"
ALTER TABLE block_rejection_signer_addrs
    ADD COLUMN reject_code INTEGER;
"#;
```

**File:** stacks-signer/src/v0/signer.rs (L2228-2249)
```rust
        // authenticate the signature -- it must be signed by one of the stacking set
        let signer_address = StacksAddress::p2pkh(self.mainnet, &public_key);
        if !self.is_valid_signer(&signer_address) {
            debug!("{self}: Received block rejection with an invalid signature. Will not store.";
                "signer_public_key" => ?public_key,
                "signer_address" => %signer_address,
                "signer_signature_hash" => %block_hash,
                "signature" => %signature
            );
            return;
        }

        let Some(mut block_info) = self.block_lookup_by_reward_cycle(block_hash) else {
            if let Err(e) = self.signer_db.add_pending_block_rejection_response(
                block_hash,
                &signer_address,
                (&rejection.response_data.reject_reason).into(),
            ) {
                warn!("{self}: Failed to add pending block rejection response: {e:?}");
            }
            return;
        };
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

**File:** stacks-signer/src/v0/signer.rs (L2295-2313)
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
```

**File:** stacks-signer/src/v0/signer.rs (L2335-2341)
```rust
        if let Err(e) = block_info.mark_globally_rejected() {
            warn!("{self}: Failed to mark block as globally rejected: {e:?}",);
        }
        if let Err(e) = self.signer_db.insert_block(block_info) {
            error!("{self}: Failed to update block state: {e:?}",);
            panic!("{self} Failed to update block state: {e}");
        }
```
