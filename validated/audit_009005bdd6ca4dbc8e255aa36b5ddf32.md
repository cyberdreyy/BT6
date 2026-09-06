### Title
Cross-tenure conflict guard uses a single hardcoded staleness window that lets a signer sign two conflicting blocks - (File: `stacks-signer/src/v0/signer.rs`)

### Summary
`Signer::handle_block_pre_commit` decides whether a previously signed/accepted block still blocks a new, conflicting pre-commit-threshold block using a single fixed "freshness" window (`tenure_last_block_proposal_timeout`, default 30s). Once a prior conflicting signature falls outside this window it is treated as *stale* and, for cross-tenure conflicts, is dropped from consideration entirely — the node is never asked whether that older block could still become canonical. Because the timeout is a hardcoded/static value that does not adapt to real burnchain/networking conditions (slow Bitcoin confirmation, node catch-up, propagation delay), a normal miner tenure-change sequence combined with ordinary delay can cause the signer to sign a second, conflicting block at the same height while its first signature is still potentially live on-chain.

### Finding Description
In `handle_block_pre_commit`, once the pre-commit threshold is reached and chainstate re-checks pass, the signer looks for signed conflicts at the same or higher height: [1](#0-0) 

`freshness_cutoff` is derived purely from wall-clock time minus the fixed config value `tenure_last_block_proposal_timeout` (default 30s, `DEFAULT_TENURE_LAST_BLOCK_PROPOSAL_TIMEOUT_SECS`): [2](#0-1) 

A conflict only blocks signing if it is "fresh" (`last_endorsed > freshness_cutoff`) **and** the node confirms via `conflict_still_blocks` that it could still be canonical. If the conflict is stale, cross-tenure conflicts are dropped unconditionally — the code explicitly documents that "a stale conflict in another tenure ... no longer speaks for us": [3](#0-2) 

This mirrors the reported bug class exactly: a single hardcoded tolerance/threshold value is applied uniformly regardless of the actual "market conditions" (here: burnchain confirmation latency, node catch-up time, or network propagation delay). If real-world delay before a fork is resolved exceeds the fixed 30-second window — plausible during Bitcoin confirmation variance, signer/node restarts, or a slow orphaning event — the guard silently stops asking the node whether the earlier signed block is still alive, and the signer proceeds to sign a second, conflicting block at the same height in a different tenure. Both blocks can then carry this signer's signature, and if the first block is still (or becomes) part of the canonical chain via honest majority participation from other signers who saw it earlier, the signer has signed two conflicting blocks — an equivocation that the guard exists specifically to prevent, per the design in `docs/signer-flows.md`: [4](#0-3) 

The hardcoded window is a config default, not something the signer adapts based on observed chain conditions per evaluation, so it can systematically be too short for the network state that actually exists at reorg time.

### Impact Explanation
This falls under the Critical impact category: a signer signing a conflicting block. Once the freshness window has elapsed, the signer no longer asks the node whether the older signed block is dead; it just signs the new one, breaking the intended single-vote-per-height invariant that `get_signed_conflicts` / `conflict_still_blocks` is designed to enforce.

### Likelihood Explanation
Likelihood is Medium: the trigger only requires ordinary chain conditions (Bitcoin confirmation delay, node catch-up, or a slow-to-settle fork) to exceed the fixed 30-second `tenure_last_block_proposal_timeout`, plus a miner naturally proposing a second, conflicting tenure block after that window — no majority collusion or key compromise is needed, only the normal flow of tenure changes and gossip.

### Recommendation
Do not let cross-tenure conflicts age out purely on a fixed wall-clock timer. Either always re-derive liveness from the node via `conflict_still_blocks` regardless of `last_endorsed` staleness (paying the round-trip cost), or make the staleness window adapt to observed burnchain conditions (e.g., scale with recent block-time variance / confirmation depth) rather than using a single hardcoded default that is blind to current network conditions.

### Proof of Concept
1. Signer signs block A in tenure 1 at height H (`signed_self` set, `last_endorsed = t0`).
2. Due to slower-than-usual Bitcoin confirmation / node catch-up, resolution of whether A is still canonical takes longer than `tenure_last_block_proposal_timeout` (30s default).
3. A miner proposes a competing tenure-2 block B at height H; pre-commits accumulate to threshold.
4. `handle_block_pre_commit` computes `freshness_cutoff = now - 30s`; since `t0 <= freshness_cutoff`, the conflict from A is treated as stale.
5. Per [3](#0-2) , since A's tenure differs from B's `consensus_hash`, the stale cross-tenure conflict is dropped without any node query.
6. Signer signs B, producing signatures over both A and B at the same height H — an equivocation, even though A may still be part of, or later rejoin, the canonical chain.

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

**File:** stacks-signer/src/v0/signer.rs (L1423-1435)
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
```

**File:** stacks-signer/src/config.rs (L42-43)
```rust
const DEFAULT_FIRST_PROPOSAL_BURN_BLOCK_TIMING_SECS: u64 = 60;
const DEFAULT_TENURE_LAST_BLOCK_PROPOSAL_TIMEOUT_SECS: u64 = 30;
```

**File:** docs/signer-flows.md (L288-297)
```markdown
Freshness alone is not enough to hold a signature back, because a signature can
outlive the block it covers: a Bitcoin reorg can kill the block, and a dead
signature must not stall the chain restarting beneath it until it goes stale. So
`conflict_still_blocks` derives, per evaluation, whether the conflict could still
end up in the chain. Deriving this here — instead of recording it when a fork is
observed — is deliberate: the node's view mid-reorg is a moving target (burn
block events fire before the sortition transaction commits, and a node error can
wipe the local state machine), so a fact recorded once at observation time can be
silently wrong, while a question asked per evaluation self-corrects on the next
pre-commit or re-proposal. Two questions, in order:
```
