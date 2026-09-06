### Title
Wall-clock-only conflict freshness check lets a single miner get a signer to sign two conflicting blocks at the same height across tenures - (File: `stacks-signer/src/v0/signer.rs`)

### Summary
`handle_block_pre_commit` guards against a signer double-signing conflicting blocks at the same height by querying `get_signed_conflicts` and only treating a conflict as blocking while it is "fresh" — i.e. `conflict.last_endorsed > freshness_cutoff`, where `freshness_cutoff` is a pure wall-clock value derived from `tenure_last_block_proposal_timeout`. Once that fixed timeout elapses since the signer's own signature (or observed group signature) over an earlier block, the conflict is silently treated as dead and the signer proceeds to sign a new, conflicting block in a different tenure at the same or higher height — without ever asking the node whether the earlier block is actually gone. This mirrors the MuteBond front-running pattern: a time-decaying value (`epochStart` there, `last_endorsed`/timeout here) that a single actor can simply wait out to get the state machine to behave more favorably than the safety design intends, bypassing what should be a canonical-chain check with a mere clock check.

### Finding Description
In `stacks-signer/src/v0/signer.rs::handle_block_pre_commit`, once the pre-commit threshold is reached for a candidate block, the code queries prior conflicting signed blocks at the same/higher chain length: [1](#0-0) 

The only thing that keeps an older, possibly-still-live conflicting signature blocking a new signature is `conflict.last_endorsed > freshness_cutoff`, a comparison against `get_epoch_time_secs()` and the fixed `tenure_last_block_proposal_timeout` config value. This exactly mirrors `SortitionData::get_tenure_last_block_info`, which discards ("times out") the last signed block in a tenure purely based on elapsed wall-clock time since `signed_self`/`signed_group`: [2](#0-1) 

Once a conflict is stale by this clock check, the code never calls `conflict_still_blocks` (the node-derived "is it actually dead" check) for it — that call only happens for conflicts that are still fresh. For a stale conflict in a *different* tenure than the new proposal, the second gate (same-tenure tip check) also does not apply, so the signer proceeds straight to `mark_locally_accepted` and signs the new block: [3](#0-2) 

The documentation itself acknowledges the intent ("a dead signature must not stall the chain... until it goes stale") but the assumption that "timed out ⇒ genuinely dead" is not proven against the node for the stale case — it is asserted purely by the passage of `tenure_last_block_proposal_timeout` seconds, a value known in advance from config (and disclosed to peers). A miner (with no majority of signers, and without needing another signer's key) can exploit this:

1. Miner builds tenure T1, proposes block A at height H. Enough signers reach pre-commit threshold and sign A (`signed_self` recorded), but the miner deliberately withholds actually pushing/broadcasting the aggregated signature so the block never becomes globally accepted and stays alive as a candidate the network could still adopt.
2. Miner waits out `tenure_last_block_proposal_timeout` (a fixed, attacker-known duration) doing nothing else — exactly analogous to letting the bond's `epochStart`-based price "decay."
3. Miner starts tenure T2, forking off the same parent, and proposes block B at height H (or higher) that conflicts with A. Because the wall clock has advanced past `tenure_last_block_proposal_timeout` since the signers' `signed_self` timestamp on A, `last_endorsed > freshness_cutoff` is now false: A no longer blocks. The signer never asks the node whether A is actually dead (that node round-trip is skipped entirely for stale conflicts), and signs B.
4. If the miner (or a partition of signers who were slower to converge, or a burnchain race) still manages to later assemble and push A, the network now has two conflicting, signer-endorsed blocks at height H — an equivocation.

### Impact Explanation
This breaks the "signing a conflicting block" invariant the pre-commit conflict check exists to enforce: a signer produces a group-countable signature over a block that conflicts with another block it already signed, at the same height, without the safety-critical node-verified proof of death (`conflict_still_blocks`) ever being consulted. Per the stated impact classes this is Critical: a signer signing a conflicting block, achievable by a single miner (plus ordinary gossip) with no majority-of-signers or key compromise required — only patience to wait past a publicly known, fixed timeout.

### Likelihood Explanation
The miner does not need to compromise any signer, does not need a majority, and does not need any unusual network conditions — only to withhold a signed block from immediate global acceptance and wait a known duration (`tenure_last_block_proposal_timeout`, a signer-side config value with a fixed default that is also referenced/negotiated in miner-facing rejection timing). This is comparable in effort to the original bond front-run (which required no privileged access, just ordinary transaction submission timed against public, known decay behavior).

### Recommendation
Do not let a stale conflict be dismissed purely by wall-clock timeout. Before allowing a new signature to be produced over a conflicting block, always consult `conflict_still_blocks` (or equivalent node-derived canonical-state proof) regardless of freshness, or at minimum require a node-confirmed statement that the earlier tenure/sortition is orphaned or that the earlier block has been definitively pruned before dropping it as a veto. If the freshness shortcut is kept for latency reasons, it should require corroboration (e.g., burnchain tip having advanced past the earlier tenure's sortition) rather than local timestamp comparison alone.

### Proof of Concept
Conceptual PoC (network-level, not a code-level repro since it needs `stacks-node`/multi-signer integration harness):
1. Configure signers with a short `tenure_last_block_proposal_timeout` (as in existing tests, e.g. `stacks-node/src/tests/signer/v0/reorg.rs`).
2. Miner mines tenure T1, proposes block A at height H, waits for the pre-commit/signature threshold to be reached by signers (confirmed via `wait_for_block_pre_commits_from_signers`/signature observation), but never submits the aggregated block to the node (simulating withheld broadcast).
3. Sleep past `tenure_last_block_proposal_timeout`.
4. Miner forks to tenure T2 (new sortition) and proposes block B at height H conflicting with A.
5. Observe via signer logs/`get_signed_conflicts` that the conflict against A is no longer found (`last_endorsed <= freshness_cutoff`), and that `conflict_still_blocks`/node round-trip is never invoked for it; the signer proceeds to `mark_locally_accepted` and signs B.
6. Later push A (still valid, unexpired at chain level) to demonstrate two group-signable, conflicting blocks at height H. [4](#0-3) [5](#0-4)

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1383-1421)
```rust
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

**File:** stacks-signer/src/v0/signer.rs (L1423-1466)
```rust
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

**File:** stacks-signer/src/chainstate/mod.rs (L330-363)
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
```
