### Title
Signer forgets an already-signed block after a fixed `tenure_last_block_proposal_timeout` window, allowing it to sign a conflicting block at the same height - ([File: stacks-signer/src/chainstate/mod.rs])

### Summary
`SortitionData::get_tenure_last_block_info` discards its knowledge of the last block a signer signed in a tenure once a fixed, hardcoded-by-default window (`tenure_last_block_proposal_timeout`, default 30s) has elapsed since the signature was produced, regardless of whether that block has actually propagated through signature aggregation and reached the node as its canonical tip. When that window lapses before the block is durably reflected in the node's tenure tip, `check_latest_block_in_tenure` falls back to comparing against the node's (still stale) tip and lets a *competing* block at the same chain height pass its "does the proposal confirm what we expect" veto, opening the door to the signer set producing valid signatures over two conflicting blocks at the same height in the same tenure.

### Finding Description
`get_tenure_last_block_info` retrieves the last *signed* block in a tenure, but only if it has not "timed out": [1](#0-0) 

The window used, `tenure_last_block_proposal_timeout`, is a single fixed value (default 30 seconds, see `DEFAULT_TENURE_LAST_BLOCK_PROPOSAL_TIMEOUT_SECS`) that is applied uniformly regardless of how long it actually takes for:
- the signer set to gather a 70% signature threshold on that block, and
- the aggregated signature to be pushed to, and processed by, the Stacks node (`NewBlock` event → `mark_globally_accepted`). [2](#0-1) 

Once that fixed window elapses, `get_tenure_last_block_info` returns `None` **even if the block was never actually confirmed by the node**, by design ("Even globally accepted blocks are allowed to be timed out ... This is needed to handle Bitcoin reorgs correctly"). `check_latest_block_in_tenure` then falls through to comparing against the node's *own* (possibly still-stale) tenure tip: [3](#0-2) 

If the node has not yet incorporated the earlier signed block N (which can legitimately take longer than the fixed timeout under real-world signature aggregation/network latency, or can be deliberately elongated by delaying gossip of pre-commits/signatures for that block), `client.get_tenure_tip` still returns the pre-N tip. The subsequent check `tip.height() < block.header.chain_length` is then evaluated against a *competing* block N' proposed by the miner at the exact same `chain_length` as N, and it succeeds, so `check_latest_block_in_tenure` returns `true` (i.e., "no veto") for N' instead of rejecting it as a duplicate/conflicting height. This code path is invoked both at proposal-arrival time and again in `check_block_against_signer_db_state`, which is explicitly the last-chance re-check before a signer commits its signature: [4](#0-3) 

The result: a signer that already signed N can, once the fixed timeout has elapsed relative to *its own* signing time (not relative to when N actually became durable/canonical), go on to also sign N' at the same height in the same tenure — breaking the intended "one signed block per height per tenure" invariant that the rest of the chainstate logic (`DuplicateBlockFound`, `SortitionViewMismatch`) is built to enforce.

### Impact Explanation
This is a Critical-class break: a signer can be induced to sign a conflicting block at a height it has already signed for, within the very same tenure, without needing to compromise a majority of signers, another signer's key, or the auth_token. The trigger is entirely a function of a fixed, one-size-fits-all timing constant that does not scale with the actual (network- and threshold-dependent) time required for a block's signature to become durable at the node. This is directly analogous to the referenced report's core flaw: a "cooldown"-style window hardcoded independent of the real duration of the process it's meant to gate, causing either premature veto-loss (attack surface here) or over-long stalls elsewhere in the same code (`reorg_attempts_activity_timeout`, `first_proposal_burn_block_timing`) which share the identical fixed-window pattern.

### Likelihood Explanation
The precondition — signature aggregation + node ingestion of a block taking longer than the configured `tenure_last_block_proposal_timeout` (30s default) — is realistic under normal network jitter, especially for larger signer sets, slow StackerDB propagation, or a miner (a single, one-slot actor) that simply delays announcing/relaying its own block N and then submits a second, differently-built block N' at the same height once the fixed window has lapsed for at least some signers. Because the timeout is measured purely from local signing time rather than confirmed global state, different signers can independently and legitimately fall out of sync with the node's tip at different times, making the race exploitable without any single signer needing majority collusion.

### Recommendation
Do not let `get_tenure_last_block_info` unconditionally discard the last-signed-block record purely on a wall-clock timeout. Instead, gate the fallback on positively confirming (via the node) that the previously signed block has been superseded/orphaned, or extend the window dynamically based on observed signature/propagation progress (e.g., only forget the record once the node's tenure tip has actually advanced past it, or once a Bitcoin-fork condition is independently confirmed) rather than relying on a static duration decoupled from the real distributed-confirmation latency.

### Proof of Concept
1. Signer set (N signers) is evaluating tenure T. Miner proposes block `N` at height `h`; the signer set reaches a 70% signature threshold on `N`, but broadcasting the aggregated signature to the Stacks node, and the node's processing of the resulting `NewBlock` event, takes noticeably longer than `tenure_last_block_proposal_timeout` (30s default) — e.g. due to StackerDB propagation delay or a slow node.
2. At `t = signed_over_time + 31s` (i.e., just after the fixed timeout), `get_tenure_last_block_info` for tenure T returns `None` for any signer that has not yet observed the node's tip advance to `N`, per [5](#0-4) .
3. The miner (or a colluding relay) proposes competing block `N'` at the same height `h` in tenure T, built on a divergent transaction set.
4. `check_latest_block_in_tenure` for `N'` calls `client.get_tenure_tip(tenure_id)`, which still returns the pre-`N` tip (since the node has not processed `N` yet); the check `tip.height() < N'.header.chain_length` evaluates true, so the veto that would normally catch a same-height conflicting proposal is bypassed, per [6](#0-5) .
5. The signer proceeds to sign `N'`, while (potentially) other signers or its own earlier commitment still stand behind `N` — producing two valid, conflicting signed blocks at the same height/tenure.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L330-364)
```rust
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

**File:** stacks-signer/src/chainstate/mod.rs (L376-478)
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

        // A block we have only pre-committed to must NOT veto this proposal, but, similar to above
        // this should still count as activity for the miner.
        let last_accepted_block = signer_db
            .get_last_accepted_block(tenure_id)
            .map_err(|e| ClientError::InvalidResponse(e.to_string()))?;
        if let Some(info) = last_accepted_block {
            let is_fresh_pre_commit = info.state == BlockState::PreCommitted
                && info.approved_time.is_some_and(|approved_time| {
                    approved_time.saturating_add(tenure_last_block_proposal_timeout.as_secs())
                        > get_epoch_time_secs()
                });
            if is_fresh_pre_commit && block.header.chain_length <= info.block.header.chain_length {
                info!(
                    "Miner's block proposal conflicts with a block we have only pre-committed to. Counting it as miner activity, but not rejecting the proposal.";
                    "proposed_block_consensus_hash" => %block.header.consensus_hash,
                    "signer_signature_hash" => %block.header.signer_signature_hash(),
                    "proposed_chain_length" => block.header.chain_length,
                    "pre_committed_signer_signature_hash" => %info.block.header.signer_signature_hash(),
                    "pre_committed_chain_length" => info.block.header.chain_length,
                );
                if let Err(e) = signer_db
                    .update_last_activity_time(&block.header.consensus_hash, get_epoch_time_secs())
                {
                    warn!("Failed to update last activity time: {e}");
                }
            }
        }

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
    }
```

**File:** stacks-signer/src/config.rs (L42-43)
```rust
const DEFAULT_FIRST_PROPOSAL_BURN_BLOCK_TIMING_SECS: u64 = 60;
const DEFAULT_TENURE_LAST_BLOCK_PROPOSAL_TIMEOUT_SECS: u64 = 30;
```

**File:** stacks-signer/src/v0/signer.rs (L1842-1866)
```rust
        // Ensure that the block is the last block in the chain of its current tenure.
        match SortitionData::check_latest_block_in_tenure(
            &proposed_block.header.consensus_hash,
            proposed_block,
            &mut self.signer_db,
            stacks_client,
            self.proposal_config.tenure_last_block_proposal_timeout,
            self.proposal_config.reorg_attempts_activity_timeout,
        ) {
            Ok(is_latest) => {
                if !is_latest {
                    warn!(
                        "Miner's block proposal does not confirm as many blocks as we expect";
                        "proposed_block_consensus_hash" => %proposed_block.header.consensus_hash,
                        "proposed_block_signer_signature_hash" => %signer_signature_hash,
                        "proposed_chain_length" => proposed_block.header.chain_length,
                    );
                    Some(self.create_block_rejection(
                        RejectReason::SortitionViewMismatch,
                        proposed_block,
                    ))
                } else {
                    None
                }
            }
```
