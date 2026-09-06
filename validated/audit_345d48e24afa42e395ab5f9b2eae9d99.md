### Title
Stale cross-tenure conflict is cleared without any liveness check, allowing a signer to double-sign a sibling block at the same height - (File: `stacks-signer/src/v0/signer.rs`)

### Summary
`handle_block_pre_commit`'s conflict guard is supposed to stop a signer from putting a signature on two blocks that could both end up in the chain. It only re-verifies a conflict against the node (`conflict_still_blocks`) while that conflict is still "fresh" (`last_endorsed > freshness_cutoff`). Once a conflict in a **different tenure** goes stale (elapsed time since `last_endorsed` exceeds `tenure_last_block_proposal_timeout`), it is dropped from consideration entirely with no query to the node about whether the original block is still canonical/live. The narrower same-tenure re-check that follows only looks at conflicts sharing the *same* `consensus_hash` as the newly-evaluated block, so it cannot substitute for the dropped cross-tenure check.

### Finding Description
In `handle_block_pre_commit`, once pre-commit weight crosses threshold for a candidate block, the signer computes all conflicting signed blocks at the same or higher height in *any* tenure: [1](#0-0) 

It then only refuses to sign if a conflict is simultaneously fresh, not covered by a reorg permit, and confirmed still-live by the node: [2](#0-1) 

The comment right after explicitly states the design rationale — that a *stale* conflict in another tenure is assumed to no longer speak for the signer, and reliance is placed on the chainstate re-check instead: [3](#0-2) 

But the chainstate re-check (`check_block_against_signer_db_state`, documented in `docs/signer-flows.md`) only inspects *one* tenure: a tenure-change block's parent tenure, or a block's own tenure — never a third, unrelated tenure holding a sibling at the same height: [4](#0-3) 

The subsequent same-tenure guard that runs after the fresh-conflict check only matches conflicts whose `consensus_hash` equals the *new* block's own tenure: [5](#0-4) 

So a conflict recorded against a *third* tenure that has gone stale is filtered out of the fresh-conflict `find()` and is never checked by the same-tenure block either — it is silently treated as cleared, and the signer proceeds to sign: [6](#0-5) 

Critically, `get_signed_conflicts` explicitly documents that these conflicts span *any* tenure — same-height siblings from a different tenure are exactly what this whole guard exists to prevent, per its own doc comment: [7](#0-6) 

The existing test suite only exercises this cross-tenure path while the freshness window is held wide open (`Duration::from_secs(100_000)`), i.e. always on the "fresh, ask the node" branch: [8](#0-7) 

There is no test that drives a cross-tenure conflict into staleness (`last_endorsed <= freshness_cutoff`) while the conflicting tenure/block is still genuinely canonical, confirming this is an unverified/untested code path rather than a deliberately proven-safe one.

### Impact Explanation
This breaks the "one signature per height" invariant the guard is explicitly built to protect: "The guard exists to stop us endorsing two blocks that could both end up in the chain." A single miner (with ordinary gossip/timing control, no majority of signers, no key compromise) can engineer the timing of a two-tenure fork so that the sibling proposal in the second tenure arrives after `tenure_last_block_proposal_timeout` has elapsed since the first block's `last_endorsed` timestamp, while the first tenure/block is still fully live and canonical (never orphaned, never reorged past). The signer will then sign the second, conflicting block without any node-side liveness check for that stale cross-tenure record, producing a second signer certificate at the same height — the exact equivocation ("a signer signing a conflicting block") that the report's "grace-period-less pause bypass" bug class describes: a state written off purely by elapsed time, later exploited once the write-off silently occurs, with no re-verification against ground truth.

### Likelihood Explanation
This requires only a single miner controlling proposal timing across two tenures (e.g. around a natural or induced Bitcoin fork/tenure change) and ordinary network delay/gossip — no signer majority, no compromised keys, and no StackerDB-transport-only trickery. The relevant timeout (`tenure_last_block_proposal_timeout`) is a signer config value on the order of tens of seconds in tests, easily exceeded by naturally occurring propagation delay or deliberately timed re-proposals, both of which are already modeled elsewhere in this file's own test harness (`re_propose_b_after`).

### Recommendation
Remove the freshness gate from the node-liveness question for cross-tenure conflicts, or equivalently, make `conflict_still_blocks` (or an equivalent live check) run for *every* recorded conflict in `get_signed_conflicts`, not only fresh ones, before deciding to sign — mirroring the same-tenure path, which already asks the node (`get_tenure_tip`) regardless of freshness. At minimum, extend `check_block_against_signer_db_state` (or the pre-commit guard) to check third-party tenures holding a same-height sibling, so staleness alone can never substitute for a canonical-chain check.

### Proof of Concept
1. Miner proposes block A (tenure 1, height 10) off a shared parent; all signers validate and locally accept/sign A; A's `last_endorsed` is stamped at time T.
2. Miner lets tenure 1 continue as the live/canonical tenure (never orphaned, sortition remains canonical, A is never handed to the node yet because global acceptance requires the whole set — matching `TenureAFate::Live` in `run_cross_tenure_scenario`).
3. Miner delays until `T + tenure_last_block_proposal_timeout` has elapsed, then triggers/broadcasts a conflicting sibling proposal B (tenure 2, same height 10, same parent).
4. In `handle_block_pre_commit`, `get_signed_conflicts` returns A as a conflict; but `conflict.last_endorsed <= freshness_cutoff` now holds, so the `find()` in lines 1403-1411 skips it without calling `conflict_still_blocks`.
5. The same-tenure check in lines 1432-1457 does not match (A's `consensus_hash` is tenure 1, B's tenure is tenure 2), so it never queries the node about tenure 1's tip either.
6. The signer proceeds to `mark_locally_accepted` and signs B — producing a second signature at the same height while A's tenure is still fully live, unverified by any node query. This exact code path (minus the artificial timing) is what `run_cross_tenure_scenario`/`fresh_conflict_in_another_tenure_blocks_signing` exercises with `Duration::from_secs(100_000)`; the vulnerability is reproduced by shrinking that timeout to a realistic value and letting real time pass instead.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1110-1136)
```rust
    /// The guard exists to stop us endorsing two blocks that could both end up in the chain. It
    /// must not, however, outlive the block it protects: a Bitcoin reorg can kill a block we
    /// signed, and a dead signature must not stall the chain restarting beneath it.
    ///
    /// Two questions, each answerable by the node at any time:
    ///
    /// 1. Is the tenure's sortition still on the canonical burn chain? We saved the tenure's
    ///    burn block when it arrived, and `/v3/sortitions` resolves it against the node's
    ///    canonical fork. A 404 means a burnchain fork orphaned the tenure: everything it built
    ///    is void, so the conflict is dead no matter what state its block is in.
    ///
    /// 2. Does the node's canonical Stacks chain still reach the block?
    ///    * If it does, the block is real chain state, so it keeps blocking. (If the reorg-timing
    ///      rules sanctioned replacing it, the tenure is recorded as superseded and the conflict
    ///      never reaches this check at all.)
    ///    * If it does not, and the block was once globally accepted, the node had it and a
    ///      reorg moved past it. That is proof it is dead, so it stops blocking.
    ///    * If it does not, and the block was never globally accepted, the node may simply never
    ///      have been handed it, since that only happens once the whole signer set has signed. We
    ///      cannot tell "dead" from "not yet known", so a sibling at the same height keeps
    ///      blocking (signing both would be the double-sign this guard is for), while a block
    ///      above the proposal does not: it is no sibling, and abandoning an unconfirmed block to
    ///      restart beneath it is a reorg rather than an equivocation.
    ///
    /// If we have no saved burn block, or the node is unreachable, the conflict keeps blocking.
    /// That only delays the replacement until our signature goes stale, whereas wrongly signing
    /// cannot be taken back.
```

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

**File:** stacks-signer/src/v0/signer.rs (L1423-1431)
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
```

**File:** stacks-signer/src/v0/signer.rs (L1432-1457)
```rust
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
```

**File:** stacks-signer/src/v0/signer.rs (L1458-1466)
```rust
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

**File:** stacks-signer/src/v0/tests.rs (L957-960)
```rust
        // The freshness window is wide open: A's signature is fresh throughout, so only the
        // orphan record can decide whether it still blocks B.
        let mut node = MockNode::new(tips, Duration::from_secs(100_000));

```
