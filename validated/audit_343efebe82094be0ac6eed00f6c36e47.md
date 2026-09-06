### Title
Fixed `tenure_last_block_proposal_timeout` staleness gate lets a signer sign a live conflicting sibling block from a third tenure once it "goes stale" - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`handle_block_pre_commit` gates whether a previously-signed conflicting block still blocks a new signature purely on a single, fixed wall-clock timeout (`tenure_last_block_proposal_timeout`, default 30s). Once that window elapses, the signer stops asking the node whether the earlier conflicting block could still complete/reach the chain (for conflicts outside the block's own/parent tenure), and signs the new, conflicting block anyway. This mirrors the Chainlink finding: a single fixed staleness threshold is used to decide "is this prior commitment dead?" without verifying it is actually dead, so a signature can be produced over a second, conflicting block while the first is still capable of being finalized.

### Finding Description
In `stacks-signer/src/v0/signer.rs::handle_block_pre_commit`, once a block crosses the pre-commit weight threshold, conflicts at the same height (any tenure) are fetched via `SignerDb::get_signed_conflicts` [1](#0-0) , and freshness is computed as a single local cutoff:

```
let freshness_cutoff = get_epoch_time_secs().saturating_sub(
    self.proposal_config.tenure_last_block_proposal_timeout.as_secs(),
);
``` [2](#0-1) 

A conflict only blocks signing if it is *fresh* (`last_endorsed > freshness_cutoff`) **and** the reorg permit doesn't stand **and** `conflict_still_blocks` (a node query) says it's still live: [3](#0-2) 

If the conflict is *not* fresh (elapsed > 30s by default) and it is not in the block's own tenure, no further live-check against the node is performed for it at all — the code explicitly documents "A stale conflict in another tenure in particular no longer speaks for us" and proceeds to sign: [4](#0-3) 

The only chainstate re-check earlier in the same function (`check_block_against_signer_db_state`) only ever inspects the new block's **own tenure** (or its **parent** tenure for tenure-change blocks) — never a third, unrelated sibling tenure — a gap explicitly acknowledged in the documentation: "the re-check only ever looks at _one_ tenure... so a signed sibling at the same height in a third tenure is invisible to it" [5](#0-4) .

The freshness timestamp (`last_endorsed` = `signed_self`/`signed_group`) records only *this signer's own* local signing time, not any guarantee about the group's aggregation/broadcast latency for that earlier conflicting block. `tenure_last_block_proposal_timeout` (default 30s, configurable, minimum enforced only by config parsing, not by any relation to actual network/broadcast latency) is used identically for both determining "is a fresh signed tip still authoritative" (`get_tenure_last_block_info` in `chainstate/mod.rs`) and for gating this equivocation-prevention check — exactly the single fixed-threshold pattern from the Chainlink report, applied here to decide whether a prior commitment can safely be considered dead.

### Impact Explanation
If a slower-than-expected signature-aggregation/broadcast round for block A (in tenure T1) takes longer than `tenure_last_block_proposal_timeout` (e.g. due to normal network delay, temporarily degraded connectivity of some signers, or a miner deliberately timing a competing proposal), a signer can end up signing block B (in tenure T2, a sibling at the same height, invisible to the own/parent-tenure recheck) even though block A is still capable of collecting its 70% threshold and being pushed to the node. Because the same gap is reachable independently by other signers observing similar timing, multiple signers could contribute signatures to *both* conflicting blocks at the same height, breaking the "one-per-height, no equivocation" invariant that `get_signed_conflicts`/pre-commit re-checks exist to enforce. This falls under the "signer signing a conflicting block" critical impact category, since it can enable two independently-signed blocks at the same Stacks height in different tenures.

### Likelihood Explanation
Reachable by a single miner/proposer (plus ordinary network gossip) crafting two conflicting proposals — one in an earlier tenure and one in a later, unrelated (non-parent) tenure at the same height — timed so that the earlier one's local signature timestamp on a given signer exceeds the fixed 30s default window before the later one reaches its pre-commit threshold. No signer majority or private key compromise is required; only ordinary timing/latency variance (which the design's own comments acknowledge as expected, since it explicitly built the mechanism to tolerate a "dead signature" case) needs to occur while the earlier commitment is still alive rather than dead. This is more of a timing/latency-dependent likelihood than a trivially-triggerable one, but it does not require the "majority of signers" restriction the rules exclude — it requires only ordinary asynchronous delay, which the code's own risk analysis (in `docs/signer-flows.md`) already flags as a known blind spot it accepts for liveness.

### Recommendation
Do not use a single fixed local timestamp comparison as the sole substitute for a liveness check on conflicts outside the own/parent tenure. Either (a) always run `conflict_still_blocks` (the node-derived liveness check) regardless of the conflict's local freshness, only using freshness as an additional/optional signal, or (b) extend `check_block_against_signer_db_state`/`check_latest_block_in_tenure` to also examine sibling tenures that are conflict-bearing (as reported by `get_signed_conflicts`) instead of only own/parent, so a genuinely-still-live block in a third tenure cannot be silently superseded purely because a fixed wall-clock window elapsed.

### Proof of Concept
Not independently executable from static code review alone (timing-dependent, multi-node race). Conceptual reproduction path, grounded in the code read:
1. Signer S validates and signs block A (tenure T1, height H) at time t0; `signed_self` is stamped.
2. Due to normal broadcast delay, block A's 70% signature threshold is not yet reached, so it is not yet observed as globally accepted by the node.
3. At time t0 + `tenure_last_block_proposal_timeout` + ε (default > 30s), the same miner (or an attacker) gets block B accepted for signing (tenure T2, height H, not T1's parent) and it crosses the pre-commit threshold on signer S.
4. `handle_block_pre_commit` computes `freshness_cutoff = now - timeout`; A's `last_endorsed` (t0) is now ≤ `freshness_cutoff`, so it is classified stale [6](#0-5) .
5. Because T1 ≠ B's own or parent tenure, no `conflict_still_blocks` node query is performed to confirm A is actually dead; signer S proceeds to `mark_locally_accepted` and sign block B [7](#0-6) .
6. If A's signature aggregation subsequently completes (still possible since A was never proven dead), both A and B now carry valid threshold-crossing signer signatures at the same height in different tenures.

Given the time constraints of this review, I was unable to trace `conflict_still_blocks`'s and `reorg_permit_stands`'s full bodies (only their doc-referenced behavior and one prior call site were retrievable in this pass), so the precise conditions under which `conflict_still_blocks` would otherwise catch this case (absent staleness) could not be fully re-verified against source in this session — the analysis rests on the documented behavior in `docs/signer-flows.md` and the source excerpt of `handle_block_pre_commit` shown above.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1383-1392)
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
```

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

**File:** stacks-signer/src/v0/signer.rs (L1423-1478)
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

**File:** docs/signer-flows.md (L274-286)
```markdown
Order matters here: the chainstate re-check runs first and produces an explicit
(sticky) rejection when the block now conflicts with a signed one. The conflict
guard behind it is the silent backstop for what that re-check cannot see, and
silence keeps the door open to sign later once the conflict goes stale. Two
blind spots make the guard necessary:

- the re-check only ever looks at _one_ tenure (a tenure-change block's parent,
  or any other block's own), so a signed sibling at the same height in a third
  tenure is invisible to it;
- the `DuplicateBlockFound` check that would catch a second block in the same
  tenure lives in `check_proposal` and runs only at proposal arrival, never
  again. A block that crosses the pre-commit threshold minutes later has no
  other guard, which is what the own-tenure branch above covers.
```
