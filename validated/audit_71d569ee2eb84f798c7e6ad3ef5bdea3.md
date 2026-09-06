### Title
Threshold-crossing on relayed peer signatures skips the chainstate/conflict re-check that gates the self-sign path - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`store_and_process_block_signature` — the function that tallies a peer's `BlockAccepted` signature and, once ≥70% weight is reached, marks the block locally accepted and pushes it to the node — never re-runs `check_block_against_signer_db_state` (the conflict/reorg/tenure-tip re-check). That re-check is mandatory on every other path that can cause a signature to be produced or a block to be pushed (`handle_block_validate_ok`, `handle_block_pre_commit`). This creates an "alternate binding" into the accept state, analogous to the ECP SAML binding bypassing the primary authentication flow's checks: a signer can reach and act on the 70% threshold purely by aggregating already-produced peer signatures, without the freshness/conflict gate that the direct proposal→pre-commit→sign path enforces at the moment of crossing threshold.

### Finding Description
The design (documented in `docs/signer-flows.md` section 5, "Pre-commit threshold → signature") is explicit that the chainstate re-check must happen every time the pre-commit weight crosses the signing threshold, specifically because the world may have moved between validation and threshold: [1](#0-0) 

That is implemented in `handle_block_pre_commit`, which re-runs `check_block_against_signer_db_state` (RECHECK) and the conflict/reorg-permit logic every time the pre-commit weight crosses threshold, before the signer will put a signature on the block: [2](#0-1) 

By contrast, the peer-signature aggregation path, `store_and_process_block_signature`, stores the raw signature immediately, and — once the signer has already recorded a pre-commit from that peer (`has_committed` true, or it's our own signature) — proceeds straight to counting weight and, if the threshold is met, marks the block accepted and broadcasts/pushes it, with **no call to `check_block_against_signer_db_state`, no re-check of `block_info.valid`, and no conflict/reorg-permit re-evaluation**: [3](#0-2) [4](#0-3) 

The entry point, `handle_block_signature`, only authenticates the signature (recovers the pubkey, checks `is_valid_signer`) before delegating to `store_and_process_block_signature`; it performs no chainstate check either: [5](#0-4) 

Compare this to the two paths that *do* gate a state transition with the re-check: validation-ok (`handle_block_validate_ok`) re-checks before marking pre-committed and before self-signing, [6](#0-5) 
and the pre-commit→sign path re-checks before self-signing (see anchor in the flow doc, section 5, "RECHECK -- yes --> CONF").

So there are two structurally different gates to the same terminal action (marking the block accepted and pushing it via `broadcast_signed_block`):
1. Self-sign path: proposal → validate → pre-commit → **RECHECK against current signer_db state (conflicts, reorg permits, tenure tips)** → sign.
2. Peer-signature aggregation path: any inbound `BlockAccepted` for a block we've already pre-committed to (`has_committed` true) is simply tallied; once the tally alone reaches 70%, the block is marked accepted and pushed — **no re-check of current local conflict state at the moment the threshold is crossed.**

Each individual peer's signature was vetted by *that peer* at *their* signing time, but time passes between when different peers sign and when the last one's signature arrives and pushes us over threshold. The self-sign path explicitly treats this window as dangerous enough to warrant a fresh RECHECK on every threshold crossing (see the design rationale in the docs: "the world must be re-checked before the signature leaves the box," and the freshness/`conflict_still_blocks` machinery built specifically to handle exactly this class of staleness). The signature-tally path has no equivalent freshness check at all, even though it triggers the same terminal action (`mark_locally_accepted` + `broadcast_signed_block`, which pushes the block to this signer's own node).

### Impact Explanation
This breaks the "aggregated-weight vs verified-accepts" equality: a locally accepted/pushed decision is produced from weight that was never re-verified against the signer's current view of chainstate at the moment the decision is made. Concretely: a signer signs block B under its own RECHECK at time T0 (valid then). Between T0 and the time enough peer signatures for B arrive, this signer's local chainstate view can shift (e.g., a conflicting sibling at the same height becomes locally/globally signed, or a burnchain reorg makes B's tenure non-canonical). If instead of routing back through `handle_block_pre_commit` the incoming signatures are already past `has_committed`, they go straight to the tally branch and can push the signer over threshold and cause it to broadcast+push B to its own node without ever re-asking "does this still fit the chain, and have I signed a rival at this height?" — the exact question the self-sign path always asks. This is the "aggregated-weight vs verified-accepts" mismatch called out in scope, and can cause the signer to push a stale/conflicting block to its node.

### Likelihood Explanation
No majority of malicious signers or leaked keys is required — the peer signatures being aggregated are honestly produced (each peer legitimately validated and signed at their own point in time). The trigger is purely timing/ordering: a one-slot miner forcing height/tenure ambiguity (e.g., via a burnchain fork or a rapid re-tenure) plus normal gossip propagation delay of `BlockAccepted` messages is sufficient to create the window where a signer's local view changes between its own signing and the arrival of the signatures that cross the threshold. This is a plausible, naturally occurring race given the documented staleness concerns that motivated the RECHECK in the self-sign path.

### Recommendation
Add the same `check_block_against_signer_db_state` (and associated conflict/reorg-permit) re-check to `store_and_process_block_signature` immediately before crossing the threshold and calling `mark_locally_accepted`/`broadcast_signed_block`, mirroring the guard already present in `handle_block_pre_commit` and `handle_block_validate_ok`. If the re-check fails, the signature tally should not trigger a push; the block should instead be handled the same way a stale pre-commit-crossing is handled (mark locally rejected / stay silent per the freshness rules), leaving the door open to accept once the conflict resolves.

### Proof of Concept
Not independently executed (index/tool access does not let me run the signer test suite). A concrete reproduction path is outlined by the existing test `signers_treat_signatures_as_precommits` in `stacks-node/src/tests/signer/v0/mod.rs` (lines ~8977-9013), which already demonstrates injecting raw `BlockAccepted` signatures directly to drive a signer's tally path; that scaffolding could be extended to (1) let a signer self-sign block B under one chainstate view, (2) mutate local chainstate to introduce a fresh conflicting signed sibling at the same height, then (3) inject additional peer `BlockAccepted` signatures for B until the 70% threshold is crossed via `store_and_process_block_signature`, and observe that `broadcast_signed_block`/push occurs without the RECHECK rejecting it — unlike what would happen if the same weight were delivered via `BlockPreCommit` messages into `handle_block_pre_commit`.

### Citations

**File:** docs/signer-flows.md (L229-236)
```markdown
## 5. Pre-commit threshold → signature

The only place the signer produces a block signature by counting votes.
Pre-commits from peers (and our own) accumulate; at ≥70% weight the signer
decides whether to follow through. Between validation and threshold, we may have
signed a _different_ block at the same height, possibly in another tenure, so
the world must be re-checked before the signature leaves the box.

```

**File:** stacks-signer/src/v0/signer.rs (L1340-1346)
```rust
        // The chain and signer db state may have changed materially since this block passed the
        // proposal-time checks (e.g. between validation and reaching the pre-commit threshold we
        // may have signed a block that this one would reorg). Re-run the chainstate checks
        // before putting a signature over the block, and respond with a rejection if they no
        // longer pass, just as the block validation response handler does.
        if let Some(block_rejection) =
            self.check_block_against_signer_db_state(stacks_client, &block_info.block)
```

**File:** stacks-signer/src/v0/signer.rs (L1946-1960)
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
        } else {
```

**File:** stacks-signer/src/v0/signer.rs (L2371-2439)
```rust
    /// Handle an observed signature from another signer
    fn handle_block_signature(
        &mut self,
        stacks_client: &StacksClient,
        sortition_state: &mut Option<SortitionsView>,
        accepted: &BlockAccepted,
    ) {
        let BlockAccepted {
            signer_signature_hash: block_hash,
            signature,
            metadata,
            ..
        } = accepted;
        debug!(
            "{self}: Received a block-accept signature: ({block_hash}, {signature}, {})",
            metadata.server_version
        );

        // recover public key
        let Ok(public_key) = Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s(
            block_hash.bits(),
            signature,
        ) else {
            debug!("{self}: Received unrecovarable signature. Will not store.";
                   "signature" => %signature,
                   "signer_signature_hash" => %block_hash);

            return;
        };

        // authenticate the signature -- it must be signed by one of the stacking set
        let signer_address = StacksAddress::p2pkh(self.mainnet, &public_key);
        if !self.is_valid_signer(&signer_address) {
            debug!("{self}: Received block acceptance with an invalid signature. Will not store.";
                "signer_public_key" => ?public_key,
                "signer_address" => %signer_address,
                "signer_signature_hash" => %block_hash,
                "signature" => %signature
            );
            return;
        }
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
```

**File:** stacks-signer/src/v0/signer.rs (L2442-2472)
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
```

**File:** stacks-signer/src/v0/signer.rs (L2503-2538)
```rust
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
