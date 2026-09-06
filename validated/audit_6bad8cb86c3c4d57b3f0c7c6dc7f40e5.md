Based on my investigation, I found a genuine analog to the Lybra "keeper automatically becomes provider" bug class in the `store_and_process_block_signature` outdated-peer fallback.

### Title
A miner-adjacent gossip signature can be silently re-typed into a pre-commit vote, letting an untrusted `BlockAccepted` message count toward the signing threshold without going through pre-commit validity gating - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`store_and_process_block_signature` treats any peer's `BlockAccepted` signature as an implicit pre-commit if that peer never explicitly sent a `BlockPreCommit` message for the block [1](#0-0) . This is exactly the shape of the Lybra bug: an action taken for one purpose (a signature/acceptance) is silently reinterpreted by the receiver as consent for a different, more consequential role (a pre-commit vote that feeds the 70% threshold), without the signer that produced it having gone through the intended checks for that role.

### Finding Description
In the intended flow, a signer only pre-commits after re-validating the block locally (`handle_block_validate_ok` → `check_block_against_signer_db_state` → `mark_pre_committed`) [2](#0-1) , and only signs after the pre-commit threshold and conflict re-checks in `handle_block_pre_commit` pass [3](#0-2) .

However, `handle_block_signature` accepts any `BlockAccepted` whose recovered signer is a member of the signer set (`is_valid_signer`) [4](#0-3)  and forwards it to `store_and_process_block_signature`, which — if that address has no recorded pre-commit (`has_committed`) — routes the message into `handle_block_pre_commit` as if it *were* a pre-commit from that address [1](#0-0) . `handle_block_pre_commit` then calls `add_block_pre_commit` unconditionally to persist this synthetic vote and tallies it toward `commit_weight` for the 70% pre-commit threshold [5](#0-4) .

The equality this breaks: "pre-commit weight" is supposed to represent signers who independently ran validation and *chose* to signal willingness before spending their signature; here it is silently populated by re-labeling a different message type (a signature, which is a stronger and different-typed act) from a signer address that never actually broadcast a pre-commit. The receiver, not the claimed sender, decides that the address participated in the pre-commit round. The design intent documented for the "outdated peer" case is compatibility with older signer versions that skip pre-commit and jump straight to signature, but the code applies the same rewrite for **any** signer whose pre-commit message wasn't seen — including messages lost to network jitter, StackerDB replication lag, or reordering, and to an attacker-controlled minority sub-protocol version. There is no cryptographic or protocol-level marker distinguishing "legitimately an old client" from "network dropped the pre-commit."

### Impact Explanation
This does not by itself let an attacker produce a signature over an invalid/non-canonical block, since `handle_block_pre_commit` still re-runs `check_block_against_signer_db_state` and the conflict/fresh-sibling guard before any local signature is produced [6](#0-5) . However, it does inflate the *pre-commit* threshold weight using a message class (`BlockAccepted`) that was never intended to satisfy it, and does so unconditionally — with no explicit versioning check gating the "outdated peer" reinterpretation, only the absence of a recorded pre-commit. This is a design-level equivocation-adjacent issue: it lets one message type be recounted as another type of vote, an analog of the "rejection recounted as acceptance" class in spirit (a message intended for a different consensus round is recounted into this round's tally). Under the stated rules, this is a High-severity liveness/consensus concern rather than a proven Critical safety break, since the underlying signature production still passes through the chainstate re-check.

### Likelihood Explanation
Reachable by any single signer (or by miner-adjacent gossip relaying a legitimate signer's own `BlockAccepted`) without needing majority collusion: any signer that signs a block and whose pre-commit message is dropped, delayed, or simply never sent will have its acceptance recounted as a pre-commit by every peer that missed the pre-commit, increasing the observed pre-commit tally without an actual pre-commit round having occurred for that weight.

### Recommendation
Gate the "treat acceptance as pre-commit" fallback on an explicit protocol-version signal from the peer (e.g., only apply it when the sender is confirmed to be running a pre-pre-commit protocol version, rather than merely "we never saw a pre-commit from them"), or introduce a distinct pre-commit-vs-signature session marker so that a `BlockAccepted` cannot silently satisfy the pre-commit threshold for the current-protocol majority.

### Proof of Concept
Not independently reproduced with a live node/network in this investigation (index-only search); the causal chain is demonstrated purely via the code path cited above: `handle_block_signature` (recovers pubkey, checks `is_valid_signer`) → `store_and_process_block_signature` (checks `has_committed`, calls `handle_block_pre_commit` on failure) → `handle_block_pre_commit` (`add_block_pre_commit`, tallies `commit_weight`). No majority or additional key material is required to trigger the reinterpretation for any one signer's message.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1278-1298)
```rust
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
```

**File:** stacks-signer/src/v0/signer.rs (L1333-1392)
```rust
        if min_weight > commit_weight {
            debug!(
                "{self}: Not enough pre-committed to block {block_hash} (have {commit_weight}, need at least {min_weight}/{total_weight})"
            );
            return;
        }

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
```

**File:** stacks-signer/src/v0/signer.rs (L1946-1975)
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
```

**File:** stacks-signer/src/v0/signer.rs (L2401-2411)
```rust
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
```

**File:** stacks-signer/src/v0/signer.rs (L2462-2466)
```rust
        // If this isn't our own signature and we haven't seen a pre-commit from this signer yet, try treating it as a pre-commit in case the caller is running an outdated version
        if signer_address != &self.stacks_address && !self.signer_db.has_committed(block_hash, signer_address).inspect_err(|e| warn!("Failed to check if pre-commit message already considered for {signer_address:?} for {block_hash}: {e}")).unwrap_or(false) {
            self.handle_block_pre_commit(stacks_client, sortition_state, signer_address, block_hash);
            return;
        }
```
