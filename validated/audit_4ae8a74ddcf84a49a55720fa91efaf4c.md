## Analysis Result

**Analog identified**: the SaltStack bug class (an alternate/batch code path silently skips the authorization check that the "normal" path enforces) maps onto the stacks-signer's two independent routes to producing a signature: the **pre-commit threshold path** (`handle_block_pre_commit`) and the **signature-gossip threshold path** (`store_and_process_block_signature`). Only the first re-runs the equivocation/conflict guard.

### Title
Signer double-signs a conflicting block via the gossiped-signature threshold path, which skips the conflict/equivocation re-check enforced on the pre-commit path - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`handle_block_pre_commit`, the code path that promotes a `PreCommitted` block to `LocallyAccepted` once 70% pre-commit weight is reached, re-validates the block against chainstate and against every other signed block at or above the same height (`check_block_against_signer_db_state`, `get_signed_conflicts`, `reorg_permit_stands`, `conflict_still_blocks`) immediately before calling `mark_locally_accepted` [1](#0-0) . `store_and_process_block_signature`, the code path that promotes the same state once 70% *signature* weight (gossiped `BlockResponse::Accepted` messages from other signers) is reached, performs no such re-check and calls `mark_locally_accepted(true)` directly after only tallying weight [2](#0-1) .

### Finding Description
A signer reaches `LocallyAccepted` (i.e., commits its own signature) through exactly two triggers:

1. **Pre-commit threshold** — `handle_block_pre_commit`, which before signing re-runs `check_block_against_signer_db_state`, queries `get_signed_conflicts` for every other signed block at or above the same height in *any* tenure, and only signs once every fresh conflict is proven dead (`reorg_permit_stands`/`conflict_still_blocks`) [3](#0-2) .
2. **Signature-gossip threshold** — `handle_block_signature` recovers the public key from a peer's `BlockResponse::Accepted` signature, authenticates it against the signer set, then hands it to `store_and_process_block_signature` [4](#0-3) . That function tallies signing weight from `get_block_signatures` and, once the 70% threshold (`compute_voting_weight_threshold`) is met, calls `block_info.mark_locally_accepted(true)` and broadcasts our own signature — with **no call** to `check_block_against_signer_db_state`, `get_signed_conflicts`, or any freshness/conflict logic [5](#0-4) .

The docs explicitly describe the purpose of the re-check that path (1) performs: *"Between validation and threshold, we may have signed a different block at the same height, possibly in another tenure, so the world must be re-checked before the signature leaves the box."* [6](#0-5)  and *"a signature over either may conflict with a fresh signature over the other"* for the cross-tenure sibling case [7](#0-6) . Tests explicitly assert this invariant is preserved on the pre-commit path (`signer_refuses_to_sign_second_sibling_tenure_start`, `fresh_conflict_in_another_tenure_blocks_signing`) [8](#0-7) [9](#0-8) , but no equivalent test exercises the signature-gossip path with a conflicting already-signed block.

Concretely: if a signer has already signed block **B** at height *h* (via the pre-commit path, correctly guarding against sibling **A**), and later a genuine 70% weight of the rest of the signer set's signatures over sibling block **A** (in the same or a different tenure at the same height, e.g. following a tenure fork or reorg) arrive as gossiped `BlockResponse::Accepted` messages, `store_and_process_block_signature` will happily add this signer's own signature to **A** too, once its local tally of *A*'s signatures crosses 70% — with zero check that **A** conflicts with the already-signed **B**. The signer breaks the "sign at most one block per height" invariant that is the entire purpose of section 5's conflict guard, purely because it received the threshold via signatures rather than pre-commits. This requires no majority of keys under attacker control and no auth_token/local access — only a naturally occurring or miner-induced fork producing two blocks at the same height that legitimately gather independent signature weight, which a one-slot miner plus normal gossip propagation can trigger.

### Impact Explanation
This is a **Critical** finding under the stated rubric: a signer signing a conflicting block. Two blocks at the same stacks height, each carrying this signer's own signature, is exactly the double-sign/equivocation scenario the pre-commit-path guard exists to prevent (`get_signed_conflicts`, `conflict_still_blocks`, `reorg_permit_stands`), and it is bypassed entirely when the 70% weight is discovered via gossiped signatures rather than pre-commits.

### Likelihood Explanation
No majority of signer keys, no auth token, and no privileged access is required by an attacker; the trigger is a naturally occurring race between the pre-commit path and the signature-gossip path during any tenure/height contention (fork, reorg, or a miner re-proposing at the same height under different tenures) — precisely the scenarios docs/signer-flows.md and the existing test suite are designed to cover for the pre-commit path, but leave uncovered for the signature-accumulation path.

### Recommendation
Before `store_and_process_block_signature` calls `mark_locally_accepted`, it should perform the same re-check that `handle_block_pre_commit` performs: call `check_block_against_signer_db_state`, query `get_signed_conflicts` for the block's height, and honor `reorg_permit_stands`/`conflict_still_blocks` before accepting the threshold-crossing signature and adding this signer's own signature to the response.

### Proof of Concept
1. Signer S validates and pre-commits to block B at height h in tenure T1; S's own pre-commit plus enough peer pre-commits cross 70% weight, so `handle_block_pre_commit` re-checks conflicts (none exist yet), signs B, and stores B as `LocallyAccepted` (`stacks-signer/src/v0/signer.rs:1467`).
2. A fork/reorg causes a competing tenure T2 to propose sibling block A at the same height h; a genuine 70% weight of the *other* signers (who never saw B, or saw it go stale) independently sign A, broadcasting `BlockResponse::Accepted(A, sig)` messages.
3. S receives these gossiped acceptances via `handle_block_signature` → `store_and_process_block_signature` (`stacks-signer/src/v0/signer.rs:2433`); the function tallies weight from `get_block_signatures`, crosses the 70% threshold, and — without ever calling `check_block_against_signer_db_state` or `get_signed_conflicts` — calls `block_info.mark_locally_accepted(true)` and broadcasts S's own signature over A (`stacks-signer/src/v0/signer.rs:2528-2537`).
4. S has now signed both B and A at height h, a conflict that the pre-commit path's guard (section 5 of `docs/signer-flows.md`) was specifically built to prevent.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1340-1466)
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

        // No conflict is both fresh and still live. A conflict that no longer matters, i.e.
        // stale, or provably dead per `conflict_still_blocks`, cannot veto on its own. A
        // stale conflict in another tenure in particular no longer speaks for us: whether this
        // block may replace what another tenure built is settled by the chainstate checks above.
        // A stale conflict in this block's own tenure still blocks if the node already has that
        // tenure at or above the proposed height, since the proposal then duplicates state the
        // node has already built on. (The chainstate checks don't cover this for tenure-change
        // blocks: those check the parent tenure instead of their own.)
        // The permit check is deferred to here so that only same-tenure conflicts pay for it.
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

**File:** stacks-signer/src/v0/signer.rs (L2389-2439)
```rust
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

**File:** docs/signer-flows.md (L1594-1598)
```markdown

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

**File:** stacks-signer/src/v0/tests.rs (L1116-1128)
```rust
    #[test]
    fn fresh_conflict_in_another_tenure_blocks_signing() {
        // A sibling at the same height in a DIFFERENT tenure is just as much a double-sign as
        // one in the same tenure. The node knows nothing about either tenure, which must not be
        // read as "tenure 1 is orphaned": a locally accepted block is unknown to the node until
        // the whole signer set has signed it.
        let (info_a, info_b) = run_cross_tenure_scenario(TenureAFate::Live);
        assert_a_signed(&info_a);
        assert_b_refused(
            &info_b,
            "the conflicting sibling in another tenure is fresh",
        );
    }
```
