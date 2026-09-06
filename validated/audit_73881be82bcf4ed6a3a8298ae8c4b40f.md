### Title
`store_and_process_block_signature` broadcasts a group-signed block without re-checking chainstate/conflict state, unlike the parallel pre-commit-threshold path - (File: `stacks-signer/src/v0/signer.rs`)

### Summary
Illuminate's `Marketplace.setPrincipal` handled allowance for only one of two symmetrical code paths (`Element` vault vs `APWine` router), so a caller going through the incomplete path lost a guarantee the sibling path enforced. The stacks-signer has the same shape of bug: two code paths that both lead to the signer treating a block as consensus-final apply *different* levels of scrutiny. The pre-commit-threshold path re-verifies the block against the signer's chainstate/conflict view immediately before acting; the group-signature-threshold path does not.

### Finding Description
When a pre-commit crosses the ≥70% weight threshold, `handle_block_pre_commit` explicitly re-runs `check_block_against_signer_db_state` and the freshness/conflict checks (`get_signed_conflicts`, `reorg_permit_stands`, `conflict_still_blocks`) immediately before the signer commits to signing: [1](#0-0) [2](#0-1) 

This mirrors the flow documented for pre-commits: "the world must be re-checked before the signature leaves the box." [3](#0-2) 

In contrast, `store_and_process_block_signature` — the path that counts *foreign* signatures toward the group acceptance threshold and, on success, broadcasts the fully-signed block to the node — performs no equivalent recheck. It stores the incoming signature, tallies weight, and once `total_signature_weight` clears `min_weight` it goes straight to `mark_locally_accepted(true)` and `broadcast_signed_block`, without calling `check_block_against_signer_db_state` or the conflict/freshness checks that gate the sibling pre-commit path: [4](#0-3) 

Both paths ultimately decide whether *this signer* treats a block as settled and, in the acceptance case, actively relays a fully-signed block to the node. The pre-commit path treats "reached threshold" as necessary but not sufficient — it insists on a fresh chainstate/conflict check first. The signature-threshold path treats "reached threshold" (based purely on signatures already gathered from peers, which could have been produced or gossiped before this signer learned of a conflicting sibling, a reorg, or a chainstate change) as sufficient on its own.

This is the same class of defect as M-14: one branch of logic that is supposed to mirror another (both are "is this block now safe to act on and finalize" checks) drops a control the sibling branch enforces, breaking the equality between "aggregated-weight reached" and "still verified as valid/non-conflicting."

### Impact Explanation
If a signer accumulates enough peer signatures on a block that — per this signer's own, possibly more up-to-date chainstate/conflict view — should now be rejected (e.g., it conflicts with an already-signed sibling at the same height, or the sortition that would justify it has since been orphaned), `store_and_process_block_signature` will still call `broadcast_signed_block`, relaying that fully-signed, conflicting/stale block onward. This risks pushing a non-canonical or conflicting block toward the node for processing, which is exactly the "signer acting on/propagating an invalid or conflicting block" class of impact this scan targets, since the local safety recheck that the parallel pre-commit path performs is skipped here.

### Likelihood Explanation
This is reachable by ordinary gossip timing from a single miner tenure/one-slot-miner scenario the scan permits: a signer can receive threshold-crossing signatures for block B from peers before it has locally re-evaluated B against a newer chainstate view (e.g., after already pre-committing to or signing a conflicting sibling A, or after a burnchain fork). No majority collusion or key compromise is required — it only needs signatures that were validly produced earlier, now stale relative to this signer's current view, to arrive and cross the local weight-based threshold check in `store_and_process_block_signature`.

### Recommendation
Add the same `check_block_against_signer_db_state` (and same-tenure/cross-tenure conflict/freshness) recheck used in `handle_block_pre_commit` (lines 1345–1432) to `store_and_process_block_signature` immediately before `mark_locally_accepted(true)` / `broadcast_signed_block`, so that reaching the signature-weight threshold is treated as necessary-but-not-sufficient the same way pre-commit threshold is.

### Proof of Concept
Not independently reproduced in this pass; this is a static code-path comparison. Confirming exploitability end-to-end (constructing the exact sequence of a conflicting-sibling proposal plus delayed peer signatures crossing the threshold in `store_and_process_block_signature`) would require running the signer test harness (e.g., extending scenarios like those in `stacks-signer/src/v0/tests.rs` lines 711–750 and `stacks-node/src/tests/signer/v0/reorg.rs`), which is out of scope for this static review.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1345-1366)
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
```

**File:** stacks-signer/src/v0/signer.rs (L1403-1421)
```rust
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

**File:** docs/signer-flows.md (L229-235)
```markdown
## 5. Pre-commit threshold → signature

The only place the signer produces a block signature by counting votes.
Pre-commits from peers (and our own) accumulate; at ≥70% weight the signer
decides whether to follow through. Between validation and threshold, we may have
signed a _different_ block at the same height, possibly in another tenure, so
the world must be re-checked before the signature leaves the box.
```
