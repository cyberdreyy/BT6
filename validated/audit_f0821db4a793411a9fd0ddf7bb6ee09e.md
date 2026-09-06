### Title
Local, time-only "freshness" cutoff (`tenure_last_block_proposal_timeout`) lets a signer sign a conflicting sibling block without ever asking the node whether the earlier signed block is actually dead — ([File: stacks-signer/src/v0/signer.rs])

### Summary
The double-sign guard in `handle_block_pre_commit` decides whether a previously-signed conflicting block still "blocks" a new one using a **local wall-clock cutoff** (`tenure_last_block_proposal_timeout`) *before* it ever queries the node. If a conflict's `last_endorsed` timestamp is older than this cutoff, it is treated as stale and — for conflicts outside the proposed block's own tenure — the node is never consulted at all before the signer signs the new, conflicting block. Exactly like the Lido report where a fixed timelock (3 days) may be shorter than the real-world event it's meant to protect against (1–5 day withdrawal delay), this fixed local timeout may be shorter than the real time it takes for the node/network to actually confirm that the earlier signed sibling is dead (e.g., a slow/partitioned node, a stalled sortition confirmation, or simple network delay in `get_tenure_tip`/`get_sortition_by_burn_hash` calls prior to the cutoff). This can cause the signer to place a second signature on a conflicting block at the same height while the first one is still live, breaking the "no double sign" safety invariant.

### Finding Description
In `stacks-signer/src/v0/signer.rs`, `handle_block_pre_commit` fetches all signed conflicts at or above the proposed block's height via `get_signed_conflicts`, then computes:

```
let freshness_cutoff = get_epoch_time_secs().saturating_sub(
    self.proposal_config.tenure_last_block_proposal_timeout.as_secs(),
);
``` [1](#0-0) 

It then only calls the node-backed liveness check `conflict_still_blocks` (which asks the node two questions: is the conflicting tenure's sortition still canonical, and does the canonical chain still reach the conflicting block) for conflicts whose `last_endorsed > freshness_cutoff`:

```
if let Some(conflict) = conflicts.iter().find(|conflict| {
    conflict.last_endorsed > freshness_cutoff
        && !self.reorg_permit_stands(stacks_client, conflict)
        && self.conflict_still_blocks(stacks_client, conflict, ...)
}) { ... refuse to sign ... }
``` [2](#0-1) 

For any conflict that is *stale* by this local time comparison (`last_endorsed <= freshness_cutoff`) **and is in a different tenure** than the proposed block, the code falls straight through to signing — the only remaining node check (`get_tenure_tip`) is scoped to conflicts sharing the *same* consensus hash as the proposed block:

```
if conflicts.iter().any(|conflict| {
    conflict.consensus_hash == block_info.block.header.consensus_hash
        && !self.reorg_permit_stands(stacks_client, conflict)
}) {
    match stacks_client.get_tenure_tip(&block_info.block.header.consensus_hash) { ... }
}
...
// It is only considered globally accepted IFF ...
if let Err(e) = block_info.mark_locally_accepted(false) { ... }
``` [3](#0-2) 

This is exactly the documented design in the project's own flow docs: once a conflict is judged "stale" by the local timer, the "OWN tenure? no" branch goes straight to `SIGN` with no live node check at all: [4](#0-3) 

So the entire liveness verdict for cross-tenure conflicts hinges on a **single fixed local duration**, `tenure_last_block_proposal_timeout`, exactly analogous to the report's `adminSettleDebtLockPeriod`. The comment in the code itself acknowledges the guard's purpose: “the guard exists to stop us endorsing two blocks that could both end up in the chain… it must not, however, outlive the block it protects” — i.e., the authors reasoned about the timeout being *too long*, but not about it being *too short* relative to real network/node confirmation latency. [5](#0-4) 

If `tenure_last_block_proposal_timeout` is shorter than the real-world time needed for the signer to be able to prove a conflict dead via the node (e.g. node lag, degraded connectivity, delayed burnchain propagation, or simply a slow validation round trip for the *first* block that consumed most of the window before the signature was even placed), the conflict will already be judged "stale" the moment it's queried, and the cross-tenure liveness check is skipped entirely. A signer can then sign a second, conflicting sibling block at the same height in another tenure — a concrete equivocation/double-sign, breaking exactly the safety property `conflict_still_blocks` and the whole freshness+liveness apparatus were built to guarantee.

### Impact Explanation
This falls squarely under the "Critical" impact bucket: a signer signing a conflicting block at the same height (equivocation) in a different tenure. Two conflicting Nakamoto blocks with valid signer signatures could each accumulate weight toward the 70% threshold, risking a chain split or requiring intervention, and it directly undermines the "nobody double-signs" guarantee described in the project's own design docs.

### Likelihood Explanation
Likelihood is low-to-moderate, similar to the referenced report: it requires an adverse timing window — the local `tenure_last_block_proposal_timeout` window elapsing while the node hasn't yet been able to confirm (via `/v3/sortitions` or tenure tip) that the earlier-signed sibling block is actually dead, and a genuinely competing proposal arriving after that window. This does not require a majority of signers or key compromise; it only needs the config default/timing mismatch and delayed node RPCs (which the code paths themselves already anticipate as fallible/slow, given the extensive `ClientError` handling around `get_tenure_tip` and `get_sortition_by_burn_hash`).

### Recommendation
Do not let a purely local timestamp comparison (`freshness_cutoff`) fully gate whether the node-backed liveness check (`conflict_still_blocks`) runs for cross-tenure conflicts. Either:
- Always run `conflict_still_blocks` (or an equivalent conclusive node check) for every unresolved conflict regardless of staleness before allowing a sign, treating "stale" purely as a fallback when the node is unreachable, not as a shortcut that skips the node entirely; or
- Increase/tie `tenure_last_block_proposal_timeout` conservatively to the worst-case round-trip/latency for the node to conclusively answer "is this sortition still canonical / does the chain still reach this block", with enough margin (analogous to the report's recommendation to widen `adminSettleDebtLockPeriod` beyond the observed 1–5 day range) rather than a static default.

### Proof of Concept
Conceptual sequence (this cannot be fully driven without live infra, but is directly derivable from the code paths cited):
1. Signer signs block A in tenure T1 at height H (`signed_self`/`signed_group` timestamp recorded).
2. A partition or node lag prevents the signer's stacks node from confirming/propagating tenure T1's sortition or tip promptly (`get_sortition_by_burn_hash` / `get_tenure_tip` calls in `conflict_still_blocks` would time out or be delayed if invoked).
3. Time passes until `now - last_endorsed(A) > tenure_last_block_proposal_timeout` — A is now "stale" per `handle_block_pre_commit`'s freshness_cutoff check at [6](#0-5) .
4. A competing block B, in a different tenure T2 but same height H, is proposed, pre-committed, and reaches the pre-commit threshold.
5. Because A's conflict entry is stale and `A.consensus_hash != B.consensus_hash`, the only remaining check (`get_tenure_tip(B's tenure)`) does not examine A at all — the cross-tenure liveness check for A is skipped entirely per [7](#0-6) .
6. The signer signs B via `mark_locally_accepted` even though A may still be perfectly live/canonical on-chain — the signer has now signed two conflicting blocks at height H.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1108-1136)
```rust
    /// Whether a block we signed still conflicts at `proposed_height`.
    ///
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

**File:** stacks-signer/src/v0/signer.rs (L1423-1470)
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
```

**File:** docs/signer-flows.md (L253-271)
```markdown
    PERM -- no --> FRESH{"any of them still fresh?<br/>last_endorsed > cutoff"}
    FRESH -- yes --> SORT{"conflict_still_blocks, question 1:<br/>is its tenure's sortition still on the<br/>canonical burn chain?<br/>get_sortition_by_burn_hash"}
    SORT -- "404, with the node's burnchain tip<br/>at or past the burn block — a fork<br/>orphaned the tenure" --> OWN
    SORT -- "canonical, or we never<br/>saved its burn block" --> LIVE{"question 2: does the node's chain<br/>still reach the block itself?<br/>get_tenure_tip(its tenure)"}
    SORT -- "could not ask, or 404 with the<br/>node's tip still below the burn block" --> HOLD1
    LIVE -- "yes — real chain state" --> HOLD1["refuse to sign for now<br/>(may sign once conflict is stale)"]:::hold
    LIVE -- "no, and it was<br/>globally accepted" --> OWN
    LIVE -- "no, only locally accepted<br/>— but above this height" --> OWN
    LIVE -- "no, only locally accepted<br/>and a sibling at this height" --> HOLD1
    LIVE -- "could not ask" --> HOLD1
    FRESH -- "no — all stale" --> OWN{"a conflict in this block's<br/>OWN tenure?"}
    OWN -- yes --> TIP{"own tenure confirmed<br/>at ≥ this height?<br/>get_tenure_tip(own tenure)"}
    TIP -- yes --> HOLD2["refuse to sign"]:::hold
    TIP -- "no — never confirmed" --> SIGN
    TIP -- "node unreachable" --> SIGN
    OWN -- no --> SIGN["SIGN: mark_locally_accepted,<br/>handle_block_signature,<br/>broadcast acceptance"]:::good
    classDef good fill:#17a45c22,stroke:#1d9d5f,stroke-width:1.5px;
    classDef bad fill:#d84a3f22,stroke:#c9473d,stroke-width:1.5px;
    classDef hold fill:#8a95a51f,stroke:#8a95a5,stroke-dasharray:4 3;
```
