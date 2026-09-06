### Title
Same-signer double-signing at (tenure, height) once a stale locally-signed sibling's tenure tip has not advanced - `handle_block_pre_commit` ([File: stacks-signer/src/v0/signer.rs])

### Summary
`handle_block_pre_commit` re-checks conflicts before signing a block that has crossed the pre-commit threshold, but the own-tenure conflict guard only blocks a competing block B if the node's tenure tip has already advanced to or past B's height. A previously signed sibling A that this same signer already produced `signed_self` for, but which the node has not yet processed into its tenure tip, stops blocking once `conflict.last_endorsed` falls outside `tenure_last_block_proposal_timeout` (the freshness cutoff), letting the signer sign a second, conflicting block B at the identical (tenure, height).

### Finding Description
The equality "distinct blocks signed by this signer at (tenure T1, height H) must be ≤1" is enforced in `handle_block_pre_commit` by two layered checks against `get_signed_conflicts`: [1](#0-0) 

The first check only vetoes signing when a conflict is both fresh (`last_endorsed > freshness_cutoff`, cutoff derived from `tenure_last_block_proposal_timeout`) and `conflict_still_blocks`. Once A's `signed_self`/`signed_group` timestamp ages past the timeout, this first veto no longer fires.

The fallback, own-tenure guard then runs: [2](#0-1) 

This only blocks signing B if `stacks_client.get_tenure_tip(...)` reports the node's tenure tip is already `>= chain_length` of B. If A was only ever locally/group-signed (never announced/processed by the node as `NewBlock`), the node's tenure tip has not advanced, so this branch does not block, and the signer proceeds to `mark_locally_accepted(false)` for B and broadcasts a fresh signature — even though it already holds `signed_self` on A at the same height in the same tenure.

The chainstate re-check that runs earlier in the same function (`check_block_against_signer_db_state` → `confirms_latest_block_in_same_tenure` → `check_latest_block_in_tenure`) also stops vetoing once `get_tenure_last_block_info` considers A timed out (`stacks-signer/src/chainstate/mod.rs:330-364`), since a timed-out tenure tip returns `None` and the height-conflict branch is skipped entirely (`stacks-signer/src/chainstate/mod.rs:390-419`).

Both guards derive "is this a real, still-live chain state" from the stacks-node's reported tenure tip rather than from whether *this signer itself* has already put a signature on a conflicting block at that height. The equivocation guard is therefore keyed to canonicity/liveness of the conflict as seen by the node, not to the signer's own signing history, which is exactly the gap the question identifies: `signed_self` can be set on two different blocks at the same (tenure, height) purely because the node hasn't caught up, with no consensus failure having occurred.

### Impact Explanation
This breaks the signer-side uniqueness/equivocation invariant that a signer must never place its signature over two conflicting blocks at the same tenure and height — a chain-safety property. A single equivocating miner who is also the current tenure's block producer can propose A, let it cross the pre-commit threshold and get `signed_self` from honest signers, then (after `tenure_last_block_proposal_timeout` has elapsed and before the signed block has been processed into the node's tenure tip) propose a distinct B at the same height, harvesting a second, conflicting `signed_self` from the same signers. If this occurs across the honest signer set broadly, two conflicting blocks can each accumulate real signatures, which is the raw material for a fork/equivocation at the signature layer. This matches the "signer signing a conflicting block (chain safety)" Critical category.

### Likelihood Explanation
Preconditions: the attacker needs only one miner slot (to be the tenure's block producer, and thus free to propose two different block bodies for the same height) and the ability to gossip `BlockProposal` messages, matching the stated unprivileged-attacker model. No majority of signers, no auth_token, and no local host access is required. The only environment dependency is that `tenure_last_block_proposal_timeout` be shorter than, or comparable to, the round trip from "signature threshold reached" to "node processes the block into its tenure tip." This is a real, operator-configured value (not test-only), and the attacker fully controls when to gossip B relative to A's timestamp, since all pre-commit/acceptance timestamps are visible over StackerDB. The condition is repeatable each time the miner wins a tenure.

### Recommendation
Track, per signer, whether `signed_self`/`signed_group` has already been set on any block at a given (tenure, height) independent of node tenure-tip visibility, and make that record itself the veto for a competing block at the same (tenure, height) rather than deferring entirely to `get_tenure_tip`. At minimum, `conflict_still_blocks`'s own-tenure branch in `handle_block_pre_commit` (stacks-signer/src/v0/signer.rs:1432-1457) should not be bypassable purely by node-tip lag when the conflict is the signer's own prior `signed_self` in the exact same tenure — that case should require positive proof that A cannot be canonical (e.g., a Bitcoin-reorg-derived permit via `reorg_permit_stands`) rather than "the node hasn't heard of it yet."

### Proof of Concept
Rust signer state-machine test plan (using `stacks-signer` unit test harness, e.g. in `stacks-signer/src/v0/tests.rs` or `stacks-signer/src/chainstate/tests/v2.rs` style):
1. Build a `ProposalEvalConfig` with `tenure_last_block_proposal_timeout = Duration::from_millis(50)` (or similarly short).
2. Construct block A (`BlockProposal` at tenure `T1`, `chain_length = H`), drive it through the normal path to `mark_locally_accepted(false)` (simulating pre-commit threshold reached in `handle_block_pre_commit`), and assert `block_info_a.signed_self.is_some()`.
3. Do not simulate a `NewBlock`/tenure-tip update from the mocked `StacksClient` (i.e., keep `get_tenure_tip` returning a height below H, or an error causing "assume higher").
4. Sleep past the configured timeout (e.g. 100ms) so `get_epoch_time_secs`/mocked clock puts A's `signed_self` outside the freshness window.
5. Construct block B (distinct `NakamotoBlock`, same `consensus_hash` = `T1`, same `chain_length = H`, different `timestamp`/tx content so `signer_signature_hash` differs), feed it through `handle_block_pre_commit` with enough mocked pre-commit weight to cross `compute_voting_weight_threshold`.
6. Assert `block_info_b.signed_self.is_some()` and `block_info_a.signer_signature_hash() != block_info_b.signer_signature_hash()`, proving both A and B carry this signer's `signed_self` at the same `(T1, H)`. [3](#0-2)

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1383-1421)
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

**File:** stacks-signer/src/v0/signer.rs (L1467-1471)
```rust
        if let Err(e) = block_info.mark_locally_accepted(false) {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally accepted: {e:?}",);
            }
        }
```
