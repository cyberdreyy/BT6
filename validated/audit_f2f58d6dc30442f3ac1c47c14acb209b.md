### Title
Time-based freshness cutoff (not actual chain state) can let a signer sign two genuinely conflicting sibling blocks at the same height - ([File: stacks-signer/src/v0/signer.rs])

### Summary
The signer's pre-commit-to-signature gate decides whether a previously signed/accepted block still "blocks" a new conflicting sibling at the same height using a **wall-clock freshness window** (`tenure_last_block_proposal_timeout_secs`, default 30s) rather than the actual, verified state of the chain. This mirrors the reported bug class: pre-estimating a future cutoff from a time assumption instead of deriving it from the real, checkpointed state, which can desynchronize the estimate from reality and lead to an incorrect outcome — here, a double-signature on conflicting blocks instead of a miscalculated reward.

### Finding Description
In `handle_block_pre_commit` (`stacks-signer/src/v0/signer.rs`), before finally signing a block that reached the pre-commit threshold, the signer queries previously signed/accepted conflicting blocks at the same or higher height via `SignerDb::get_signed_conflicts` [1](#0-0)  and computes a `freshness_cutoff` purely from the local epoch clock: [2](#0-1) 

The veto logic only blocks signing the new (conflicting) block if the conflict is *fresh AND* the reorg permit doesn't stand *AND* the node confirms the conflict "still blocks" it: [3](#0-2) 

Because Rust's `&&` short-circuits left-to-right, once `conflict.last_endorsed > freshness_cutoff` evaluates to `false` (i.e., the prior signature is judged "stale" purely by elapsed wall-clock time), the code **never calls `reorg_permit_stands` or `conflict_still_blocks`** for that conflict — it never asks the node whether the earlier, conflicting block is actually still live or could still become canonical. The only remaining veto path only applies to *same-tenure* conflicts (`conflict.consensus_hash == block_info.block.header.consensus_hash`) [4](#0-3) . A *different-tenure* sibling conflict (e.g., the previous tenure's block vs. a new tenure's block at the same height) that has merely aged past the configured timeout is subject to **no veto check at all**, regardless of whether the earlier block might still be pushed to the node and become canonical.

This is structurally the same defect pattern as the reported issue: a value meant to reflect real chain progress/finality (`stakeEndBlock`, derived from an assumed constant `blockTime`) is instead estimated from elapsed wall-clock time, and if the real-world timing diverges from the assumption (clock skew between signer and miner/node, network delay, deliberate delay by the current or next miner in propagating a block), the estimate stops matching reality. Here that means the signer's "is my old signature still relevant" checkpoint is wrong, and the guard against double-signing conflicting blocks silently disengages.

### Impact Explanation
If the freshness window elapses on a signer's local clock while the earlier signed block A is still capable of being finalized by the node (e.g., due to slow gossip propagation, a miner delaying broadcast, or clock skew), that signer can go on to sign a second, conflicting sibling block B at the same height in a different tenure. This is exactly the "signer signing a conflicting block" scenario called out as Critical impact: two blocks at the same height, both bearing this signer's signature, undermines the one-signature-per-height safety invariant the pre-commit/conflict-check logic is designed to enforce, and can contribute to a chain split/equivocation if enough signers experience the same timing skew.

### Likelihood Explanation
The trigger requires only ordinary network/timing conditions achievable by a single (possibly malicious) miner plus normal gossip delay: propose block A, have (or cause) its acceptance/broadcast to be delayed beyond `tenure_last_block_proposal_timeout_secs` (default 30s) from this signer's point of view, then propose a conflicting sibling B in a new tenure. No majority of signers, no other signer's key, and no auth token are needed — this is purely a function of the local timing race on a single signer instance, matching the "one-slot miner plus gossip" trigger constraint.

### Recommendation
Do not use the wall-clock freshness window as a hard bypass for the liveness/canonical-status check. Always consult the node via `conflict_still_blocks` (and `reorg_permit_stands`) before allowing a same-height conflicting sibling to be signed, using the timeout only to decide *how hard* to re-check, not to skip the check entirely — analogous to the recommended fix in the referenced report: base the decision on the actual/verified chain state (checkpointed height/tenure status) rather than an estimate derived from elapsed time.

### Proof of Concept
Not independently reproduced in this pass (no execution environment available); the control-flow trace above is drawn directly from `stacks-signer/src/v0/signer.rs` lines 1383-1457, showing that the `conflict_still_blocks` node-state check is unreachable once `last_endorsed <= freshness_cutoff` for a differing-tenure conflict. A concrete PoC would involve: (1) signer signs block A for tenure X at height H; (2) hold back A's global acceptance/propagation for longer than `tenure_last_block_proposal_timeout_secs` from this signer's clock; (3) propose conflicting sibling B for tenure Y at height H; (4) observe the signer sign B via the pre-commit path without any `conflict_still_blocks`/`reorg_permit_stands` query, while A might still be confirmable at the node.

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

**File:** stacks-signer/src/v0/signer.rs (L1393-1397)
```rust
        let freshness_cutoff = get_epoch_time_secs().saturating_sub(
            self.proposal_config
                .tenure_last_block_proposal_timeout
                .as_secs(),
        );
```

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
