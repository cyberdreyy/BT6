Found it: the pre-commit path (`handle_block_pre_commit`) re-runs the full chainstate/conflict guard immediately before signing, but the signature-tally path (`store_and_process_block_signature`, reached via `handle_block_signature`) that also ends in `mark_locally_accepted(true)` performs **no** such re-check.

### Title
Signer follows peer-signature threshold into signing/accepting a stale or conflicting block because `store_and_process_block_signature` skips the chainstate/conflict re-check that `handle_block_pre_commit` performs - (File: `stacks-signer/src/v0/signer.rs`)

### Summary
`handle_block_pre_commit` (the path that produces *our own* signature via the pre-commit threshold) re-validates the block against `check_block_against_signer_db_state` and the same-height/any-tenure conflict guard (`get_signed_conflicts` + `conflict_still_blocks`/`reorg_permit_stands`) immediately before calling `mark_locally_accepted` [1](#0-0) . By contrast, `store_and_process_block_signature` (reached from `handle_block_signature` when a peer's `BlockAccepted` message is processed) tallies peer signature weight and, once the 70% threshold is met, calls `block_info.mark_locally_accepted(true)` and `broadcast_signed_block` directly, with **no** call to `check_block_against_signer_db_state` and no conflict/freshness check at all [2](#0-1) .

### Finding Description
The two code paths that can move a `BlockInfo` into `LocallyAccepted` (`mark_locally_accepted`) are supposed to be symmetric: both represent "this signer is now willing to treat the block as signed and push it toward the node." The pre-commit path treats this as consensus-sensitive and explicitly re-derives, at the moment weight crosses the threshold, whether the chain state has moved since validation:
- re-runs `check_block_against_signer_db_state` (tenure-change parent check / same-tenure latest-block check) and rejects if it now fails,
- then walks `get_signed_conflicts` for any signed block at the same or higher height in *any* tenure, applying `reorg_permit_stands` and `conflict_still_blocks` to decide whether the conflict is still live before allowing the signature to go out.

This logic exists precisely because "Between validation and threshold, we may have signed a _different_ block at the same height, possibly in another tenure, so the world must be re-checked before the signature leaves the box" (doc comment in the same function) [3](#0-2) .

`store_and_process_block_signature`, however, reaches the exact same terminal action (`mark_locally_accepted`, followed by broadcasting the now-fully-signed block to the node) purely by counting *other signers'* accept messages against the same 70% `NakamotoBlockHeader::compute_voting_weight_threshold`, without ever calling `check_block_against_signer_db_state` or the conflict-guard logic [4](#0-3) . The only staleness-independent guard present is `block_info.signed_group.is_some()` (already-processed short-circuit) and the "outdated peer" pre-commit fallback, neither of which re-derives current chain state .

Concretely: suppose between the time this signer validated/pre-committed block B and the time peer accept-messages for B cross 70% weight, this same signer has locally signed a conflicting sibling block A at the same height (in this tenure or another), exactly the scenario the pre-commit path's own-tenure/any-tenure conflict guard exists to catch (see `docs/signer-flows.md` section 5, and the `signer_refuses_to_sign_second_sibling_tenure_start` test which asserts the pre-commit path refuses to sign B while A's signature is fresh) [5](#0-4) . If instead of a local pre-commit-triggered check, B's *acceptance* threshold is reached via `handle_block_response`→`handle_block_signature`→`store_and_process_block_signature` (i.e., peers already had enough weight and this signer's own pre-commit tally never independently crossed threshold, or the peer messages arrive/aggregate through this path first), the code goes straight to `mark_locally_accepted(true)` and `broadcast_signed_block`, bypassing the conflict re-check and the `check_block_against_signer_db_state` re-validation entirely.

This breaks the "aggregated-weight vs verified-accepts" equality: the aggregated weight of *raw peer accept messages* is treated as sufficient to finalize/broadcast a block, without the signer confirming that its own current chainstate view (including its own more-recent conflicting signature) still permits the block.

### Impact Explanation
This can cause a signer to accept-and-broadcast (`broadcast_signed_block`→`handle_post_block`→`post_block`) a block B that conflicts with another block A this same signer already signed at the same/higher height, i.e. exactly the double-sign/equivocation condition section 5's guard exists to prevent. Broadcasting a conflicting block toward the node when the signer's own state no longer supports it is a "signer acting on stale/rejected local state as if verified," matching the Critical class ("a rejection recounted as an accept" / signing an invalid or conflicting block) since it also feeds `handle_block_signature`(self) → contributes this signer's own further attestations without a fresh validity check.

### Likelihood Explanation
Triggerable by ordinary gossip timing: a one-slot miner or any signer's own local sequence of events (sign A locally via pre-commit path, then receive/accumulate peer `BlockAccepted` messages for a competing B) is enough — no majority collusion or key compromise is required, only the normal asynchronous arrival of `BlockResponse::Accepted` messages relative to local pre-commit timing.

### Recommendation
Add the same `check_block_against_signer_db_state` call and same-height/any-tenure conflict guard (`get_signed_conflicts`, `reorg_permit_stands`, `conflict_still_blocks`) to `store_and_process_block_signature` immediately before `mark_locally_accepted(true)`, mirroring the checks already performed in `handle_block_pre_commit` before it signs.

### Proof of Concept
1. Signer S validates and pre-commits tenure-start block A (height h, tenure T1); A's pre-commit weight crosses 70% and S signs A via `handle_block_pre_commit` → `mark_locally_accepted` [6](#0-5) .
2. Before A's signature ages past `tenure_last_block_proposal_timeout`, S also holds block B (same height h, competing tenure/sibling), for which S has *not yet* reached its own pre-commit threshold but has received enough peer `BlockAccepted` messages for B to cross 70% weight via `handle_block_response`→`handle_block_signature`→`store_and_process_block_signature`.
3. `store_and_process_block_signature` for B tallies weight, crosses `min_weight`, and calls `block_info.mark_locally_accepted(true)` and `broadcast_signed_block` without ever calling `check_block_against_signer_db_state` or checking `get_signed_conflicts` against A [7](#0-6) .
4. Result: S ends up having signed/accepted and broadcast two conflicting blocks (A and B) at the same height — the exact scenario `signer_refuses_to_sign_second_sibling_tenure_start` proves is blocked on the pre-commit path but which the signature-tally path never checks for.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1340-1421)
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

**File:** stacks-signer/src/v0/signer.rs (L1458-1471)
```rust
        if !conflicts.is_empty() {
            info!(
                "{self}: Reached the pre-commit threshold for a block that conflicts with previously signed or accepted blocks, but none of those conflicts still blocks it. Signing the replacement.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "num_conflicts" => conflicts.len(),
            );
        }
        // It is only considered globally accepted IFF we receive a new block event confirming it OR see the chain tip of the node advance to it.
        if let Err(e) = block_info.mark_locally_accepted(false) {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally accepted: {e:?}",);
            }
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2442-2538)
```rust
    /// Store the block acceptance signature and check if we have reached a consensus decision on the block because of it. If we have, update the block state accordingly and broadcast the block if accepted.
    fn store_and_process_block_signature(
        &mut self,
        stacks_client: &StacksClient,
        sortition_state: &mut Option<SortitionsView>,
        block_info: &mut BlockInfo,
        signer_address: &StacksAddress,
        signature: &MessageSignature,
    ) {
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

**File:** stacks-signer/src/v0/tests.rs (L770-789)
```rust
    #[test]
    fn signer_refuses_to_sign_second_sibling_tenure_start() {
        // Pin the fresh window far beyond the test's runtime so the guard can only take the
        // fresh branch; the stale branch is covered by the tests below.
        let (info_a, info_b, _) = run_sibling_scenario(Duration::from_secs(100_000), false, None);
        assert_a_signed(&info_a);
        // B is still pre-committed (the sibling is allowed to reach pre-commit), but the signer
        // must refuse to place a second signature on a conflicting same-height block in this
        // tenure while its signature on A is fresh.
        assert_eq!(
            info_b.state,
            BlockState::PreCommitted,
            "block B should be pre-committed but not promoted, got: {}",
            info_b.state
        );
        assert!(
            info_b.signed_self.is_none(),
            "block B must NOT be signed: the signer already signed a conflicting sibling in this tenure"
        );
    }
```
