### Title
Signature-weight tally broadcasts an unvalidated block to the node — `store_and_process_block_signature` never checks `block_info.valid` before pushing consensus - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`store_and_process_block_signature` (the handler that tallies peer `BlockAccepted` signatures and, once the ≥70% acceptance threshold is reached, calls `broadcast_signed_block` → `handle_post_block` to push the block to this signer's own node) never checks `block_info.valid` before doing so. This is the direct sibling of `handle_block_pre_commit`, which explicitly guards with `if !block_info.valid.unwrap_or(false) { return; }` [1](#0-0)  before ever counting weight toward the pre-commit threshold. The signature path has no equivalent gate.

### Finding Description
`handle_block_signature` recovers and authenticates the signer's public key, then — if a pre-commit has already been seen from that peer — calls `store_and_process_block_signature` directly instead of routing back through the pre-commit evaluation: [2](#0-1) 

`store_and_process_block_signature` itself stores the signature and, without ever inspecting `block_info.valid`, computes the aggregate signing weight and — if it meets the compute_voting_weight_threshold — marks the block locally accepted and calls `broadcast_signed_block`, which pushes the block straight to the node via `handle_post_block`: [3](#0-2) 

Compare this to the pre-commit path, which is documented and coded to require `valid == Some(true)` before tallying weight toward a threshold decision [4](#0-3) , and to the validate-ok path, which explicitly re-checks chainstate before acting [5](#0-4) .

The gap is also reachable through `process_pending_responses_for_block`, called immediately after a brand-new `BlockInfo` (freshly built from `BlockInfo::from(block_proposal)`, i.e. `valid == None`, no node validation has run yet) is inserted into the DB at proposal-arrival time: [6](#0-5) 

That function replays any pending signatures (`pending_responses.signatures`) that had arrived from other signers *before* this signer had even seen the proposal, feeding them straight into `store_and_process_block_signature`: [7](#0-6) 

Because `store_and_process_block_signature` does not gate on `block_info.valid`, a signer whose own node validation of this brand-new proposal has not even been submitted yet (`valid == None`, `check_block_against_state`/`submit_block_for_validation` not yet resolved) can still tally enough pre-existing peer signatures to cross the 70% threshold and immediately push the block to its own node — before this signer's own `check_proposal`/node-validation step has run at all. This is exactly the ordering flaw described in the CVE: the equivalent of the "permission check" (`block_info.valid`, i.e., this signer's own confirmation that the block passed static/chainstate/node validation) is supposed to gate the consequential action (counting weight and pushing the block), but the code path that produces the consequential action runs without ever consulting that gate.

The documentation itself frames the pre-commit re-check as deliberate ordering-sensitive logic ("Order matters here: the chainstate re-check runs first...") [8](#0-7) , and a dedicated regression test exists proving the pre-commit path must wait for validation (`signer_waits_for_validation_before_signing`) [9](#0-8)  — but no analogous test or gate exists for the acceptance-tally/broadcast path.

### Impact Explanation
This breaks the "aggregated-weight vs. verified-accepts" equality: the local signer relays a block to its stacks-node as if consensus were locally confirmed valid, while it never independently validated the block itself. In a scenario where a fast subset of signers has already accepted a proposal that a slower signer's own node would have rejected (e.g. a proposal that is stale/conflicting/invalid per this signer's view but not yet checked), this signer still forwards it to its node purely because gossip-relayed signatures crossed the threshold — potentially causing the signer to push a block its own node validation would have rejected, wedging or corrupting its local view of consensus (mislabeling as "locally accepted" a block never vetted by this signer). This maps to the "a signer signing/relaying an invalid or non-canonical block based on a miscounted (unverified) acceptance" impact class.

### Likelihood Explanation
This requires only a single miner proposing a block plus normal StackerDB gossip of other signers' `BlockAccepted` responses — no majority-signer collusion, no private key access, and no auth token, satisfying the in-scope constraints. The race is naturally reachable any time a proposal-plus-signatures batch reaches a signer at once (the exact scenario the `should_reevaluate_block`/pending-response replay machinery and the `signers_wait_for_validation.rs` test suite were built to address for the pre-commit path, but which is left unguarded in the signature/broadcast path).

### Recommendation
Add the same `block_info.valid.unwrap_or(false)` gate (and/or a `check_block_against_signer_db_state` re-check) to `store_and_process_block_signature` before tallying weight and before calling `broadcast_signed_block`, mirroring the guard already present in `handle_block_pre_commit`. Alternatively, defer processing of `pending_responses.signatures` in `process_pending_responses_for_block` until `block_info.valid == Some(true)`, consistent with how pre-commits are gated.

### Proof of Concept
1. A signer set is running normally; signer S has slow/queued proposal delivery.
2. The miner (single slot) broadcasts a `BlockProposal` for block B.
3. Several other signers validate quickly and broadcast `BlockAccepted` signatures for B, reaching close to but under the point where S has processed the proposal.
4. Signer S's StackerDB listener delivers these `BlockAccepted` messages before the `BlockProposal` message itself arrives/is processed; they get parked via `add_pending_block_signature_response` (see `handle_block_signature`'s early return at [10](#0-9) ).
5. When S finally processes the `BlockProposal`, `handle_block_proposal` inserts a fresh `BlockInfo` with `valid == None` and immediately calls `process_pending_responses_for_block`, which replays the parked signatures into `store_and_process_block_signature` [6](#0-5) .
6. If the replayed signatures alone (from signers who had already pre-committed, e.g. from an earlier re-proposal of the same block) meet the 70% threshold, `store_and_process_block_signature` marks the block locally accepted and calls `broadcast_signed_block` → `handle_post_block`, pushing B to S's node — all without S's own `check_block_against_state`/node-validation of B ever having completed (`valid` was never checked in the signature path) [3](#0-2) .

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1316-1338)
```rust
        if block_info.signed_self.is_some() {
            debug!(
                "{self}: Received pre-commit for a block that we have already signed. Doing nothing...",
            );
            return;
        }

        if !block_info.valid.unwrap_or(false) {
            // We received a pre-commit for a block that we have not validated or we have already marked this block as invalid.
            // We should not do anything further as we do not know what our response should be and we do not change our votes on rejected
            // blocks unless we receive a new block proposal for it and the reject reason allows us to reconsider.
            debug!(
                "{self}: Received a pre-commit for a block that we have not determined to be valid: {:?}. Doing nothing...", block_info.valid
            );
            return;
        }

        if min_weight > commit_weight {
            debug!(
                "{self}: Not enough pre-committed to block {block_hash} (have {commit_weight}, need at least {min_weight}/{total_weight})"
            );
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1716-1726)
```rust
            // Do not store KNOWN invalid blocks as this could DOS the signer. We only store blocks that are valid or unknown.
            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
            self.process_pending_responses_for_block(
                stacks_client,
                sortition_state,
                &mut block_info,
                pending_responses,
            );
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1765-1779)
```rust
        let block_id = block_info.block.block_id();
        for (stackers_address, signature) in pending_responses.signatures {
            debug!("{self}: Processing pending signature.";
                "stacker_address" => %stackers_address,
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_id,
            );
            self.store_and_process_block_signature(
                stacks_client,
                sortition_state,
                block_info,
                &stackers_address,
                &signature,
            );
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1946-1959)
```rust
        if let Some(block_rejection) =
            self.check_block_against_signer_db_state(stacks_client, &block_info.block)
        {
            // The signer db state has changed. We no longer view this block as valid. Override the validation response.
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
```

**File:** stacks-signer/src/v0/signer.rs (L2412-2421)
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
```

**File:** stacks-signer/src/v0/signer.rs (L2443-2538)
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
    }
```

**File:** stacks-node/src/tests/signer/v0/signers_wait_for_validation.rs (L33-56)
```rust
#[test]
#[ignore]
/// Test that signers don't issue signatures until they have validated the block
///
/// This test verifies a race condition where a signer receives enough pre-commits
/// to exceed the 70% threshold before receiving its own block validation response.
/// The signer should NOT issue a signature until it has confirmed the block is valid.
///
/// Test Setup:
/// - Distribute signers across two miners (4 on miner 1, 1 on miner 2)
/// - Signers on different miners use different validation endpoints
///
/// Test Execution:
/// 1. Propose a block to all signers
/// 2. Pause block validation on miner 2 (the single signer)
/// 3. 4 signers on miner 1 issue pre-commits, pushing threshold over 70%
/// 4. The single signer on miner 2 receives all pre-commits but its validation is stalled
/// 5. Verify the single signer does NOT issue a signature until validation completes
/// 6. Resume validation and confirm the block is accepted
///
/// Test Assertion:
/// The signer waits for its own validation before issuing a signature, preventing
/// race conditions where it could sign before discovering the block is invalid.
fn signer_waits_for_validation_before_signing() {
```
