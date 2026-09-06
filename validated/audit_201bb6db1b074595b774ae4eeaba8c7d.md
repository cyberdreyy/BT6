### Title
`store_and_process_block_signature` marks a block group-signed and pushes it to the node without re-running the chainstate/conflict checks that gate the equivalent pre-commit path, allowing a signer to broadcast/push a block that conflicts with one it has already signed - (File: stacks-signer/src/v0/signer.rs)

### Summary
When a signer's own pre-commit weight crosses 70%, `handle_block_pre_commit` re-validates the block against current chain/DB state (`check_block_against_signer_db_state`) and re-checks for signed conflicts (`get_signed_conflicts` / `reorg_permit_stands` / `conflict_still_blocks`) *before* signing [1](#0-0) . This is the analog of the Perennial report's requirement that pending-state transitions must be re-validated against limits rather than assumed valid because an earlier check passed.

`store_and_process_block_signature`, which tallies *other signers'* acceptance signatures toward the same 70% threshold, does **not** run either of these checks. Once the aggregated signature weight crosses the threshold it directly calls `block_info.mark_locally_accepted(true)`, persists it, and calls `broadcast_signed_block` to push the block to the node [2](#0-1) . There is no re-check of `check_block_against_signer_db_state`, no `get_signed_conflicts` lookup, and no freshness/`reorg_permit_stands` evaluation — the exact protections that the docs explicitly say exist "so the world must be re-checked before the signature leaves the box" for the pre-commit path [3](#0-2) .

### Finding Description
The two paths that can complete a signer's participation in a block are structurally supposed to be equivalent gates before anything is broadcast/pushed:

1. **Pre-commit → signature** (`handle_block_pre_commit`): after crossing the 70% pre-commit weight threshold, re-runs `check_block_against_signer_db_state`, and if that passes, walks through `get_signed_conflicts`, `reorg_permit_stands`, and `conflict_still_blocks` before calling `mark_locally_accepted` [4](#0-3) [5](#0-4) .

2. **Peer signature aggregation → broadcast** (`store_and_process_block_signature`, reached via `handle_block_signature`/`handle_block_response`): once the tallied signature weight for a block reaches the same 70% threshold, it goes straight to `mark_locally_accepted(true)` and `broadcast_signed_block` without any equivalent re-validation [6](#0-5) .

The only guard present is `if signer_address != &self.stacks_address && !has_committed(...) { handle_block_pre_commit(...); return; }`, which only re-routes into the safe path when the *sending peer's* pre-commit hasn't been recorded yet [7](#0-6) . It does nothing to protect against the *local signer's own* chainstate having changed (e.g., it already signed a conflicting sibling block at the same height, or the tenure it belongs to is no longer valid) between the time it recorded peers' pre-commits and the time their acceptance signatures arrive.

This mirrors the Perennial defect precisely at the structural level: a validated/limit-checked transition (`handle_block_pre_commit`'s SIGN branch, analogous to Market's per-update invariant check) has a companion code path (`store_and_process_block_signature`, analogous to `_processPositionGlobal`'s invalidation-driven position update) that reaches the *same terminal state* (locally-accepted + broadcast) through weight-tallying alone, without re-imposing the safety checks that make the first path safe. Just as the Perennial bug let a pending-position recalculation skip the `makerLimit` check that gated every normal `update`, this path lets a block reach "signed/broadcast" status while skipping the sibling-conflict and chainstate checks that gate every normal signature.

### Impact Explanation
If a signer has itself signed (or pre-committed and is about to sign) block A at height h, and a competing/conflicting block B at height h (a genuine sibling — e.g. a re-proposed tenure-start block, or a block in a different, no-longer-canonical tenure) separately accumulates 70% weight in *signature* form from peers (each of whom pre-committed to B earlier, before A won), this signer will hit `store_and_process_block_signature`'s threshold branch for B and push B to its node — even though the pre-commit code path's own-tenure/cross-tenure conflict guard (section 5 of the flow docs) would have refused to do so had this been reached via the normal signing route. This can result in the local signer's node processing/pushing a block that conflicts with a block the signer's own logic has already treated as canonical, i.e., a rejection-equivalent-invariant being bypassed and a conflicting/non-canonical block being propagated — matching the "Critical: signer signing/propagating an invalid, non-canonical, or conflicting block" impact bar.

### Likelihood Explanation
This requires only a normal, single-slot-miner-triggerable event ordering: two conflicting proposals at the same height (a sibling re-proposal, which the docs describe as an expected, already-tested scenario — see `run_sibling_scenario`/`run_cross_tenure_scenario` tests) plus ordinary gossip propagation delay of peer `BlockResponse::Accepted` messages relative to `BlockPreCommit` messages. No majority collusion, no additional keys, and no auth token access are needed — only asynchronous message delivery that the signer network already tolerates (the codebase's own docs stress that pre-commits and signatures can arrive out of order, which is exactly what `process_pending_responses_for_block`/`drain_pending_block_responses` exist to handle).

### Recommendation
Route `store_and_process_block_signature`'s threshold-crossing branch through the same re-validation used by `handle_block_pre_commit`'s SIGN path: re-run `check_block_against_signer_db_state` and the signed-conflicts/`reorg_permit_stands`/`conflict_still_blocks` checks before calling `mark_locally_accepted(true)` and `broadcast_signed_block`. If those checks fail, respond as a rejection (as the pre-commit path does) instead of silently broadcasting.

### Proof of Concept
1. Signer S sees proposal A and proposal B, both at height h, in the same or different tenures (a sibling scenario, as already exercised by `run_sibling_scenario`/`run_cross_tenure_scenario` in `stacks-signer/src/v0/tests.rs`).
2. S validates A first and, following the pre-commit threshold and conflict checks, signs A (`mark_locally_accepted`), because A's chainstate check and conflict guard pass at that moment (`handle_block_pre_commit` SIGN branch) [5](#0-4) .
3. Other signers, having earlier pre-committed to B (before A became canonical/fresh), independently reach their own 70% signature threshold for B and broadcast `BlockResponse::Accepted` for B.
4. S receives these `Accepted(B)` messages. Since S already recorded pre-commits from these signers for B (`has_committed` is true), S does not re-route through `handle_block_pre_commit`; it proceeds directly in `store_and_process_block_signature`, tallies weight, crosses 70%, and calls `mark_locally_accepted(true)` + `broadcast_signed_block` for B — with no chainstate or conflict re-check — even though S's own conflict-guard logic (used everywhere else) would have refused to sign/push B while its A-signature is still fresh [6](#0-5) .

Note: I could not fully verify from the index whether `broadcast_signed_block`/`handle_post_block` on the node side has an independent secondary defense that would reject B outright at the node level; this would need to be checked in a live/Devin session (e.g., `stacks-node`'s block-processing/fork-choice logic) to determine whether the missing re-check is fully exploitable end-to-end or only causes a wasted/harmless push attempt.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1345-1374)
```rust
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
```

**File:** stacks-signer/src/v0/signer.rs (L1432-1466)
```rust
        if conflicts.iter().any(|conflict| {
            conflict.consensus_hash == block_info.block.header.consensus_hash
                && !self.reorg_permit_stands(stacks_client, conflict)
        }) {
            match stacks_client.get_tenure_tip(&block_info.block.header.consensus_hash) {
                Ok(tip) => {
                    let tip_height = tip.anchored_header.height();
                    if tip_height >= block_info.block.header.chain_length {
                        warn!(
                            "{self}: Reached the pre-commit threshold for a block that conflicts with previously signed or accepted blocks, and the canonical tip of its tenure is already at or above the proposed height. Refusing to sign.";
                            "signer_signature_hash" => %block_hash,
                            "block_height" => block_info.block.header.chain_length,
                            "canonical_tip_height" => tip_height,
                        );
                        return;
                    }
                }
                Err(e) => {
                    warn!(
                        "{self}: Failed to fetch the canonical tip of the proposed block's tenure: {e:?}. Treating the tenure as unconfirmed.";
                        "signer_signature_hash" => %block_hash,
                        "consensus_hash" => %block_info.block.header.consensus_hash,
                    );
                }
            }
        }
        if !conflicts.is_empty() {
            info!(
                "{self}: Reached the pre-commit threshold for a block that conflicts with previously signed or accepted blocks, but none of those conflicts still blocks it. Signing the replacement.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "num_conflicts" => conflicts.len(),
            );
        }
        // It is only considered globally accepted IFF we receive a new block event confirming it OR see the chain tip of the node advance to it.
```

**File:** stacks-signer/src/v0/signer.rs (L2462-2466)
```rust
        // If this isn't our own signature and we haven't seen a pre-commit from this signer yet, try treating it as a pre-commit in case the caller is running an outdated version
        if signer_address != &self.stacks_address && !self.signer_db.has_committed(block_hash, signer_address).inspect_err(|e| warn!("Failed to check if pre-commit message already considered for {signer_address:?} for {block_hash}: {e}")).unwrap_or(false) {
            self.handle_block_pre_commit(stacks_client, sortition_state, signer_address, block_hash);
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2468-2538)
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

**File:** docs/signer-flows.md (L229-236)
```markdown
## 5. Pre-commit threshold → signature

The only place the signer produces a block signature by counting votes.
Pre-commits from peers (and our own) accumulate; at ≥70% weight the signer
decides whether to follow through. Between validation and threshold, we may have
signed a _different_ block at the same height, possibly in another tenure, so
the world must be re-checked before the signature leaves the box.

```
