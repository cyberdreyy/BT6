### Title
Double evaluation of `reorg_permit_stands` in `handle_block_pre_commit` allows a stale-but-live conflict check to reach an inconsistent verdict within a single pre-commit evaluation - (File: stacks-signer/src/v0/signer.rs)

### Summary
Both node-derived guard checks in `handle_block_pre_commit` call `reorg_permit_stands(stacks_client, conflict)` independently, each performing its own `get_sortition_by_burn_hash` round-trip to the stacks-node, instead of evaluating the permit's validity once and reusing the cached verdict for the rest of the function.

### Finding Description
`handle_block_pre_commit` gathers all signed conflicts for a proposed block via `get_signed_conflicts` [1](#0-0) , then runs two separate closures over that same `conflicts` vector:

1. A freshness-gated `.find()` over ALL tenures that calls `self.reorg_permit_stands(stacks_client, conflict)` and `self.conflict_still_blocks(...)` to decide whether to silently refuse to sign [2](#0-1) .
2. A second `.any()` restricted to same-tenure conflicts (matched by `consensus_hash`) that calls `self.reorg_permit_stands(stacks_client, conflict)` again on the same conflict objects, then separately queries `get_tenure_tip` to decide whether to refuse or fall through to signing [3](#0-2) .

`reorg_permit_stands` itself is not cached anywhere — it performs a fresh HTTP call to `get_sortition_by_burn_hash` every time it is invoked [4](#0-3) . Any conflict that is both fresh and in the block's own tenure is therefore evaluated by both closures within the same `handle_block_pre_commit` call, each producing an independent, uncached answer to "is this permit still valid?" This is structurally the same bug class as the Vyper `slice` double-eval (CVE-2024-32646): an expression that is expected to represent one stable fact is evaluated more than once instead of being computed once and reused, so the two evaluations of the "same" fact can diverge purely from the act of asking twice (network jitter, a sortition transaction landing between the two round-trips, or a transient 404 from the node) rather than from the block or its own state changing between distinct pre-commit messages.

The design comment explicitly assumes each *evaluation* (i.e., each `handle_block_pre_commit` invocation, triggered by a new pre-commit message) may see a different, self-correcting answer [5](#0-4) , but it does not account for the fact that a single invocation of `handle_block_pre_commit` itself makes two independent queries for the same fact, silently assuming they agree.

### Impact Explanation
If `reorg_permit_stands` returns `true` (permit stands) on the first call — correctly excluding a same-tenure fresh conflict from the freshness-gated `.find()` refusal — but returns `false` on the second call moments later (e.g., a transient network hiccup, or the permitting sortition being reported as 404 by the node under load, per the explicit fallback in the code that treats any error as "permit void" [6](#0-5) ), the second `.any()` check proceeds to a separate `get_tenure_tip` decision that was never gated by the same permit context established in the first check. Depending on tip height, this can let the signer sign a block that the first (correct) evaluation had implicitly relied on the permit to allow — or refuse one it should have allowed — using two different, un-reconciled views of the same fact inside a single decision. Because the eventual signature is a durable, aggregatable bearer instrument (per the docs, a signature "can still be aggregated toward the 70% threshold" even after being superseded) [7](#0-6) , an inconsistent verdict reached during this evaluation risks the signer emitting a signature that the reorg-permit bookkeeping intended to be conditioned on a single, stable canonical-fork determination.

### Likelihood Explanation
This requires only a single one-slot miner submitting a re-proposed or competing block plus ordinary gossip of pre-commits to trigger `handle_block_pre_commit`'s pre-commit-threshold path (no majority-signer collusion needed); the double network call happens automatically whenever a same-tenure fresh conflict exists. Actually forcing the two `get_sortition_by_burn_hash` calls to diverge requires timing a real (or node-side transient) change between the two round-trips, which is a narrower window than the Vyper original (which was trivially and deterministically exploitable via `pop()`). This materially lowers likelihood relative to the source CVE, and I could not find a case in the reachable code where this divergence provably flips the final sign/no-sign outcome to a *conflicting* signature, only that the two branches consult stale/fresh views independently without reconciliation — the exact exploit path to a concrete conflicting-signature outcome remains unconfirmed with the available code and test coverage.

### Recommendation
Compute `reorg_permit_stands(conflict)` once per conflict at the top of `handle_block_pre_commit` (e.g., into a `HashMap<ConsensusHash, bool>` or by annotating each `SignedConflictInfo`), and have both the freshness-based `.find()` and the same-tenure `.any()` consult that single cached value instead of independently querying the node twice for the same fact within one evaluation.

### Proof of Concept
Not independently reproduced against a running node; based on static code-path analysis of `stacks-signer/src/v0/signer.rs::handle_block_pre_commit` lines 1383-1457, where `reorg_permit_stands` is invoked twice for a conflict that is both fresh and same-tenure, each call performing its own uncached `get_sortition_by_burn_hash` HTTP round-trip [2](#0-1) [8](#0-7) .

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1222-1247)
```rust
    fn reorg_permit_stands(
        &self,
        stacks_client: &StacksClient,
        conflict: &SignedConflictInfo,
    ) -> bool {
        let Some(superseded_by) = &conflict.superseded_by else {
            return false;
        };
        match stacks_client.get_sortition_by_burn_hash(&superseded_by.burn_block_hash) {
            Ok(_) => true,
            Err(ClientError::RequestFailure(reqwest::StatusCode::NOT_FOUND)) => {
                info!("{self}: The tenure we permitted to reorg a conflicting block's tenure was itself orphaned by a burnchain fork. The permit no longer excludes the conflict.";
                    "conflicting_consensus_hash" => %conflict.consensus_hash,
                    "superseded_by_consensus_hash" => %superseded_by.consensus_hash,
                    "superseded_by_burn_block_hash" => %superseded_by.burn_block_hash,
                );
                false
            }
            Err(e) => {
                warn!("{self}: Failed to check whether the sortition that permitted a reorg is still canonical: {e:?}. Treating the permit as void.";
                    "conflicting_consensus_hash" => %conflict.consensus_hash,
                    "superseded_by_consensus_hash" => %superseded_by.consensus_hash,
                );
                false
            }
        }
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

**File:** docs/signer-flows.md (L322-327)
```markdown
A conflict is any block a signature was ever put over — ours, or a group
threshold we observed — whatever its state now. In particular rejection, even
_global_ rejection, does not clear one: a rejection is a revocable opinion,
while a signature is a bearer instrument that can still be aggregated toward
the 70% threshold if rejecting signers change their minds. Only staleness or
node-derived death (the two questions above) clears a conflict.
```
