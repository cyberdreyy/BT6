### Title
Stale/unrechecked peer signatures are permanently recorded and later recounted toward the acceptance threshold, letting a locally-rejected block be broadcast — ([File: stacks-signer/src/v0/signer.rs])

### Summary
The `_giveToken` bug (ERC777) is a class of "record a value before an untrusted side-effect, then use a diff/count that the side-effect can inflate without going through the intended validation gate." The same class appears in `stacks-signer/src/v0/signer.rs::store_and_process_block_signature`: a peer's `BlockResponse::Accepted` signature is persisted to `SignerDb` unconditionally, *before* the chainstate re-check that is supposed to gate whether that signer's vote should count, and the persisted entry is never purged or excluded if that re-check later fails.

### Finding Description
When a `BlockResponse::Accepted` arrives from a peer that never sent a pre-commit ("outdated peer" compatibility path), `store_and_process_block_signature` first unconditionally stores the signature: [1](#0-0) 

Only *after* persisting it does the code reroute the event into `handle_block_pre_commit`, which is the function that actually re-runs the conflict/chainstate check (`check_block_against_signer_db_state`) before deciding whether *this* signer should sign: [2](#0-1) 

If that re-check fails, the local `block_info` is marked locally rejected and a rejection is broadcast — but the peer's signature that was already written to `block_signatures` in `SignerDb` is never removed. The table is keyed only by `(signer_signature_hash, signer_addr)` via `INSERT OR IGNORE`, with no linkage to whether the recorded signer ever passed the pre-commit/re-check gate: [3](#0-2) [4](#0-3) 

Crucially, when a *subsequent, legitimate* signature arrives for the same block (e.g., from a signer who never saw the conflict, or before it materialized), `store_and_process_block_signature` recomputes the acceptance weight purely from `get_block_signatures`, with **no check of `block_info.valid`, `block_info.state`, or `has_reached_consensus()`** before tallying and potentially broadcasting: [5](#0-4) [6](#0-5) 

This mirrors the ERC777 flaw exactly: the counted quantity (`total_signature_weight`) is a diff/sum computed from state (`block_signatures` rows) that can be inflated via a path (the "outdated-peer" reroute) that bypasses the intended gating check (the pre-commit RECHECK), and the inflated count is only consumed later, at which point there is no re-validation tying the stored signature back to whether it passed that gate.

### Impact Explanation
If the local signer's own chainstate re-check (`RECHECK`, section 5/7 of `docs/signer-flows.md`) has determined a block conflicts with something it already signed — and thus marked it locally rejected — but an outdated peer's *pre-existing* stored signature plus one more genuinely arriving signature push `total_signature_weight` over threshold, `store_and_process_block_signature` will call `mark_locally_accepted` and `broadcast_signed_block`, causing the local node to receive and push a block that the signer's own conflict-detection logic had already determined should not be signed. This is exactly the "rejection recounted as acceptance" / "signer participating in finalizing a conflicting block" impact class: the signer helps assemble and push a signature set for a block it independently flagged as conflicting, using weight from a signature recorded via a path that skipped the check meant to filter it out.

### Likelihood Explanation
This requires no majority of colluding signers and no access to another signer's private key: it only needs (a) at least one peer running the pre-global-state / outdated protocol version that sends `Accepted` without a prior pre-commit (an explicitly supported compatibility scenario per `docs/signer-flows.md` section 6), and (b) ordinary tenure/fork timing so that a conflict is discovered by the local re-check *after* that peer's signature has already been durably recorded. Both conditions occur naturally during forks/reorgs and mixed-version fleets, which the codebase's own documentation says it must tolerate.

### Recommendation
Before tallying and potentially broadcasting from `get_block_signatures()` in `store_and_process_block_signature`, re-validate the block against the current `SignerDb`/chainstate view (the same `check_block_against_signer_db_state` used in `handle_block_pre_commit`), or gate on `block_info.valid`/`block_info.state` so that a block already marked locally/globally rejected cannot later be re-promoted to accepted purely because previously-recorded, un-rechecked peer signatures are recounted. Alternatively, do not persist a peer's signature into `block_signatures` until it has been routed through the same re-check pipeline that governs the signer's own signing decision.

### Proof of Concept
1. Signer `S` receives a valid `BlockProposal` for block `B` at height `h`, validates it, and stores it as `pre-committed`.
2. An outdated-version peer `P` sends `BlockResponse::Accepted(B)` without ever sending a `BlockPreCommit`. `store_and_process_block_signature` verifies the signature and calls `add_block_signature`, durably storing `P`'s signature for `B` (`stacks-signer/src/v0/signer.rs:2454-2460`).
3. Because `P` has not been seen as a committer, the code reroutes into `handle_block_pre_commit` (`stacks-signer/src/v0/signer.rs:2463-2465`), which runs the RECHECK and discovers `B` now conflicts with a block `S` has since signed at the same height in another tenure; `B` is marked `LocallyRejected` and a rejection is broadcast.
4. `P`'s `Accepted` signature for `B`, however, remains stored in `block_signatures`.
5. A second, honest signer `Q`, whose view has not yet caught the conflict, sends a genuine `BlockResponse::Accepted(B)` with a real pre-commit already on file.
6. `store_and_process_block_signature` runs again for `Q`'s signature: it does not check `block_info.valid`/`state`, pulls all rows from `get_block_signatures(B)` — which now includes both `P`'s stale entry and `Q`'s — and if their combined weight crosses `min_weight`, calls `mark_locally_accepted(true)` and `broadcast_signed_block`, pushing `B` to the node even though `S` had already determined `B` conflicts and rejected it.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1340-1345)
```rust
        // The chain and signer db state may have changed materially since this block passed the
        // proposal-time checks (e.g. between validation and reaching the pre-commit threshold we
        // may have signed a block that this one would reorg). Re-run the chainstate checks
        // before putting a signature over the block, and respond with a rejection if they no
        // longer pass, just as the block validation response handler does.
        if let Some(block_rejection) =
```

**File:** stacks-signer/src/v0/signer.rs (L2452-2466)
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

        // If this isn't our own signature and we haven't seen a pre-commit from this signer yet, try treating it as a pre-commit in case the caller is running an outdated version
        if signer_address != &self.stacks_address && !self.signer_db.has_committed(block_hash, signer_address).inspect_err(|e| warn!("Failed to check if pre-commit message already considered for {signer_address:?} for {block_hash}: {e}")).unwrap_or(false) {
            self.handle_block_pre_commit(stacks_client, sortition_state, signer_address, block_hash);
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2468-2523)
```rust
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

        // put signatures in order by signer address (i.e. reward cycle order)
        let addrs_to_sigs: HashMap<_, _> = signatures
            .into_iter()
            .filter_map(|sig| {
                let Ok(public_key) = Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s(
                    block_hash.bits(),
                    &sig,
                ) else {
                    return None;
                };
                let addr = StacksAddress::p2pkh(self.mainnet, &public_key);
                Some((addr, sig))
            })
            .collect();

        let signature_weight = self.signer_weights.get(signer_address).unwrap_or(&0);
        let total_signature_weight = self.compute_signature_signing_weight(addrs_to_sigs.keys());
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });

        if min_weight > total_signature_weight {
            info!("{self}: Received block acceptance, but have not yet reached the acceptance threshold.";
                "signer_signature_hash" => %block_hash,
                "signature_weight" => signature_weight,
                "consensus_hash" => %block_info.block.header.consensus_hash,
                "block_height" => block_info.block.header.chain_length,
                "total_weight_approved" => total_signature_weight,
                "total_weight" => total_weight,
                "percent_approved" => (total_signature_weight as f64 / total_weight as f64 * 100.0),
            );
            return;
        }
        info!("{self}: have reached the block acceptance threshold";
            "signer_signature_hash" => %block_hash,
            "signature_weight" => signature_weight,
            "consensus_hash" => %block_info.block.header.consensus_hash,
            "block_height" => block_info.block.header.chain_length,
            "total_weight_approved" => total_signature_weight,
            "total_weight" => total_weight,
            "percent_approved" => (total_signature_weight as f64 / total_weight as f64 * 100.0),
        );
```

**File:** stacks-signer/src/v0/signer.rs (L2525-2538)
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
    }
```

**File:** stacks-signer/src/signerdb.rs (L708-720)
```rust
static CREATE_BLOCK_SIGNATURES_TABLE_V16: &str = r#"
CREATE TABLE IF NOT EXISTS block_signatures (
    -- The block sighash commits to all of the stacks and burnchain state as of its parent,
    -- as well as the tenure itself so there's no need to include the reward cycle.  Just
    -- the sighash is sufficient to uniquely identify the block across all burnchain, PoX,
    -- and stacks forks.
    signer_signature_hash TEXT NOT NULL,
    -- the signer address that signed the block
    signer_addr TEXT NOT NULL,
    -- signature itself
    signature TEXT NOT NULL,
    PRIMARY KEY (signer_signature_hash, signer_addr)
) STRICT;"#;
```

**File:** stacks-signer/src/signerdb.rs (L1871-1906)
```rust
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

        let is_new_signature = rows_added > 0;
        if is_new_signature {
            debug!("Added block signature.";
                "signer_signature_hash" => %block_sighash,
                "signer_address" => %signer_addr,
                "signature" => %signature
            );
        } else {
            debug!("Duplicate block signature.";
                "signer_signature_hash" => %block_sighash,
                "signer_address" => %signer_addr,
                "signature" => %signature
            );
        }
        Ok(is_new_signature)
    }
```
