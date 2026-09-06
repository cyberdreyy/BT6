Found a concrete analog. `BlockPreCommit` messages carry only a `Sha512Trunc256Sum` — no signature at all — and the sender's identity is taken purely from the StackerDB chunk's own signing key, which the signer maps to a `StacksAddress` and then treats as authenticated: [1](#0-0) [2](#0-1) 

That `signer_address` (derived from `signer_public_key`, itself just the chunk's StackerDB signer key) is passed straight into `handle_block_pre_commit` as `stacker_address` and persisted/tallied as this signer's committed vote toward the 70% pre-commit threshold that ultimately triggers the local signature: [3](#0-2) [4](#0-3) 

### Title
Pre-commit vote weight is attributed to the StackerDB chunk signer, not to a message-bound signature — `BlockPreCommit` has no per-message authentication ([File: stacks-signer/src/v0/signer.rs])

### Summary
Every other signer-to-signer vote type (`BlockResponse::Accepted`, `BlockResponse::Rejected`) carries its own SIP-018 structured-data signature over the vote content, which is verified and used to *recover* the voting identity independently of whoever relayed the chunk (`handle_block_signature`/`handle_block_rejection` at lines 2371-2440, 2208-2265, using `recover_public_key`/`recover_to_pubkey_without_validating_low_s`). `BlockPreCommit`, by contrast, is just a bare `Sha512Trunc256Sum` [1](#0-0) . Its "signer" is whatever StackerDB slot/pubkey the message arrived signed under, taken directly as `signer_public_key` in the dispatch loop and turned into `stacker_address` with no additional per-vote signature check [2](#0-1) .

### Finding Description
This is structurally the same defect class as the report's "mixed `msg.sender`/`_msgSender()`" bug: two different mechanisms are used to establish "who is voting" across sibling code paths that are supposed to be equivalent inputs into the same threshold. For `Accepted`/`Rejected` votes, identity is bound cryptographically to the vote's *content* (the block hash) via a dedicated signature that is recovered and checked against `is_valid_signer`. For `BlockPreCommit`, identity is bound only to the *transport* layer (the StackerDB chunk signature over the chunk, decoded upstream into `signer_public_key`), and the pre-commit payload itself (`Sha512Trunc256Sum`) is never authenticated as having been intentionally produced by that signer for that specific block — the "signature" is on the chunk metadata, not on a domain-separated pre-commit message.

Because pre-commit weight is what actually gates the transition into `mark_pre_committed`/eventual signature production (`handle_block_pre_commit`, lines 1250-1301, threshold check at `min_weight > commit_weight`), and `store_and_process_block_signature` will retroactively convert *any* `Accepted` signature into a synthesized pre-commit for backward compatibility when no explicit pre-commit was seen yet (lines 2462-2466), an inconsistency between "whose identity counts toward pre-commit" and "whose identity counts toward the final signature threshold" is exactly the kind of equality break the report is about: the local signer's decision to sign is driven by weight attributed via one authentication path (transport-level, StackerDB chunk key) rather than the message-content signature path used everywhere else in the same file.

### Impact Explanation
If the transport-level attribution used for `BlockPreCommit` (StackerDB chunk signer key → address) can ever diverge from the content-signed attribution used for `Accepted`/`Rejected` (recovered pubkey → address) — e.g., through relaying, replay, or any code path that decodes `signer_public_key` from something other than a fresh per-message signature check — a signer's pre-commit threshold could be satisfied by votes that were never actually cryptographically bound to that specific block by the claimed signer, pushing the local signer to sign a block based on miscounted/unauthenticated "acceptance." This falls under the Critical impact category (accepted weight not actually verified to correspond to that signer's endorsement of that block) if such a discrepancy is reachable by a single malicious signer or a gossip-layer manipulation, since it directly weakens the "aggregated-weight vs verified-accepts" equality the pre-commit/signature pipeline depends on.

### Likelihood Explanation
I could not find, within the indexed portion of this repo, a demonstrated concrete divergence between the StackerDB chunk-key attribution and the intended message-signer for `BlockPreCommit` — `messages.into_iter()` at line 372 of `stackerdb_listener.rs` and the analogous loop in `signer.rs` both appear to derive `signer_public_key`/`_pk` from the per-chunk signature check performed by the StackerDB layer itself (outside the files I was able to inspect in depth), which is a reasonably strong binding in the current single-slot-per-signer design. Without visibility into the full StackerDB chunk-signing/slot-assignment code (`libsigner`'s StackerDB session and the node's slot-to-signer mapping), I cannot conclusively prove a reachable path where the "sender" resolved for a `BlockPreCommit` chunk differs from a legitimately-authenticated per-message signer identity the way the external report's `msg.sender` vs `_msgSender()` mismatch does in a meta-transaction relay context. This finding should be treated as an architectural analog/code-smell rather than a proven exploitable divergence — the two authentication mechanisms (transport-key vs content-signature) exist side-by-side in `stacks-signer/src/v0/signer.rs`, but I did not find code that actually lets an attacker forge one without controlling the corresponding StackerDB slot's private key, which would fall under "requires another signer's key" and be out of scope per the rules.

### Recommendation
Add a signature to `BlockPreCommit` (SIP-018 structured data over the block hash, matching the domain-separation approach used for `BlockAccepted`/`BlockRejection`), and have `handle_block_pre_commit` recover the voting identity from that signature via the same `recover_to_pubkey_without_validating_low_s` path used elsewhere in `stacks-signer/src/v0/signer.rs`, rather than trusting the StackerDB chunk-level signer key as an implicit stand-in for message-level authentication. This removes the asymmetry between how `Accepted`/`Rejected` votes and `BlockPreCommit` votes establish signer identity, which is the same category of defect (inconsistent sender-resolution mechanisms feeding one trust decision) flagged in the external report.

### Proof of Concept
Not reproducible from the indexed code alone: doing so requires exercising the StackerDB chunk-signing/slot-verification path (outside `stacks-signer/src/**` proper) to show that the address attributed to a `BlockPreCommit` chunk can differ from a hypothetical per-message signature over the pre-commit's block hash. I flag this as an architectural analog for further investigation rather than a demonstrated Critical/High finding.

### Citations

**File:** libsigner/src/v0/messages.rs (L207-209)
```rust
    StateMachineUpdate(StateMachineUpdate),
    /// The pre-commit message from signers for other signers to observe
    BlockPreCommit(Sha512Trunc256Sum),
```

**File:** stacks-signer/src/v0/signer.rs (L528-566)
```rust
                // try and gather signatures
                for (_slot_id, signer_public_key, message) in messages {
                    let signer_address = StacksAddress::p2pkh(self.mainnet, signer_public_key);
                    if !self.is_valid_signer(&signer_address) {
                        debug!("{self}: Received a message from an unknown signer. Ignoring...";
                            "signer_public_key" => ?signer_public_key,
                            "signer_address" => %signer_address,
                            "message" => ?message,
                        );
                        continue;
                    }
                    match message {
                        SignerMessage::BlockResponse(block_response) => {
                            #[cfg(any(test, feature = "testing"))]
                            if self.test_ignore_all_block_responses(block_response) {
                                continue;
                            }
                            self.handle_block_response(
                                stacks_client,
                                block_response,
                                sortition_state,
                            )
                        }
                        SignerMessage::StateMachineUpdate(update) => self
                            .handle_state_machine_update(signer_public_key, update, received_time),
                        SignerMessage::BlockPreCommit(signer_signature_hash) => {
                            #[cfg(any(test, feature = "testing"))]
                            if self
                                .test_ignore_all_pre_commits(&signer_address, signer_signature_hash)
                            {
                                continue;
                            }
                            self.handle_block_pre_commit(
                                stacks_client,
                                sortition_state,
                                &signer_address,
                                signer_signature_hash,
                            )
                        }
```

**File:** stacks-signer/src/v0/signer.rs (L1250-1301)
```rust
    /// Handle pre-commit message from another signer
    fn handle_block_pre_commit(
        &mut self,
        stacks_client: &StacksClient,
        sortition_state: &mut Option<SortitionsView>,
        stacker_address: &StacksAddress,
        block_hash: &Sha512Trunc256Sum,
    ) {
        let Some(mut block_info) = self.block_lookup_by_reward_cycle(block_hash) else {
            // A pre-commit for a block we have not seen proposed yet means the proposal
            // has not reached us. Log it at INFO: it is a direct signal that our view of
            // the proposal stream is behind the rest of the signer set.
            info!("{self}: Received block pre-commit for an unknown block, storing as pending";
                "signer_address" => %stacker_address,
                "signer_signature_hash" => %block_hash,
                "signer_weight" => self.signer_weights.get(stacker_address).copied().unwrap_or(0),
            );
            if let Err(e) = self
                .signer_db
                .add_pending_block_pre_commit_response(block_hash, stacker_address)
            {
                warn!("{self}: Failed to save pending block pre-commit response: {e:?}");
            }
            return;
        };
        // Always save the pre-commit - we will need to store signer responses for determining which
        // are misbehaving, offline, etc.
        // commit message is from a valid sender! store it
        self.signer_db
            .add_block_pre_commit(block_hash, stacker_address)
            .unwrap_or_else(|_| panic!("{self}: Failed to save block pre-commit"));

        let block_hash = block_info.block.header.signer_signature_hash();
        // do we have enough pre-commits to reach consensus?
        // i.e. is the threshold reached?
        //
        // Tally this up front, before the early returns below, so that every pre-commit we
        // receive can be logged with the running weight. Crossing this threshold is what
        // triggers our block response, so without it the wait for the threshold, which can
        // be minutes and is the bulk of a stalled block's latency, leaves no trace at all.
        let committers = self
            .signer_db
            .get_block_pre_committers(&block_hash)
            .unwrap_or_else(|_| panic!("{self}: Failed to load block commits"));

        let commit_weight = self.compute_signature_signing_weight(committers.iter());
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
```
