Based on the investigation, I found a concrete analog in the signer's pre-commit conflict guard where an RPC failure is treated as "safe to sign" instead of "safe to refuse," inverted from the guard's own design intent elsewhere in the same code path. This mirrors the LiFi bug class: an external call that fails is handled by taking the unsafe fallback action instead of preserving the safety invariant.

### Title
Fail-open RPC-error handling in the same-tenure pre-commit conflict guard lets a signer double-sign at the same height/tenure - (File: stacks-signer/src/v0/signer.rs)

### Summary
`Signer::handle_block_pre_commit` guards against a signer producing two signatures for conflicting blocks at the same height. Every RPC failure encountered while resolving whether a conflict is still "live" is documented and coded to *keep the conflict blocking* (fail closed) — except one: the second, same-tenure-only check that queries `get_tenure_tip` to see whether the node already confirmed a competing block at or above the proposed height. On `Err`, that branch merely logs "Treating the tenure as unconfirmed" and falls through to sign, instead of refusing like every sibling error path in the same function does.

### Finding Description
`conflict_still_blocks` (used earlier in the same evaluation) treats every node-communication failure as "leave the conflict in place" (refuse to sign): failures from `get_sortition_by_burn_hash`, `get_peer_info`, and `get_tenure_tip` all `return true` on `Err`, per the documented rationale "If we have no saved burn block, or the node is unreachable, the conflict keeps blocking. That only delays the replacement... whereas wrongly signing cannot be taken back." [1](#0-0) [2](#0-1) 

But the second, own-tenure-specific guard in `handle_block_pre_commit` — added specifically to catch a *stale* conflict that the chainstate re-check does not cover for tenure-change blocks — does the opposite on error:
```
match stacks_client.get_tenure_tip(&block_info.block.header.consensus_hash) {
    Ok(tip) => {
        let tip_height = tip.anchored_header.height();
        if tip_height >= block_info.block.header.chain_length {
            warn!(... "Refusing to sign.");
            return;
        }
    }
    Err(e) => {
        warn!(... "Treating the tenure as unconfirmed.");
    }
}
``` [3](#0-2) 
On `Err(e)`, execution merely warns and falls through the `match` — it does not `return`, so the function proceeds to `mark_locally_accepted` / sign the block. [4](#0-3) 

This branch is reached precisely for a conflict in the block's *own tenure* that survived the earlier fresh/live check (i.e., it is stale, or `conflict_still_blocks` returned false) but per the guard's own comment "still blocks if the node already has that tenure at or above the proposed height, since the proposal then duplicates state the node has already built on." [5](#0-4) 
The only way to actually determine "does the node already have that height" is the `get_tenure_tip` call — and precisely when that call cannot be answered, the code silently assumes the safe answer ("no, not yet confirmed") rather than the conservative one used everywhere else in this same function.

### Impact Explanation
If a signer had already signed block A at height *h* in tenure T (its endorsement later going stale per `tenure_last_block_proposal_timeout`), and the node genuinely still serves A as tip *h*, then a re-proposed/duplicate block B at the same height *h* in the same tenure must be refused — signing B as well would be a literal equivocation (two conflicting blocks at the same height/tenure both carrying this signer's signature), the exact double-sign event the whole guard exists to prevent. If the RPC to the signer's own node fails or times out during this specific re-evaluation (e.g., a transient node restart, RPC timeout, brief connectivity blip — all realistic operational events, not requiring compromise of any key or a majority of signers), the signer proceeds to sign B anyway. This is a Critical-class outcome per the given rubric: "a signer signing an invalid, non-canonical, or conflicting block."

### Likelihood Explanation
The trigger requires only: (1) a miner (one slot) proposing a duplicate/competing block at the same height in the same tenure after the original's signature has gone stale — fully attacker-controlled timing via re-proposal, which the codebase's own tests show is a normal, expected scenario (`reproposal_signs_replacement_after_conflict_times_out`); and (2) the signer's RPC call to its own stacks-node failing or timing out at that moment — an ordinary operational condition, not requiring any cryptographic compromise, majority collusion, or access to another party's key. No majority of signers is needed; a single signer hitting this race independently produces its own conflicting signature.

### Recommendation
Make the `Err(e)` branch of the `get_tenure_tip` call in this guard consistent with the rest of `handle_block_pre_commit`/`conflict_still_blocks`: on failure to reach the node, refuse to sign (return) rather than treating the tenure as unconfirmed, mirroring the fail-closed behavior used for every other RPC failure in this same conflict-resolution path.

### Proof of Concept
1. Signer signs block A (tenure T, height h); node processes/serves A as the tip at height h.
2. `tenure_last_block_proposal_timeout` elapses so A's endorsement is now stale.
3. Miner re-proposes block B at height h in tenure T (a legitimate re-proposal flow the codebase explicitly supports for stall recovery).
4. B crosses the pre-commit threshold; `handle_block_pre_commit` re-evaluates conflicts. The earlier fresh/live check does not block (A is stale). Execution reaches the own-tenure branch and calls `get_tenure_tip(T)`.
5. Force/observe a transient failure of that RPC call (network blip, node restart, timeout).
6. Per the code, the `Err` arm only logs and falls through — the signer proceeds to sign B, producing a second, conflicting signature at height h in tenure T alongside its earlier signature over A.

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
