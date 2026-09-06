### Title
Stale cross-tenure conflicts bypass the equivocation guard, letting a signer sign two live blocks at the same height - (File: stacks-signer/src/v0/signer.rs)

### Summary
In `Signer::handle_block_pre_commit`, once a previously-signed conflicting block's `last_endorsed` timestamp ages past `tenure_last_block_proposal_timeout`, the conflict is dropped from consideration entirely for cross-tenure siblings, with no re-verification against the node. This lets a signer sign a second, conflicting block B at the same height as a still-live block A, as long as A was never pushed to (or accepted by) the node before it went stale.

### Finding Description
`handle_block_pre_commit` runs two conflict checks before signing:

1. **Fresh-conflict check** (only blocks while `conflict.last_endorsed > freshness_cutoff`), which additionally asks the node via `conflict_still_blocks` whether the conflicting block A could still be canonical (including the "only locally accepted, sibling at this height" case, which is treated as still blocking): [1](#0-0) 

2. **Own-tenure check**, which only fires when `conflict.consensus_hash == block_info.block.header.consensus_hash` — i.e., it is blind to conflicts in a *different* tenure: [2](#0-1) 

If A's tenure differs from B's tenure (a genuine cross-tenure sibling) and A's `last_endorsed` has aged past `freshness_cutoff`, **neither check applies to A**: check (1) skips it because it's stale, check (2) skips it because the consensus hashes differ. The code falls straight through to "Signing the replacement": [3](#0-2) 

The code comments explicitly acknowledge this is deliberate, on the assumption that "the chainstate checks above" (`check_block_against_signer_db_state`, run earlier in the same function) will catch a genuinely-live conflicting block. But the same documentation explicitly states that check only inspects *one* tenure (a tenure-change block's parent, or the block's own tenure) and is blind to a signed sibling in a third tenure: [4](#0-3) 

The `get_signed_conflicts`/freshness/`conflict_still_blocks` machinery exists specifically as the "silent backstop" for that blind spot, but it silently disables itself for cross-tenure conflicts the moment they go stale — without ever asking the node (via `conflict_still_blocks`) whether A is actually dead. Compare this to the FRESH branch, which explicitly keeps blocking a "locally accepted, sibling at this height" conflict (`HOLD1` in the flowchart) precisely because a block that only reached local (signer-side) 70% pre-commit weight may never have been pushed to the node yet, so its absence from the node's chain proves nothing: [5](#0-4) 

Once that same conflict simply ages past the timeout, that reasoning is dropped and the "no longer speaks for us" rule takes over unconditionally for cross-tenure conflicts, regardless of whether A was ever confirmed dead by the node: [6](#0-5) 

**Exploit flow**: A legitimate majority of signers validates and locally-accepts block A in tenure T1 at height H (`signed_self`/`signed_group` set, satisfying `get_signed_conflicts`'s "signed" predicate). Before A's aggregate signature is broadcast/adopted by the node (so `globally_accepted = false`, and the node's canonical chain has not yet advanced to H in T1), the single-slot attacker miner lets the timeout elapse (no more proposals, causing `last_endorsed` to fall behind `freshness_cutoff`), then proposes a competing tenure T2 block B at height H. If the honest signer set (not majority-controlled by the attacker) also independently validates and pre-commits B — a legitimate outcome if the node still has no visibility into A — the code path above lets each individual signer sign B too, because A is stale and cross-tenure. The result: two majority-endorsed, "signed" blocks at height H in different tenures, i.e., a real equivocation, without the attacker ever controlling a majority of signer weight, the node, or the auth token.

### Impact Explanation
This breaks the block-uniqueness/canonicity safety property the pre-commit conflict guard exists to enforce: a signer set can produce two conflicting, aggregately-signed blocks at the same Stacks height in different tenures. Whichever reaches the node first becomes canonical, but the other remains a valid, publishable signature set — a chain-safety violation (Critical: "a signature valid across chain/cycle/tenure boundaries" / "a signer signing an invalid, non-canonical, or conflicting block"). The scenario is repeatable any time a legitimately-signed block is delayed in reaching the node for longer than `tenure_last_block_proposal_timeout` while a competing tenure's block is proposed.

### Likelihood Explanation
Requires: (1) block A to genuinely reach majority signer endorsement in tenure T1 without being pushed/adopted by the node before going stale — plausible via network delay, a stalled miner relay, or an attacker-controlled miner simply not submitting the aggregate signature; (2) the timeout window (`tenure_last_block_proposal_timeout`) to be shorter than the delay, which is attacker-controllable by simply waiting; (3) a competing tenure block B to independently reach majority pre-commit — this needs honest signers to accept B as valid, which is realistic once they believe A/T1 is abandoned. The attacker needs only their own single miner slot plus normal gossip of `BlockProposal`/pre-commit messages — no majority signer weight, no node compromise, and no auth token.

### Recommendation
For cross-tenure conflicts that have gone stale, do not unconditionally drop them; instead, before treating a stale conflict as dead, run `conflict_still_blocks` (or an equivalent node round-trip) at least once to confirm the node's chain does not (and, if only locally accepted, could not soon) reach A at or above height H — mirroring the "only locally accepted and a sibling at this height" rule already applied in the fresh branch. Alternatively, do not permit a stale, only-locally-accepted, non-globally-accepted conflict to be cleared purely by elapsed time; require it to also be judged dead by `conflict_still_blocks` regardless of freshness.

### Proof of Concept
Rust test plan (in `stacks-signer/src/v0/tests.rs`, alongside `run_cross_tenure_scenario`):
1. Construct block A in tenure T1 at height H; drive the signer state machine so that `handle_block_pre_commit` marks it `LocallyAccepted` (`signed_self` set, `state != GloballyAccepted`).
2. Advance the mocked clock (or directly set `signed_self`/`signed_group` timestamps) so that `get_epoch_time_secs() - last_endorsed > tenure_last_block_proposal_timeout`.
3. Mock `stacks_client.get_tenure_tip(T1)` to return a tip still below/absent A's height (simulating "not yet pushed to node", i.e., A is NOT dead) and mock `get_sortition_by_burn_hash` to show T1's sortition still canonical (A's tenure not orphaned).
4. Construct conflicting block B in tenure T2 at height H, drive it through `handle_block_validate_ok` → `handle_block_pre_commit` to reach the pre-commit threshold.
5. Assert that `handle_block_pre_commit` for B results in `HOLD` (block_info.state remains `LocallyAccepted`/not signed, `signed_self` stays `None`), matching the security equality; if instead the test observes `info_b.state == BlockState::LocallyAccepted` with `signed_self.is_some()` (i.e., `SIGN`), this demonstrates the vulnerability — two live signed blocks at height H across tenures T1 and T2.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1403-1421)
```rust
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

**File:** stacks-signer/src/v0/signer.rs (L1423-1465)
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

**File:** docs/signer-flows.md (L310-320)
```markdown
2. **Does the node's canonical Stacks chain still reach the block itself?**
   - **it does** — real chain state; keep blocking;
   - **it does not, and the block was globally accepted** — the node once _did_
     have it, so a reorg moved past it. That is proof it is dead;
   - **it does not, and the block was never globally accepted** — a block is
     not handed to the node until the whole signer set has signed it, so this
     may mean "not yet seen" rather than "dead". A sibling at the same height
     therefore keeps blocking, since signing both would be the double-sign this
     guard exists for; a block _above_ the proposal does not, because it is no
     sibling and abandoning an unconfirmed block to restart beneath it is a
     reorg, not an equivocation.
```
