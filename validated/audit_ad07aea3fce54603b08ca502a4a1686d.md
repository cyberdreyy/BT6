### Title
Stale third-tenure conflicting signatures are neither freshness-checked nor liveness-checked before a second signature is placed at the same height - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`Signer::handle_block_pre_commit` re-validates a block against known signed conflicts before placing a signature on it once the pre-commit threshold is reached. The conflict resolution logic enumerates only two categories of "still blocking" conflicts — fresh conflicts (checked via `conflict_still_blocks`) and same-tenure stale conflicts (checked via a direct node tip query) — but omits a third, reachable case: a *stale* conflicting signed block in a *different, third* tenure. This mirrors the reported Gitea bug class: an allow/deny-list enumeration ("is this range private/blocked?") that omits ranges that matter, letting through what it was meant to stop.

### Finding Description
`get_signed_conflicts` deliberately returns every signed block (`signed_self` or `signed_group`) at or above the proposed height, **in any tenure**, because "two blocks at the same height are siblings no matter which tenure they belong to" [1](#0-0) .

The consumer of that list, in `handle_block_pre_commit`, only guards two cases:

1. A conflict that is **fresh** (`last_endorsed > freshness_cutoff`) *and* not covered by a reorg permit *and* `conflict_still_blocks` says it's still live — this is checked regardless of tenure.
2. A conflict whose `consensus_hash` equals the **proposed block's own tenure** and is not permitted — this is re-verified against the node's live tenure tip. [2](#0-1) 

Any conflict that is **stale** (`last_endorsed <= freshness_cutoff`) *and* belongs to a **third tenure** (neither the proposed block's own tenure nor its parent tenure, which the earlier `check_block_against_signer_db_state` re-check already examined) is checked by **neither** branch. It falls straight through to the `!conflicts.is_empty()` logging branch and the block gets signed anyway [3](#0-2) .

The project's own documentation confirms this is a real, acknowledged blind spot rather than a hypothetical: the chainstate re-check "only ever looks at _one_ tenure ... so a signed sibling at the same height in a third tenure is invisible to it," and states the own-tenure conflict guard is what covers "the same ground" for a block's *own* tenure only — it does not claim third-tenure coverage [4](#0-3) .

The staleness assumption ("a dead signature must not stall the chain") is valid only if going stale reliably means the underlying block died. That is not guaranteed: `freshness_cutoff` is a fixed local timer (`tenure_last_block_proposal_timeout`) independent of whether the node's canonical chain still serves that older tenure's block as live. A block signed in tenure A can remain globally accepted and canonical on the node long after the local staleness timer expires, while this signer is asked to sign a same-height sibling proposed from an unrelated tenure C.

### Impact Explanation
If this path is taken, the signer places its signature (`mark_locally_accepted`, `handle_block_signature`, broadcast acceptance [5](#0-4) ) on a block that conflicts with — and does not supersede — an already globally-accepted, still-canonical block at the same height in a different tenure. That is exactly the "signer signing a conflicting/non-canonical block" class called out as Critical: it breaks the one-globally-accepted-block-per-height invariant that the rest of the state machine (`get_signed_conflicts`, `mark_tenure_superseded`, `reorg_permit_stands`) is built to preserve, and does so via *this signer's own vote* toward a competing chain, not merely a node-side rejection.

### Likelihood Explanation
Reaching this requires: (1) a block in tenure A reaches global signature/acceptance at height h; (2) enough time passes for `tenure_last_block_proposal_timeout` to elapse without a fresh re-endorsement — the docs explicitly design for this ("a signature can outlive the block it covers"); (3) a later, unrelated tenure C (a normal, single sortition-winning miner) proposes a conflicting sibling at height h and gathers the ordinary ≥70% pre-commit weight via gossip. None of these steps require colluding signers, another signer's key, or majority control — only ordinary chain activity plus the passage of time, which a single miner can help engineer by delaying its own tenure or exploiting a Bitcoin reorg window.

### Recommendation
Extend the stale-conflict fallback so it is not scoped to `conflict.consensus_hash == block_info.block.header.consensus_hash` only. For every conflict not covered by the fresh/`conflict_still_blocks` branch, query the node's tenure tip for **that conflict's own tenure** (not just the tenure of the block being signed) and refuse to sign if the node still reports that tenure at or above the conflicting height — mirroring the same-tenure fallback already implemented, just generalized to all conflicting tenures returned by `get_signed_conflicts`.

### Proof of Concept
Conceptual reproduction (matches the existing test harness pattern in `stacks-signer/src/chainstate/tests/v2.rs` and `stacks-signer/src/v0/tests.rs`):
1. Tenure A produces block X at height h; the signer records it as `GloballyAccepted`/`signed_group` (`get_signed_conflicts` will later return it).
2. Advance local time past `tenure_last_block_proposal_timeout` so `last_endorsed <= freshness_cutoff` for X — it is now "stale."
3. A new, unrelated tenure C proposes block Z at height h (a sibling of X, not X's own or parent tenure) and gathers ≥70% pre-commit weight via `handle_block_pre_commit`.
4. In the conflict-resolution code path, X fails the freshness test (branch 1 skipped) and fails the tenure-equality test (`X.consensus_hash != Z.consensus_hash`, branch 2 skipped) at [6](#0-5) .
5. The signer proceeds to `mark_locally_accepted` and broadcasts acceptance for Z, producing a second signed conflicting block at height h even though X may still be the node's canonical tip in tenure A.

### Citations

**File:** stacks-signer/src/signerdb.rs (L1587-1605)
```rust
    /// Return every signed block at or above the given Stacks height, in ANY tenure, excluding
    /// the block with the given signer signature hash, ordered by height (highest first). A
    /// block is considered signed if a signature was ever put over it, ours (`signed_self`)
    /// or the observed group's (`signed_group`). Blocks that were only pre-committed carry no
    /// signature and are never returned. Each row carries the most recent endorsement time
    /// (`signed_self`/`signed_group`, whichever is later) so the caller can judge freshness per
    /// conflict.
    ///
    /// The search deliberately spans all tenures: two blocks at the same height are siblings
    /// no matter which tenure they belong to (e.g. a tenure-start block conflicts with the
    /// previous tenure's block at the same height), so a signature over either may conflict
    /// with a fresh signature over the other.
    ///
    /// Blocks in tenures whose reorg we sanctioned under the reorg-timing rules (see
    /// [`SignerDb::mark_tenure_superseded`]) are still returned, but annotated with the
    /// permitting tenure's sortition (`superseded_by_*`): the permit only holds while that
    /// sortition is canonical, which the caller derives from the node per evaluation (see
    /// `Signer::reorg_permit_stands`) -- like every other question about whether a conflict is
    /// still *live* (`Signer::conflict_still_blocks`), it is not recorded.
```

**File:** stacks-signer/src/v0/signer.rs (L1383-1457)
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
```

**File:** stacks-signer/src/v0/signer.rs (L1458-1478)
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

**File:** docs/signer-flows.md (L274-287)
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
