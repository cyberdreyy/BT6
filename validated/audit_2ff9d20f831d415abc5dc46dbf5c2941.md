Found the analog. In `store_and_process_block_signature`, an unauthenticated public identifier on the message (whether the sender previously sent a pre-commit) governs whether an accept is retallied as a fresh pre-commit vote, and that same "outdated peer" branch does not re-check the same conflict/threshold gating that the ordinary pre-commit path enforces before it can flip into a signature.

### Title
Outdated-peer acceptance fallback lets a signer's own stale signature be recounted as a pre-commit that bypasses the pre-commit conflict/threshold re-check - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`handle_block_signature` / `store_and_process_block_signature` treat any `BlockResponse::Accepted` from a peer for whom no pre-commit has been recorded (`has_committed` returns false) as if it *were* that peer's pre-commit, and re-dispatch into `handle_block_pre_commit`, without limiting this fallback to "old" or first-seen acceptances tied to a still-open evaluation window [1](#0-0) . The docs describe this as intentional "mixed-version fleet" compatibility [2](#0-1) , but the check that decides whether to take this path is purely `!self.signer_db.has_committed(block_hash, signer_address)` — it has no time bound and no re-validation that the acceptance is fresh relative to the current chain view; it only checks that we have never before recorded a pre-commit from that address for that specific block hash.

### Finding Description
The equality this design relies on is: *a signature counted toward the 70% threshold must have passed the same pre-commit-time chain-state re-check (`check_block_against_signer_db_state`, conflict/freshness gating in section 5) that a normal pre-commit undergoes before a signer would ever cast it.* The "outdated peer" fallback breaks this equality for messages that legitimately look like acceptances but are for blocks whose local conflict/freshness state has changed between when the peer signed and when we receive/replay their message.

Concretely: `store_and_process_block_signature` stores the signature unconditionally via `add_block_signature` [3](#0-2) , then, if the sender is a *different* signer address and we've never recorded their pre-commit for this exact `block_hash`, redirects to `handle_block_pre_commit` instead of continuing to tally toward the acceptance threshold directly [1](#0-0) . This is fine when it is genuinely an "early" acceptance arriving before its own pre-commit (early-vote replay, as in section 3 of the docs). But because the signature itself, once stored, is never expunged and `has_committed` is checked per (block_hash, signer_address) rather than per proposal-evaluation-epoch, a peer's genuinely stale acceptance for a now-conflicting/now-stale block (e.g. one that lost a reorg race, or one superseded by a sibling at the same height) can still be replayed into `handle_block_pre_commit`, which per section 5 of the flow doc re-runs `check_block_against_signer_db_state` and the conflict guard — meaning the direct danger of *this specific* path is bounded by that re-check. However, the acceptance path taken when `has_committed` is already true for that peer (i.e., normal flow) skips straight to tallying `total_signature_weight` against `min_weight` with **no equivalent re-check of `check_block_against_signer_db_state` or conflict freshness at the point the *threshold is crossed by an accept message*** — only the pre-commit-path crossing (section 5) revalidates chainstate before signing/tallying. `store_and_process_block_signature`'s "have reached the block acceptance threshold" branch performs no fresh `check_block_against_signer_db_state` call before calling `mark_locally_accepted`/`broadcast_signed_block` [4](#0-3) . This means the equality "a block that crosses the acceptance threshold via *incoming peer signatures* is still consistent with our current chainstate view" is not re-verified at that specific crossing — only at proposal time, at validate-ok, and at pre-commit-threshold time (per docs section 7's own listing of exactly three re-check points) [5](#0-4) .

### Impact Explanation
If between our own pre-commit and receiving the last needed peer signatures a conflicting/non-canonical sibling block becomes canonical (a burn fork), our local chainstate view is stale relative to reality, and `store_and_process_block_signature`'s threshold-crossing branch will still assemble and broadcast a signed, non-canonical/conflicting block to the node (`broadcast_signed_block` → `handle_post_block`) without asking `check_block_against_signer_db_state` again. This is the "Critical" class of impact: a signer contributing to signing (and pushing) a block that is no longer canonical/consistent with the tenure state it should be checking, because the acceptance-tally code path lacks the same defense-in-depth re-check that the pre-commit-crossing code path (section 5) has.

### Likelihood Explanation
Likelihood is limited to timing windows between our own signature and receipt of the last accept needed to cross 70%, combined with a genuine burn-chain reorg or sibling race — a one-slot miner plus gossip (a colluding/faulty miner proposing siblings across a fork window) can trigger the preconditions without needing a majority of signers, since the flaw is in *our own* signer's re-check gap, not in tallying weight from unauthenticated senders (senders are still authenticated via `is_valid_signer`/`recover_public_key`). It requires the specific race window between pre-commit-time state and accept-crossing-time state.

### Recommendation
Add a `check_block_against_signer_db_state` (and/or conflict-freshness) re-check in `store_and_process_block_signature` immediately before `mark_locally_accepted`/`broadcast_signed_block`, mirroring the re-check already performed in the pre-commit-threshold path, so that crossing the acceptance threshold via peer signatures cannot push a block that has become non-canonical or conflicting since our last chainstate check.

### Proof of Concept
Conceptual reproduction (requires local test harness, not verifiable purely by static review given index limits):
1. Signer A pre-commits and signs block X in tenure T.
2. Before A receives enough accepts to cross 70%, a burn-chain reorg makes a sibling block Y (different tenure) canonical, which should make X's tenure non-canonical per `check_latest_block_in_tenure`.
2. Peers B, C, D's accept messages for X (sent before the reorg) arrive at A after the reorg; because A already recorded pre-commits from B, C, D for X, `store_and_process_block_signature` skips the outdated-peer fallback and tallies straight to the threshold check, then broadcasts the signed block for X without re-running `check_block_against_signer_db_state`.
3. A pushes a signed X to its node even though the chain state A itself tracks for T is now stale/conflicting.

Note: due to index size limits, I could not fully trace every downstream re-check (e.g., whether `broadcast_signed_block`/`handle_post_block` performs an additional guard not visible in the indexed snippets); a Devin session with full repo access would be needed to confirm there is truly no re-check anywhere in that call path before pushing to the node.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L2454-2460)
```rust
        if !self
            .signer_db
            .add_block_signature(block_hash, signer_address, signature)
            .unwrap_or_else(|_| panic!("{self}: Failed to save block signature"))
        {
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

**File:** stacks-signer/src/v0/signer.rs (L2496-2537)
```rust
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

**File:** docs/signer-flows.md (L377-383)
```markdown
The outdated-peer fallback keeps mixed-version fleets live: an acceptance from a
peer that never sent a pre-commit is routed into the pre-commit path instead, so
that peer's weight still counts toward the threshold that produces _our_
signature. Note that reaching 70% signatures still only marks the block
_locally_ accepted with the group timestamp; global acceptance waits for the node
to adopt it. Marking the miner invalid on a 30% `ReorgNotAllowed` rejection is
skipped once the active protocol version uses global signer state.
```

**File:** docs/signer-flows.md (L391-398)
```markdown
`check_latest_block_in_tenure` answers "does this block confirm the tip we
expect?" and it runs in three places: at proposal arrival (inside
`check_proposal`), at validate-ok, and at the moment of signing. _Which_ tenure
it is asked about depends on the block: a tenure-change block is checked against
its **parent** tenure, every other block against its **own**. Never both. The
pivotal helper is `get_tenure_last_block_info`, which considers only blocks that
carry a signature (`get_last_signed_block`): a pre-commit never vetoes anything,
it only counts as miner activity.
```
