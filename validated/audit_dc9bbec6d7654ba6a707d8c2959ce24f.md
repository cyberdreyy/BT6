### Title
Wall-clock "conflict freshness" check in pre-commit signing lets a signer double-sign conflicting blocks at the same height after any delay (crash/restart/backlog) exceeding `tenure_last_block_proposal_timeout` - ([File: stacks-signer/src/v0/signer.rs])

### Summary
The report describes Arcadia's liquidation-price curve continuing to decay purely on wall-clock time while the L2 sequencer is down, so that when the sequencer resumes, a stale time-based computation is used as if no downtime had occurred, producing an unfair/dangerous outcome. `stacks-signer`'s pre-commit signing path contains the same class of flaw: whether a previously-signed conflicting block at the same chain height still "blocks" a new signature is gated by a pure local wall-clock comparison (`freshness_cutoff`) against `conflict.last_endorsed`, independent of whether the signer process (or its view of the network) was actually live during that interval.

### Finding Description
In `handle_block_pre_commit` (name inferred from context; the relevant logic is in `stacks-signer/src/v0/signer.rs`), once a competing block proposal accumulates enough pre-commit weight, the signer looks up any previously signed/accepted conflicting blocks at the same or higher height via `self.signer_db.get_signed_conflicts(...)`, and computes: [1](#0-0) 

```
let freshness_cutoff = get_epoch_time_secs().saturating_sub(
    self.proposal_config.tenure_last_block_proposal_timeout.as_secs(),
);
if let Some(conflict) = conflicts.iter().find(|conflict| {
    conflict.last_endorsed > freshness_cutoff
        && !self.reorg_permit_stands(stacks_client, conflict)
        && self.conflict_still_blocks(stacks_client, conflict, block_info.block.header.chain_length)
}) { ... return; }
```

The predicate's *first* condition, `conflict.last_endorsed > freshness_cutoff`, is a pure comparison against the local wall clock (`get_epoch_time_secs()`), with no dependency on the node's chain state, on whether the conflicting block is still canonical, or on whether the signer process was actually running/observing events during the elapsed interval. This mirrors the audited bug: a decay curve is evaluated purely against elapsed wall-clock time, so it silently "moves forward" through any period the system was not actively processing (sequencer downtime there; signer downtime/backlog here), producing outcomes calibrated as if the interval had been observed normally.

Because `last_endorsed` is a persisted timestamp read from `SignerDB`, and `tenure_last_block_proposal_timeout` defaults to a short window (30s, per `sample/conf/signer/mainnet-signer-conf.toml`): [2](#0-1) 

if the signer process is restarted, wedged behind an event backlog, paused by the host (GC/OS scheduling), or partitioned from the network for longer than this timeout, the persisted conflict from before the pause will already read as "stale" (`last_endorsed <= freshness_cutoff`) the moment it resumes processing — even though the earlier signed block might still be canonical/live on the node. The `find` predicate short-circuits on `&&`, so once freshness fails, `conflict_still_blocks` (which actually queries the node for liveness) is never even consulted. The code comment at the call site explicitly acknowledges the freshness check is evaluated first specifically because it is a "local timestamp comparison" cheaper than querying the node — but that same design choice means the decision to no longer treat a conflict as blocking is made solely from elapsed wall-clock time, not from any signal that the signer was actually live/synced during that time. [3](#0-2) 

The result: the signer proceeds to `mark_locally_accepted` and produce a fresh signature over the new, competing block: [4](#0-3) 

If its earlier signature over the first conflicting block at the same height is still valid/collected elsewhere in the network (e.g. the block it signed earlier is still being pushed/gathered by a miner or another node), the signer now has two signatures over two different blocks at the same chain height — an equivocation, i.e. exactly the "signer signing a conflicting block" class this scan flags as Critical.

### Impact Explanation
This breaks the core equality that a signer must never produce signatures over two conflicting (same-height, different-tenure-or-content) blocks while either signature could still matter. Equivocating signatures from even one signer can be aggregated with signatures from a majority of other signers/across two different miner-produced blocks at the same height, contributing to a fork or double-signature scenario the freshness/conflict-tracking logic exists specifically to prevent. This matches the report's root cause: a purely time-decayed check silently treats a "still-live" state as "expired" once real time elapses past a fixed window, regardless of whether the elapsed time was spent in normal operation or in an outage/backlog of the component performing the check.

### Likelihood Explanation
This requires only a single signer to experience a pause (crash+restart, resource contention, GC pause, brief network partition preventing block/consensus RPCs, or event-queue backlog) exceeding `tenure_last_block_proposal_timeout` (default 30s) while a signed conflict is pending in `SignerDB`, and then observe a new competing pre-commit reach threshold immediately after resuming. No majority collusion, no other signer's key, and no auth-token access are needed — a lone miner (or gossip of a re-proposed sibling block) triggering the pre-commit path after the pause is sufficient. Given 30 seconds is a fairly tight bound compared to plausible node RPC stalls or process restarts in production, this is a realistically reachable window, not merely theoretical.

### Recommendation
The freshness determination for a previously-signed conflict should not rely solely on elapsed wall-clock time. Instead:
- Track whether the signer itself was continuously live/processing events since `last_endorsed`; if there was a gap (restart, long stall) longer than the timeout, treat the conflict as still-blocking until `conflict_still_blocks` (a node-derived, authoritative check) explicitly confirms it is dead, rather than skipping that authoritative check on the basis of local time alone.
- Alternatively, always call `conflict_still_blocks` regardless of freshness before allowing a new signature over a conflicting height, using the wall-clock freshness only as a performance short-circuit for the *reorg-permit* check, not as a sole gate on whether the node-side liveness check runs at all.

### Proof of Concept
1. Signer S signs/accepts block `A` at height `H` in tenure `T1`; `SignerDB` records the conflict-tracking entry with `last_endorsed = t0`.
2. Signer S process stalls or restarts for `> tenure_last_block_proposal_timeout` seconds (e.g. 31s with the default 30s config) — analogous to sequencer downtime in the source report.
3. During/after the stall, a competing block `B` at the same height `H` (e.g. sibling tenure-start block from a new miner) is proposed and reaches the pre-commit weight threshold.
4. On resuming, S evaluates `handle_block_pre_commit` for `B`: `conflict.last_endorsed (t0) > freshness_cutoff (now - 30s)` is now false, so the `find` predicate short-circuits to `None` without ever calling `conflict_still_blocks`.
5. S signs `B` via `mark_locally_accepted` / `create_block_acceptance`, even though block `A` (already signed by S) may still be canonical/live on the node — producing conflicting signatures from S over two different blocks at height `H`.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1393-1421)
```rust
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

**File:** stacks-signer/src/v0/signer.rs (L1466-1478)
```rust
        // It is only considered globally accepted IFF we receive a new block event confirming it OR see the chain tip of the node advance to it.
        if let Err(e) = block_info.mark_locally_accepted(false) {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally accepted: {e:?}",);
            }
        }
        self.signer_db
            .insert_block(&block_info)
            .unwrap_or_else(|e| self.handle_insert_block_error(e));
        let accepted = self.create_block_acceptance(&block_info.block);
        // have to save the signature _after_ the block info
        self.handle_block_signature(stacks_client, sortition_state, &accepted);
        self.send_block_response(&block_info.block, accepted.into());
```

**File:** sample/conf/signer/mainnet-signer-conf.toml (L137-142)
```text
# Time to wait for the last block of a tenure to be globally accepted
# or rejected before considering a new miner's block at the same height
# as potentially valid.
# Default: 30
# Units: seconds
# tenure_last_block_proposal_timeout_secs = 30
```
