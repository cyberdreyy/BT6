### Title
Signer defaults to signing a conflicting block when its own node's tenure-tip lookup fails — ([File: stacks-signer/src/v0/signer.rs])

### Summary
In `handle_block_pre_commit`, when a stale same-tenure conflict exists, the signer asks its own node whether that tenure's canonical tip already covers the proposed height before it is allowed to place a second signature. If that RPC call errors, the code treats the tenure as "unconfirmed" and falls through to sign anyway, instead of treating the unknown answer as still-blocking (the safe default used everywhere else in this same guard chain).

### Finding Description
`handle_block_pre_commit` re-checks whether a stale, same-tenure conflicting signature should still block a new signature before signing a block that reached the pre-commit threshold: [1](#0-0) 

The comment directly preceding this code states the invariant being protected: "A stale conflict in this block's own tenure still blocks if the node already has that tenure at or above the proposed height, since the proposal then duplicates state the node has already built on." [2](#0-1) 

The `get_tenure_tip` lookup is the *only* mechanism enforcing that invariant once the earlier signature has gone stale (`conflicts.iter().find(...)` freshness/`conflict_still_blocks` check above only vetoes on *fresh* conflicts). On success, the code correctly refuses to sign if the tip is already at or above the proposed height: [3](#0-2) 

But on `Err(e)` — any RPC failure to the signer's own node (timeout, transient network error, node restart, etc.), which requires no majority collusion, no other signer's key, and no auth token — the code merely logs a warning and falls through, continuing on to `mark_locally_accepted` and signing the block: [4](#0-3) [5](#0-4) 

This is the inverse of how the sibling function `conflict_still_blocks` (used for the fresh-conflict / cross-tenure case) treats the exact same failure mode. There, every RPC error explicitly "leaves the conflict in place" (keeps blocking, i.e., refuses to sign), with the doc comment stating: "If we have no saved burn block, or the node is unreachable, the conflict keeps blocking. That only delays the replacement until our signature goes stale, whereas wrongly signing cannot be taken back." [6](#0-5) [7](#0-6) 

The project's own architecture documentation confirms this is a known asymmetry rather than an oversight elsewhere: the mermaid diagram explicitly shows `TIP -- "node unreachable" --> SIGN` for this own-tenure branch, in contrast to every other "node unreachable" branch in the same flow, which resolves to holding/refusing to sign. [8](#0-7) [9](#0-8) 

This is directly analogous to the Chainlink L2-sequencer report: rather than treating "cannot verify the freshness/state of the authoritative source" (sequencer uptime feed / node tenure tip) as untrustworthy and refusing to act, the code assumes a favorable answer and proceeds with the consequential action (signing).

### Impact Explanation
If the signer previously signed block A at height H in tenure T (signature now stale, i.e., past `tenure_last_block_proposal_timeout`), and a second, conflicting block B at the same height H in the same tenure T is proposed and pre-committed by enough peers, the *only* remaining safeguard against signing both A and B is this `get_tenure_tip` check. A single transient failure of the signer's own node RPC at that moment causes the signer to sign B despite never confirming that A (or the tenure's actual chain state) doesn't already occupy that height. This produces a genuine equivocation: one signer's valid signature over two conflicting blocks at the same height/tenure, contributing to whichever block reaches the aggregate signing threshold — i.e., a signer signing a conflicting block, matching the Critical impact category (signer signing an invalid/non-canonical/conflicting block).

### Likelihood Explanation
The trigger condition — this signer's own local RPC call to its own node failing or timing out at the moment a stale same-tenure conflict is being re-evaluated — is a common, single-node-local event (node restart, brief network hiccup, RPC timeout), not something requiring a majority of signers, another signer's key, or any auth token. The specific scenario (an earlier signature going stale while a competing same-height block in the same tenure gets proposed and reaches the pre-commit threshold) is an explicitly anticipated and handled path in this code (as shown by the surrounding comments and the dedicated test suite for sibling/conflict scenarios), making the precondition realistic under normal reorg-recovery/miner-timeout operation, not a contrived edge case.

### Recommendation
Make the error branch in this own-tenure check consistent with `conflict_still_blocks`: on `Err(e)` from `get_tenure_tip`, treat the conflict as still blocking (i.e., `return` without signing) rather than falling through to sign, mirroring the "leave the conflict in place" default used for every other node-unreachable case in this same guard chain.

### Proof of Concept
1. Signer S signs block A (height H, tenure T) via the normal pre-commit-threshold path; `signed_self` is stamped with a timestamp.
2. `tenure_last_block_proposal_timeout` elapses so A's endorsement becomes stale relative to `freshness_cutoff` used in `handle_block_pre_commit` (line 1393-1421), so the freshness/`conflict_still_blocks` guard no longer vetoes.
3. A competing block B, also at height H in tenure T (e.g., a re-proposed tenure-start block after the first failed to gather full consensus), is broadcast and gathers pre-commits from ≥70% weight.
4. When S's local pre-commit tally for B crosses the threshold, `handle_block_pre_commit` reaches the own-tenure block at line 1432; the conflict with A is found (same `consensus_hash`), so `stacks_client.get_tenure_tip(&block_info.block.header.consensus_hash)` is called.
5. If that RPC call to S's own node returns an error (any transient timeout/connection failure — reproducible by stalling/killing the node's `/v3/tenures/info` endpoint at this instant), execution falls into the `Err(e)` branch (lines 1449-1456), which only logs a warning and does not `return`.
6. Execution proceeds past the guard, `mark_locally_accepted` is called, and S broadcasts a signature over B — even though B may conflict with A, which S already signed at the same height in the same tenure.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1134-1136)
```rust
    /// If we have no saved burn block, or the node is unreachable, the conflict keeps blocking.
    /// That only delays the replacement until our signature goes stale, whereas wrongly signing
    /// cannot be taken back.
```

**File:** stacks-signer/src/v0/signer.rs (L1192-1203)
```rust
        let node_reaches_conflict = match stacks_client.get_tenure_tip(&conflict.consensus_hash) {
            Ok(tip) => tip.anchored_header.height() >= conflict.stacks_height,
            // A 404 is an answer, not a failure: the node has no blocks in that tenure at all.
            Err(ClientError::RequestFailure(reqwest::StatusCode::NOT_FOUND)) => false,
            Err(e) => {
                warn!("{self}: Failed to fetch the canonical tip of a conflicting block's tenure: {e:?}. Leaving the conflict in place.";
                    "conflicting_consensus_hash" => %conflict.consensus_hash,
                    "conflicting_block_height" => conflict.stacks_height,
                );
                return true;
            }
        };
```

**File:** stacks-signer/src/v0/signer.rs (L1423-1457)
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
```

**File:** stacks-signer/src/v0/signer.rs (L1466-1471)
```rust
        // It is only considered globally accepted IFF we receive a new block event confirming it OR see the chain tip of the node advance to it.
        if let Err(e) = block_info.mark_locally_accepted(false) {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally accepted: {e:?}",);
            }
        }
```

**File:** docs/signer-flows.md (L264-268)
```markdown
    OWN -- yes --> TIP{"own tenure confirmed<br/>at ≥ this height?<br/>get_tenure_tip(own tenure)"}
    TIP -- yes --> HOLD2["refuse to sign"]:::hold
    TIP -- "no — never confirmed" --> SIGN
    TIP -- "node unreachable" --> SIGN
    OWN -- no --> SIGN["SIGN: mark_locally_accepted,<br/>handle_block_signature,<br/>broadcast acceptance"]:::good
```
