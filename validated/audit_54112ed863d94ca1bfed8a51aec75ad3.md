### Title
Signer's peer-signature aggregation path (`store_and_process_block_signature`) skips the chainstate/conflict re-check that the pre-commit path enforces before crossing the signing threshold - ([File: stacks-signer/src/v0/signer.rs])

### Summary
The signer has two independent paths that can push a block to the ≥70% "signed" threshold: (1) `handle_block_pre_commit` (pre-commit tally → own signature) and (2) `handle_block_signature`/`store_and_process_block_signature` (peer `Accepted` responses tally → broadcast). Path (1) explicitly re-runs `check_block_against_signer_db_state` and the same-height/any-tenure conflict guard (`get_signed_conflicts` + `conflict_still_blocks` + `reorg_permit_stands`) immediately before crossing the threshold, precisely because "the chain and signer db state may have changed materially since this block passed the proposal-time checks." Path (2) does not: it only checks whether *this* signature was already stored and whether `signed_group` is already set, then directly calls `mark_locally_accepted(true)` and `broadcast_signed_block` once the tallied weight clears the threshold.

### Finding Description
`handle_block_pre_commit` re-validates chainstate/conflicts right before signing [1](#0-0) , and additionally re-checks for fresh signed conflicts at the same or higher height across any tenure before finally signing [2](#0-1) . The doc comments explicitly frame this as necessary because "between validation and threshold, we may have signed a different block at the same height, possibly in another tenure, so the world must be re-checked before the signature leaves the box" [3](#0-2) .

`store_and_process_block_signature`, which is reached from `handle_block_signature` when a peer's `Accepted` response arrives [4](#0-3) , tallies signature weight from `get_block_signatures` and, once the threshold is reached, immediately calls `mark_locally_accepted(true)` and `broadcast_signed_block` [5](#0-4)  — with no call to `check_block_against_signer_db_state`, `get_signed_conflicts`, or `conflict_still_blocks` anywhere in that function. The only "no-op" gates are "signature already exists" and "`signed_group` already set" [6](#0-5) .

This means if this signer's own pre-commit tally has not yet reached threshold (so path (1) never fires and never re-derives conflicts), but enough *peers'* `Accepted` responses independently arrive and get tallied to reach 70% weight, this signer will `mark_locally_accepted` and broadcast the aggregated signature set for a block that may by then conflict with something this same signer has since signed at the same/higher height in another tenure (exactly the double-sign scenario section 5's own comments describe), or that no longer confirms the tenure tip this signer's own chainstate view expects. The report's underlying bug class — a "batch"/aggregate code path that omits the equality/validity filter enforced in the "single" path (`balanceOf` vs `balanceOfBatch`) — maps directly here: the "single" (own-signing) path enforces chainstate/conflict equality just before crossing threshold, while the parallel "aggregate" (peer-signature-tally) path that produces the same practical effect (a signed, broadcast, pushed block) omits it.

### Impact Explanation
If this gap is real, it lets a signer end up counting a group signature set toward `mark_locally_accepted`/`broadcast_signed_block` for a block that conflicts with the signer's own subsequently-observed chainstate — i.e., a rejection or hold that should have applied at the equivalent point in the pre-commit path is bypassed, degrading the "a signer signing an invalid/non-canonical/conflicting block" guard to a race that depends on which path (own pre-commit tally vs peer accept tally) happens to cross threshold first. This falls under the Critical impact bucket (signer contributing to acceptance of a conflicting block) if the timing window can be hit without needing a majority of malicious signers — only the ordinary path of a legitimate majority's `Accepted` messages arriving out of band from this signer's own pre-commit convergence.

### Likelihood Explanation
This requires no attacker action beyond ordinary network/message-ordering variance: `Accepted` (`BlockResponse`) messages and `BlockPreCommit` messages are gossiped independently, so it is plausible for a signer to receive enough peer `Accepted` weight to cross 70% via `store_and_process_block_signature` before its own pre-commit tally (which does carry the re-check) reaches threshold, especially during a fork/reorg window where chainstate is actively shifting — the same window section 5's comments say the re-check exists to protect against. However, I could not fully verify from indexed excerpts whether some upstream call site (e.g., in `handle_block_response`, before dispatching to `handle_block_signature`) performs an equivalent re-check that was not captured in the snippets retrieved; the search results consistently show `handle_block_response`/`handle_block_signature` going straight into `store_and_process_block_signature` without an intervening chainstate check, but I cannot rule out an early-return elsewhere in `handle_block_response` I did not see.

### Recommendation
Before `store_and_process_block_signature` calls `mark_locally_accepted`/`broadcast_signed_block` once the tallied peer-signature weight crosses threshold, re-run the same chainstate/conflict re-check used in `handle_block_pre_commit` (`check_block_against_signer_db_state` plus the `get_signed_conflicts`/`conflict_still_blocks`/`reorg_permit_stands` guard), and fall back to a rejection/hold path symmetrical to the pre-commit path's handling when the check fails.

### Proof of Concept
Not independently reproduced; based on static code-path comparison between `handle_block_pre_commit` (which re-checks chainstate/conflicts before signing, `stacks-signer/src/v0/signer.rs` lines 1340–1421) and `store_and_process_block_signature` (which lacks any equivalent re-check, lines 2442–2538), reachable via ordinary `handle_block_response` → `handle_block_signature` message handling with no majority-signer collusion required, only favorable message ordering during a chain-state transition.

### Citations

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

**File:** stacks-signer/src/v0/signer.rs (L1368-1421)
```rust
        // A pre-commit may be superseded by a competing proposal at the same height (e.g. a
        // re-proposed tenure-start block after the first failed to reach consensus), but a
        // signature must not be superseded while it's still "fresh". A signed block at the
        // same or higher height in ANY tenure is a conflict: two blocks at the same height are
        // siblings no matter which tenure they belong to (e.g. the next tenure's tenure-start
        // block conflicts with the current tenure's block at the same height). Blocks in
        // tenures whose reorg we sanctioned under the reorg-timing rules are excluded, but
        // only while the sortition the permit was granted to is still canonical
        // (`check_parent_tenure_choice` records the permit, `reorg_permit_stands` re-derives
        // its validity from the node); every other question about whether a conflict is
        // still live is derived from the node in `conflict_still_blocks`.
        //
        // Unlike the chainstate check above, a refusal here is "for now" rather than a
        // broadcast rejection: a later pre-commit re-evaluation may still sign the block once
        // the conflicting signature has gone stale.
        let conflicts = match self
            .signer_db
            .get_signed_conflicts(block_info.block.header.chain_length, &block_hash)
        {
            Ok(conflicts) => conflicts,
            Err(e) => {
                warn!("{self}: Failed to query the signed blocks. Refusing to sign block {block_hash}: {e:?}");
                return;
            }
        };
        let freshness_cutoff = get_epoch_time_secs().saturating_sub(
            self.proposal_config
                .tenure_last_block_proposal_timeout
                .as_secs(),
        );
        // A fresh signature only blocks while the block it covers could still be part of the
        // chain: see `conflict_still_blocks`, which asks the node whether it is. Check
        // freshness first: it is a local timestamp comparison, while `reorg_permit_stands`
        // and `conflict_still_blocks` each query the node, so stale conflicts cost no
        // round-trips.
        if let Some(conflict) = conflicts.iter().find(|conflict| {
            conflict.last_endorsed > freshness_cutoff
                && !self.reorg_permit_stands(stacks_client, conflict)
                && self.conflict_still_blocks(
                    stacks_client,
                    conflict,
                    block_info.block.header.chain_length,
                )
        }) {
            warn!(
                "{self}: Reached the pre-commit threshold for a block, but we have recently signed or accepted a different block at the same or higher height. Refusing to sign.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "conflicting_signer_signature_hash" => %conflict.signer_signature_hash,
                "conflicting_block_height" => conflict.stacks_height,
                "conflicting_consensus_hash" => %conflict.consensus_hash,
            );
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2412-2440)
```rust
        let Some(mut block_info) = self.block_lookup_by_reward_cycle(block_hash) else {
            if let Err(e) = self.signer_db.add_pending_block_signature_response(
                block_hash,
                &signer_address,
                signature,
            ) {
                warn!("{self}: Failed to add pending block signature response: {e:?}");
            }
            return;
        };

        info!("{self}: Received block acceptance";
            "signer_pubkey" => public_key.to_hex(),
            "signer_address" => %signer_address,
            "signer_signature_hash" => %block_hash,
            "consensus_hash" => %block_info.block.header.consensus_hash,
            "block_height" => block_info.block.header.chain_length,
            "signer_weight" => self.signer_weights.get(&signer_address).copied().unwrap_or(0),
            "tenure_extend_timestamp" => accepted.response_data.tenure_extend_timestamp,
            "tenure_extend_read_count_timestamp" => accepted.response_data.tenure_extend_read_count_timestamp
        );
        self.store_and_process_block_signature(
            stacks_client,
            sortition_state,
            &mut block_info,
            &signer_address,
            signature,
        );
    }
```

**File:** stacks-signer/src/v0/signer.rs (L2451-2471)
```rust
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
```

**File:** stacks-signer/src/v0/signer.rs (L2472-2538)
```rust
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

**File:** docs/signer-flows.md (L229-235)
```markdown
## 5. Pre-commit threshold → signature

The only place the signer produces a block signature by counting votes.
Pre-commits from peers (and our own) accumulate; at ≥70% weight the signer
decides whether to follow through. Between validation and threshold, we may have
signed a _different_ block at the same height, possibly in another tenure, so
the world must be re-checked before the signature leaves the box.
```
