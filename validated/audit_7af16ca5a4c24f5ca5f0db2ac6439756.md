### Title
Stale pre-commit weight from a signer that has since locally rejected the same block inflates the 70% signing threshold - ([File: stacks-signer/src/signerdb.rs], [File: stacks-signer/src/v0/signer.rs])

### Summary
`SignerDb::add_block_pre_commit` permanently records a signer's pre-commit vote for a given block hash and is never deleted, even after that same signer later re-evaluates the identical block and moves it to `LocallyRejected`. Every subsequent pre-commit tally for that exact `signer_signature_hash` (via `get_block_pre_committers`) keeps counting that signer's weight as if it still intends to sign, so the 70% pre-commit/signing threshold can be crossed using weight that no longer represents a live "willing to sign" vote — an aggregated-weight vs. verified-accepts mismatch.

### Finding Description
`handle_block_pre_commit` unconditionally persists every received pre-commit before doing any validity check: [1](#0-0) 
This insert happens even for our own signer's pre-commit path, which is invoked from `handle_block_validate_ok` immediately after `mark_pre_committed`: [2](#0-1) 

`add_block_pre_commit` is an `INSERT OR REPLACE` keyed on `(signer_signature_hash, signer_addr)`; there is no corresponding delete anywhere in `signerdb.rs`, so once a `(block, signer)` pre-commit row exists it is permanent for the lifetime of that exact block hash: [3](#0-2) [4](#0-3) 

Later in the same `handle_block_pre_commit` flow, after the weight is tallied, the code re-runs the chainstate checks and can move the block to `LocallyRejected` for the *committing* signer itself, if `check_block_against_signer_db_state` now fails: [5](#0-4) 
This rejection changes the signer's own decision (documented as a valid `LocallyAccepted <-> LocallyRejected` "re-evaluated" transition), but it does **not** retract the row previously written to `block_pre_commits`. Because `get_block_pre_committers`/`compute_signature_signing_weight` read directly from that table, this now-stale weight keeps being summed into `commit_weight` on every future pass over the same `block_hash` — e.g. when a different peer's late pre-commit or an "outdated peer" acceptance re-triggers `handle_block_pre_commit` (`store_and_process_block_signature`'s fallback path): [6](#0-5) 

This is the same bug class as the CToken exchange-rate report: a derived/cached accounting value (`commit_weight`, analogous to the exchange rate) is computed from raw ledger rows (`block_pre_commits`, analogous to token balances) that are never invalidated when the real, current state (this signer's live vote) has moved on. The external event that "transfers value out from under" the derived value here is the chainstate re-check flipping a signer's vote to reject without cleaning up the vote's earlier footprint in the tally table.

### Impact Explanation
`commit_weight >= min_weight` is the sole gate that lets a signer produce a block signature over a proposal (`handle_block_pre_commit` → `SIGN` path in the documented flow). If that weight includes a signer address whose current, authoritative state for this exact block is `LocallyRejected`, the threshold can be satisfied with less real, live support than 70%, i.e. a rejection is effectively recounted toward the acceptance tally. This can let a signer sign (and, if enough other signers are similarly miscounted, the network can globally accept) a block that does not actually have 70% live signer support — matching the Critical bar ("a rejection recounted as acceptance").

### Likelihood Explanation
No majority collusion or key compromise is required. A single miner tenure whose chainstate view flips between proposal time and pre-commit-threshold time (e.g., due to a signer briefly signing a conflicting block, or a burn/tenure-tip observation that later reverts, as extensively handled by `conflict_still_blocks`/`reorg_permit_stands`) is sufficient to make one signer's own re-evaluation move a previously pre-committed block to `LocallyRejected` while its pre-commit row remains. Any later gossip (a peer's pre-commit or an "outdated peer" acceptance) re-triggers the tally and reuses the stale weight. This is well within a one-slot miner plus normal gossip.

### Recommendation
When a `BlockInfo` transitions away from a state where the signer is willing to sign for a given `signer_signature_hash` (i.e., `mark_locally_rejected` after having previously pre-committed), delete or otherwise invalidate the corresponding row for `(signer_signature_hash, own_address)` in `block_pre_commits`, or filter `get_block_pre_committers` to exclude any signer whose recorded state for the same `block_hash` is not still "willing to sign." Equivalently, adopt the report's general remedy: derive the tally directly from live per-signer state rather than from an accumulate-only ledger that is never reconciled against the signer's current decision.

### Proof of Concept
1. Miner proposes block `B` (hash `H`); signer `S` validates it and calls `mark_pre_committed` + broadcasts its pre-commit, which inserts `(H, S)` into `block_pre_commits` (`stacks-signer/src/v0/signer.rs:1960-1983`, `signerdb.rs:2441-2456`).
2. Before `H` crosses the 70% pre-commit threshold, the chain/signer-db state changes in a way `check_block_against_signer_db_state` now detects as a conflict for `S` specifically (e.g., `S` has meanwhile signed a conflicting sibling at the same height, satisfying `get_signed_conflicts`).
3. A later pre-commit message for `H` from another peer re-triggers `handle_block_pre_commit` on `S`'s own node; the RECHECK now fails, and `S` calls `mark_locally_rejected` on `B` (`signer.rs:1340-1366`). The `(H, S)` row in `block_pre_commits` is left untouched.
4. As more peers pre-commit (or an "outdated peer" acceptance re-enters via `store_and_process_block_signature`'s fallback, `signer.rs:2462-2466`), `get_block_pre_committers(H)` still returns `S`, and `commit_weight` still includes `S`'s weight even though `S`'s current, authoritative state for `H` is `LocallyRejected`.
5. If the remaining live pre-committers plus `S`'s stale weight reach `min_weight`, a signer proceeds to `SIGN` off a threshold that never had 70% of genuinely live support for `H`.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1275-1281)
```rust
        // Always save the pre-commit - we will need to store signer responses for determining which
        // are misbehaving, offline, etc.
        // commit message is from a valid sender! store it
        self.signer_db
            .add_block_pre_commit(block_hash, stacker_address)
            .unwrap_or_else(|_| panic!("{self}: Failed to save block pre-commit"));

```

**File:** stacks-signer/src/v0/signer.rs (L1340-1366)
```rust
        // The chain and signer db state may have changed materially since this block passed the
        // proposal-time checks (e.g. between validation and reaching the pre-commit threshold we
        // may have signed a block that this one would reorg). Re-run the chainstate checks
        // before putting a signature over the block, and respond with a rejection if they no
        // longer pass, just as the block validation response handler does.
        if let Some(block_rejection) =
            self.check_block_against_signer_db_state(stacks_client, &block_info.block)
        {
            warn!(
                "{self}: Reached the pre-commit threshold for a block, but it no longer passes the chainstate checks. Rejecting.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "reject_code" => %block_rejection.reason_code,
                "reject_reason" => &block_rejection.reason,
            );
            if let Err(e) = block_info.mark_locally_rejected() {
                if !block_info.has_reached_consensus() {
                    warn!("{self}: Failed to mark block as locally rejected: {e:?}");
                }
            };
            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
            self.handle_block_rejection(&block_rejection, sortition_state);
            self.send_block_response(&block_info.block, block_rejection.into());
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1960-1983)
```rust
        } else {
            if let Err(e) = block_info.mark_pre_committed() {
                // The block may have reached enough signatures before we validated the block so should fail to mark pre-committed
                // but still call to make sure the timestamps and validity are updated correctly.
                if !block_info.has_reached_consensus()
                    && block_info.state != BlockState::LocallyAccepted
                {
                    warn!("{self}: Failed to mark block as approved: {e:?}",);
                    return;
                }
            }

            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
            self.send_block_pre_commit(signer_signature_hash.clone());
            // have to save the signature _after_ the block info
            let address = self.stacks_address.clone();
            self.handle_block_pre_commit(
                stacks_client,
                sortition_state,
                &address,
                signer_signature_hash,
            );
```

**File:** stacks-signer/src/signerdb.rs (L2441-2456)
```rust
    /// Record an observed block pre-commit
    pub fn add_block_pre_commit(
        &self,
        block_sighash: &Sha512Trunc256Sum,
        address: &StacksAddress,
    ) -> Result<(), DBError> {
        let qry = "INSERT OR REPLACE INTO block_pre_commits (signer_signature_hash, signer_addr) VALUES (?1, ?2);";
        let args = params![block_sighash, address.to_string()];

        debug!("Inserting block pre-commit.";
            "signer_signature_hash" => %block_sighash,
            "signer_addr" => %address);

        self.db.execute(qry, args)?;
        Ok(())
    }
```

**File:** stacks-signer/src/signerdb.rs (L2481-2495)
```rust
    /// Get all pre-committers for a block
    pub fn get_block_pre_committers(
        &self,
        block_sighash: &Sha512Trunc256Sum,
    ) -> Result<Vec<StacksAddress>, DBError> {
        let qry = "SELECT signer_addr FROM block_pre_commits WHERE signer_signature_hash = ?1";
        let args = params![block_sighash];
        let addrs_txt: Vec<String> = query_rows(&self.db, qry, args)?;

        let res: Result<Vec<_>, _> = addrs_txt
            .into_iter()
            .map(|addr| StacksAddress::from_string(&addr).ok_or(DBError::Corruption))
            .collect();
        res
    }
```
