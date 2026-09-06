### Title
Missing conflict/validity recheck in `store_and_process_block_signature` lets a signer relay-accept a conflicting sibling block that its own pre-commit path would have refused to sign - (File: `stacks-signer/src/v0/signer.rs`)

### Summary
The signer enforces "don't sign two conflicting blocks at the same height" via an explicit re-check in the self-signing path (`handle_block_pre_commit`), but the parallel path that accumulates *peer* signatures into a group threshold (`store_and_process_block_signature`) never runs that same re-check before marking the block locally accepted and pushing it to the node. This is the same class of bug as the MEME404 report: two logically-equivalent "become accepted" paths enforce a shared invariant inconsistently, so the invariant can be defeated through the path that lacks the guard.

### Finding Description
Two independent code paths cause a signer to move a `BlockInfo` from validated/pre-committed to `LocallyAccepted` and broadcast/push it to the node:

1. **Own signature path** — `handle_block_pre_commit` (`stacks-signer/src/v0/signer.rs:1250-1479`). Before signing and calling `mark_locally_accepted`, it explicitly gates on `block_info.valid.unwrap_or(false)` [1](#0-0)  and re-runs `check_block_against_signer_db_state` plus `get_signed_conflicts`/`conflict_still_blocks`/`reorg_permit_stands` to refuse signing a block that conflicts with something this signer already signed/accepted at the same or higher height, in any tenure [2](#0-1) .

2. **Group/peer-signature relay path** — `store_and_process_block_signature`, reached from `handle_block_signature` when a `BlockResponse::Accepted` arrives from another signer [3](#0-2) . This function stores the peer signature, checks only `block_info.signed_group.is_some()` and whether accumulated peer-signature weight has crossed the voting threshold, then unconditionally calls `mark_locally_accepted(true)`, writes to `signer_db`, and calls `broadcast_signed_block` → `handle_post_block` → `stacks_client.post_block` to push the block to this signer's own node [4](#0-3) . Nowhere in this function is `check_block_against_signer_db_state`, `get_signed_conflicts`, or even `block_info.valid` consulted — the conflict/validity re-check that gates path (1) is absent from path (2).

This mirrors the reported asymmetry exactly: the "transfer NFT" path (self-sign via pre-commit) enforces the mutual-exclusion invariant, while the "transfer token" path (accept and relay a group-signed block from peers) does not, allowing the invariant ("never accept/push two conflicting blocks at the same height") to be bypassed through the second path.

The scenario is directly reachable without signer collusion: the repository's own tests document that a single miner can produce two sibling tenure-start blocks that both survive `check_proposal` due to the async-validation timing gap (`docs/signer-flows.md` section 5; `stacks-signer/src/v0/tests.rs::async_sibling_validation`) [5](#0-4) . Different signers can therefore validate and sign different siblings depending on message arrival order. A target signer that already signed sibling A (via its own pre-commit threshold, which applied the conflict re-check) can subsequently receive enough `BlockResponse::Accepted` messages for sibling B from other signers to cross the group threshold. Because `store_and_process_block_signature` has no conflict re-check, the target signer will `mark_locally_accepted` on B and push B to its own node even though B directly conflicts with A, which the pre-commit path would have refused to sign for exactly this reason.

### Impact Explanation
This falls into the "Critical" bucket defined by the task: a signer ends up relaying/accepting and pushing a non-canonical/conflicting block to its node — the same equivocation-guard invariant that section 5 of the documented state machine (and the dedicated conflict-checking code) is built to prevent is silently bypassed through a different call path. A signer's local bookkeeping (`mark_locally_accepted`, `signed_group`) and its node's chainstate could both be driven to accept a block that conflicts with one the same signer already vouched for, undermining the "one certificate per sortition" property the pre-commit path was specifically designed to guarantee.

### Likelihood Explanation
Reachable by a single miner (no majority-signer collusion, no key compromise) exploiting the already-documented async-validation timing window that lets two siblings both reach signer consideration, combined with normal peer-to-peer `BlockResponse` gossip. The precondition (a signer already tracking both A and B locally) is the same precondition the repository's own `async_sibling_validation` tests exercise for the self-sign path; the group-relay path simply has no equivalent guard to exercise/test against.

### Recommendation
Add the same re-check used in `handle_block_pre_commit` (`check_block_against_signer_db_state`, `get_signed_conflicts` with freshness/`conflict_still_blocks`/`reorg_permit_stands` logic, and a `block_info.valid` gate) to `store_and_process_block_signature` before calling `mark_locally_accepted`/`broadcast_signed_block`, so that accumulating peer signatures cannot push a block whose local acceptance would have been refused had this signer been the one crossing the threshold itself.

### Proof of Concept
Not independently executed against the repository; the reasoning is derived from static comparison of the two accept-paths (`handle_block_pre_commit` at `stacks-signer/src/v0/signer.rs:1250-1479` vs. `store_and_process_block_signature` at `stacks-signer/src/v0/signer.rs:2443-2538`) combined with the already-present `async_sibling_validation` test harness in `stacks-signer/src/v0/tests.rs:317-369`, which demonstrates that two conflicting sibling blocks can both reach signer tracking/pre-commit from a single miner. Adapting that harness to route enough `BlockResponse::Accepted` peer messages for the *unsigned* sibling into `handle_block_response` after the signer has already signed the other sibling would concretely demonstrate `store_and_process_block_signature` bypassing the conflict guard; this exact test was not run as part of this analysis, so the concrete pass/fail behavior is unverified and should be confirmed by a Devin session with repository execution access.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1323-1331)
```rust
        if !block_info.valid.unwrap_or(false) {
            // We received a pre-commit for a block that we have not validated or we have already marked this block as invalid.
            // We should not do anything further as we do not know what our response should be and we do not change our votes on rejected
            // blocks unless we receive a new block proposal for it and the reject reason allows us to reconsider.
            debug!(
                "{self}: Received a pre-commit for a block that we have not determined to be valid: {:?}. Doing nothing...", block_info.valid
            );
            return;
        }
```

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

**File:** stacks-signer/src/v0/signer.rs (L2443-2537)
```rust
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
```

**File:** stacks-signer/src/v0/tests.rs (L319-327)
```rust
/// Tests for the asynchronous-validation tenure-start timing gap.
///
/// `check_proposal` rejects a second tenure-start block for a tenure, but it runs before the
/// node's async validation, so two sibling tenure-start blocks proposed within the validation
/// window can both be pre-committed. A signer must still refuse to place a *signature* on a
/// second sibling while its signature on the first is fresh, so a single winning miner cannot
/// obtain two signer certificates for one sortition. Once the signature has timed out, the
/// signer consults the node and signs the replacement only if the signed sibling is not
/// canonical at that height, so a sibling that failed to be confirmed can still be replaced.
```
