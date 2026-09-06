### Title
Same-height conflicting block can be signed after a signer's local "already signed" guard expires - ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
`SortitionData::check_latest_block_in_tenure` (used by both `check_tenure_change_confirms_parent` and `confirms_latest_block_in_same_tenure`) is the only per-signer guard that prevents a signer from signing a second, conflicting block at the same (or lower) height in a tenure it has already signed a block for. That guard is gated entirely on `SortitionData::get_tenure_last_block_info`, which drops the previously-signed block from consideration once `tenure_last_block_proposal_timeout` seconds have elapsed since the signature was placed (`signed_self.max(signed_group)`). Once the window lapses, the function returns `None`, and `check_latest_block_in_tenure` falls straight through to `client.get_tenure_tip`, which—since the earlier block never reached the 70% global-acceptance threshold and was therefore never pushed into the node's chainstate—still reports the previous tip, so the check answers `Ok(true)` (proposal is "higher") for a brand-new, height-colliding block. A single miner (one slot, no signer collusion needed) can exploit this purely with proposal timing.

### Finding Description
The invariant the signer network relies on is: once a signer has signed block A at height H in a tenure, it must never sign a different block B at height ≤ H in the same tenure (this is the local anti-equivocation guard, since consensus safety depends on each signer emitting at most one signature per height per tenure). This invariant is implemented only through `check_latest_block_in_tenure`: [1](#0-0) 

The freshness of the recorded "last signed block" is entirely a function of wall-clock time since the signature was placed, not of whether the block was actually superseded on-chain or globally accepted: [2](#0-1) 

If `tenure_last_block_proposal_timeout` has elapsed since a signer signed block A (this can legitimately happen if the tenure stalls, e.g. because A never reached 70% of the signer weight in time — which is exactly the state a miner deliberately withholding follow-up proposals can create), `get_tenure_last_block_info` returns `None` for that tenure. `check_latest_block_in_tenure` then has nothing to compare the new proposal against and defers to the node's view via `client.get_tenure_tip`: [3](#0-2) 

Because block A was only locally/partially signed and never crossed the 70% weight threshold, it was never handed to the stacks-node and never entered the node's chainstate (`postblock_proposal.rs`'s node-side checks like `check_block_builds_on_highest_block_in_tenure` only see committed chainstate, not in-flight signer approvals). The node's `get_tenure_tip` therefore still reports the tip *before* height H, so `tip.height() < block.header.chain_length` is `true`, and the brand-new, conflicting block B at the same height H passes the check. The signer then proceeds to sign B, even though it already signed a *different* block A at that same height in that same tenure.

If the miner engineers this on a per-signer, staggered timing basis (i.e., waits until each signer's freshness window individually lapses, or the tenure genuinely stalls near the timeout boundary because A is one signature short of quorum), it is possible for one pool of signers to hold signatures on A and another (later) pool to accumulate signatures on the conflicting B, each pool believing its `checked_proposal` block is the single valid one for that height. This breaks the "one signed block per height per tenure" equality that other equality checks (e.g. `verify_signer_signatures`'s weight-threshold logic in `stackslib/src/chainstate/nakamoto/mod.rs`) assume never gets violated at the signature-collection layer — the aggregated-weight/verified-accepts invariant is computed per-block, not across a height, so nothing else in the pipeline prevents two different blocks at the same height from separately accumulating weight if the signer set is not unanimous about which one is "the" locally signed tip.

### Impact Explanation
This is a signer-side equivocation-guard bypass: a signer can be induced to sign a second, conflicting block at a height where it previously signed a different block, purely by timing (no majority of signers, no stolen keys, no auth token required — a single miner controls proposal timing). This matches the Critical impact class defined by the rules ("a rejection recounted as acceptance" is a sibling case; here it's "a signer signing a conflicting block" directly). If enough signers experience the same timeout-driven amnesia independently (plausible because it's driven by the same `tenure_last_block_proposal_timeout` config value replicated across the signer set), a conflicting block could gather signature weight it should never have been eligible for, threatening the safety of the "at most one canonical block per height per tenure" guarantee the signer set is meant to uphold.

### Likelihood Explanation
Requires only a single miner (the current tenure's one-slot leader) to withhold or delay follow-up block broadcasts long enough for `tenure_last_block_proposal_timeout` to lapse on the earlier signed-but-not-yet-globally-accepted block, then propose a distinct block at the same height. `tenure_last_block_proposal_timeout` is a signer-configurable value intended to handle miner crashes/stalls, so the window is a normal part of protocol operation rather than an edge condition — no bug in a rare code path, no majority collusion, no external compromise needed.

### Recommendation
The "already signed a block at this height in this tenure" guard should not be allowed to silently disappear purely due to elapsed wall-clock time on the *previous* block's signature. Before falling through to `client.get_tenure_tip` on timeout, `check_latest_block_in_tenure` (or its caller) should still positively confirm that the timed-out block is either (a) confirmed by the new proposal's ancestry, or (b) actually superseded by a legitimate reorg via the same tenure/parent-tenure-choice logic used in `check_parent_tenure_choice`, rather than treating "no fresh info" as "assume the new block is fine." At minimum, before signing a new block at a height where the local DB has *any* record of a previously signed block (fresh or not), the signer should require that new block's ancestry to be consistent with (build on top of, not conflict with) that prior record, unless a supersession has been explicitly recorded via `mark_tenure_superseded`.

### Proof of Concept
1. Miner M is the sole leader of tenure T, and proposes block A at height H; a subset of signers (below the 70% weight threshold) sign A via `PreCommitted`/`LocallyAccepted`, recording `signed_self`/`signed_group` timestamps in `signerdb.rs`.
2. M deliberately withholds any further proposal until `tenure_last_block_proposal_timeout` elapses past those timestamps (this is entirely miner-controlled timing; no other party is needed).
3. `SortitionData::get_tenure_last_block_info` now returns `None` for tenure T (per `stacks-signer/src/chainstate/mod.rs:317-364`), because the last signed block's `signed_self.max(signed_group)` is stale.
4. M proposes a different block B at the same height H (or lower), built on a different transaction set/parent state but still consensus-hash/tenure-consistent.
5. Each signer's `check_latest_block_in_tenure` call for B skips the height-conflict rejection (since `last_block_info` is `None`), falls through to `client.get_tenure_tip`, which — since A was never pushed to the node (didn't reach 70%) — still reports the pre-H tip, so the check returns `Ok(true)`.
6. The signer proceeds to validate/sign B, despite having already signed A at the same height in the same tenure — an equivocation that no other check in the pipeline blocks. [4](#0-3) [5](#0-4)

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L317-364)
```rust
    /// Get the last signed block from the given tenure if it has not timed out.
    /// Even globally accepted blocks are allowed to be timed out, as that
    /// triggers the signer to consult the Stacks node for the latest globally
    /// accepted block. This is needed to handle Bitcoin reorgs correctly.
    ///
    /// The timeout window is measured from the last time a signature actually covered the
    /// block: our own (`signed_self`) or the observed group/global acceptance
    /// (`signed_group`), whichever is later, matching how `get_signed_conflicts` measures
    /// endorsement freshness. `approved_time` is deliberately not used: it is stamped at
    /// pre-commit, which carries no signature, so it would close the window early. This also
    /// means a globally accepted block we never signed ourselves gets a full window from the
    /// time its acceptance was observed, rather than timing out instantly for lack of a
    /// timestamp.
    pub fn get_tenure_last_block_info(
        consensus_hash: &ConsensusHash,
        signer_db: &SignerDb,
        tenure_last_block_proposal_timeout: Duration,
    ) -> Result<Option<BlockInfo>, ClientError> {
        // Get the last signed block in the tenure
        let last_signed_block = signer_db
            .get_last_signed_block(consensus_hash)
            .map_err(|e| ClientError::InvalidResponse(e.to_string()))?;

        let Some(block_info) = last_signed_block else {
            return Ok(None);
        };

        // `approved_time` may hold the pre-commit time; use the actual signature time.
        let Some(signed_over_time) = block_info.signed_self.max(block_info.signed_group) else {
            return Ok(None);
        };

        if signed_over_time.saturating_add(tenure_last_block_proposal_timeout.as_secs())
            > get_epoch_time_secs()
        {
            // The last accepted block is not timed out, return it
            Ok(Some(block_info))
        } else {
            // The last accepted block is timed out
            info!(
                "Last accepted block has timed out";
                "signer_signature_hash" => %block_info.block.header.signer_signature_hash(),
                "signed_over_time" => signed_over_time,
                "state" => %block_info.state,
            );
            Ok(None)
        }
    }
```

**File:** stacks-signer/src/chainstate/mod.rs (L376-420)
```rust
    pub fn check_latest_block_in_tenure(
        tenure_id: &ConsensusHash,
        block: &NakamotoBlock,
        signer_db: &mut SignerDb,
        client: &StacksClient,
        tenure_last_block_proposal_timeout: Duration,
        reorg_attempts_activity_timeout: Duration,
    ) -> Result<bool, ClientError> {
        let last_block_info = SortitionData::get_tenure_last_block_info(
            tenure_id,
            signer_db,
            tenure_last_block_proposal_timeout,
        )?;

        if let Some(info) = last_block_info {
            // N.B. this block might not be the last globally accepted block across the network;
            // it's just the highest one in this tenure that we know about.  If this given block is
            // no higher than it, then it's definitely no higher than the last globally accepted
            // block across the network, so we can do an early rejection here.
            if block.header.chain_length <= info.block.header.chain_length {
                warn!(
                    "Miner's block proposal does not confirm as many blocks as we expect";
                    "proposed_block_consensus_hash" => %block.header.consensus_hash,
                    "signer_signature_hash" => %block.header.signer_signature_hash(),
                    "proposed_chain_length" => block.header.chain_length,
                    "expected_at_least" => info.block.header.chain_length + 1,
                );
                if info.signed_group.is_none_or(|signed_time| {
                    signed_time + reorg_attempts_activity_timeout.as_secs() > get_epoch_time_secs()
                }) {
                    // Note if there is no signed_group time, this is a locally accepted block (i.e. tenure_last_block_proposal_timeout has not been exceeded).
                    // Treat any attempt to reorg a locally accepted block as valid miner activity.
                    // If the call returns a globally accepted block, check its globally accepted time against a quarter of the block_proposal_timeout
                    // to give the miner some extra buffer time to wait for its chain tip to advance
                    // The miner may just be slow, so count this invalid block proposal towards valid miner activity.
                    if let Err(e) = signer_db.update_last_activity_time(
                        &block.header.consensus_hash,
                        get_epoch_time_secs(),
                    ) {
                        warn!("Failed to update last activity time: {e}");
                    }
                }
                return Ok(false);
            }
        }
```

**File:** stacks-signer/src/chainstate/mod.rs (L450-477)
```rust
        let tip = match client.get_tenure_tip(tenure_id) {
            Ok(tip) => tip.anchored_header,
            Err(e) => {
                warn!(
                    "Failed to fetch the tenure tip for the parent tenure: {e:?}. Assuming proposal is higher than the parent tenure for now.";
                    "proposed_block_consensus_hash" => %block.header.consensus_hash,
                    "signer_signature_hash" => %block.header.signer_signature_hash(),
                    "parent_tenure" => %tenure_id,
                );
                return Ok(true);
            }
        };
        if let Some(nakamoto_tip) = tip.as_stacks_nakamoto() {
            // If we have seen this block already, make sure its state is updated to globally accepted.
            // Otherwise, don't worry about it.
            if let Ok(Some(mut block_info)) =
                signer_db.block_lookup(&nakamoto_tip.signer_signature_hash())
            {
                if block_info.state != BlockState::GloballyAccepted {
                    if let Err(e) = block_info.mark_globally_accepted() {
                        warn!("Failed to mark block as globally accepted: {e}");
                    } else if let Err(e) = signer_db.insert_block(&block_info) {
                        warn!("Failed to update block info in db: {e}");
                    }
                }
            }
        }
        Ok(tip.height() < block.header.chain_length)
```
