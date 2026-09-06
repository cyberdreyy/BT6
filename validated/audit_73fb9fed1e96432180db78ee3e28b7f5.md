### Title
Stale third-tenure sibling conflicts skip the node-state re-verification entirely, letting a signer double-sign after pure wall-clock decay - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`handle_block_pre_commit` gates the double-sign guard on `conflict.last_endorsed > freshness_cutoff`, a pure local timestamp comparison, before ever asking the node whether a conflicting sibling is still live. For conflicts that live in a tenure that is neither the proposed block's own tenure nor its tenure-change parent (a genuine "third tenure" sibling, exactly the case the code's own comments call out as invisible to the earlier chainstate re-check), once `last_endorsed` ages past `tenure_last_block_proposal_timeout` the conflict is dropped with **zero** node query and the signer proceeds to `SIGN`, even if the sibling is still fully canonical.

### Finding Description
The relevant path is `handle_block_pre_commit` in `stacks-signer/src/v0/signer.rs`:

1. `check_block_against_signer_db_state` (line 1345) runs first, but it only checks the proposed block's **own** tenure or, for a tenure-change block, its **parent** tenure — never an unrelated third tenure holding a conflicting sibling at the same height. This is explicitly documented in `docs/signer-flows.md` ("the re-check only ever looks at _one_ tenure ... so a signed sibling at the same height in a third tenure is invisible to it").
2. `get_signed_conflicts` (`stacks-signer/src/signerdb.rs:1606-1625`) returns every signed block at or above the proposed height in ANY tenure, tagging each with `last_endorsed = MAX(signed_self, signed_group)`.
3. The fresh-conflict loop only calls the real chain-state check `conflict_still_blocks` (which queries `get_sortition_by_burn_hash` / `get_tenure_tip`) for conflicts where `conflict.last_endorsed > freshness_cutoff`: [1](#0-0) 
4. If that fresh-conflict loop finds nothing, the code falls to a second check that only re-examines conflicts whose `consensus_hash == block_info's own tenure` via `get_tenure_tip`: [2](#0-1) 
5. A stale conflict sitting in neither the proposed block's own tenure nor covered by step 1 is matched by **neither** check and the code logs "none of those conflicts still blocks it" and signs: [3](#0-2) 

`last_endorsed` is stamped once, at the time a block is signed/accepted (`signed_self`/`signed_group`), and is never refreshed while the block simply continues to be canonical. So a genuinely still-canonical sibling `A` (built in a third tenure, e.g. a competing tenure-change block confirming the same parent tip as the proposed block `B`) becomes "stale" purely by the passage of `tenure_last_block_proposal_timeout` wall-clock seconds — with no real chain progression, no reorg, and no node-observable change. The code's own comment claims this is safe because "the chainstate checks above" settle it, but per point 1 those checks structurally cannot see a third-tenure sibling. This is an internal contradiction in the guard's design: the blind spot the fresh-conflict mechanism exists to cover is exactly the case that gets waved through once it goes stale.

### Impact Explanation
This breaks the equality `conflict_marked_stale == conflict_actually_resolved_on_chain` for cross-tenure (third-tenure) siblings specifically. If block `A` (tenure X) and block `B` (tenure Y, attacker's own tenure) are true siblings confirming the same parent tip at the same height, and the attacker (or simply elapsed time) delays `B`'s pre-commit threshold crossing past `tenure_last_block_proposal_timeout` after `A`'s last signature/acceptance timestamp, the signer signs `B` without ever asking the node whether `A` is still canonical. If `A` is in fact still fully live, the signer has now placed a valid signature on two conflicting blocks at the same height — the Critical chain-safety violation (double-sign) this guard exists to prevent.

### Likelihood Explanation
Preconditions: a natural or attacker-engineered fork producing two sibling tenures/blocks at the same height (one already signed as `A`), and `B` reaching the pre-commit threshold only after `A`'s `last_endorsed` ages past the configurable `tenure_last_block_proposal_timeout` (a value on the order of the block-timeout config, not attacker-controlled but exploitable by simply delaying gossip of `B`'s pre-commits). The attacker needs only to win one miner slot and control the timing/re-proposal of their own `BlockProposal`/pre-commit gossip — consistent with the "one slot plus gossip" threat model. It is repeatable each time such a cross-tenure sibling situation arises and the timeout elapses before consensus is reached on the replacement.

### Recommendation
For any conflict that is not covered by `check_block_against_signer_db_state`'s single-tenure check (i.e., not the proposed block's own tenure and not its tenure-change parent), do not allow simple staleness (`last_endorsed <= freshness_cutoff`) to bypass a node query. Instead, always run `conflict_still_blocks` (or an equivalent node-derived liveness check) for third-tenure conflicts regardless of freshness, using the freshness cutoff purely as a round-trip optimization for conflicts already covered by the existing own/parent-tenure recheck.

### Proof of Concept
Rust signer test plan (extending `stacks-signer/src/v0/tests.rs`'s `run_cross_tenure_scenario` harness):
1. Set up tenure X producing block `A` at height h, signed (`signed_self`/`signed_group` set), and keep the burnchain/Stacks node tip **static** so `A` remains genuinely canonical (`get_tenure_tip`/`get_sortition_by_burn_hash` would both prove it live if queried).
2. Propose sibling block `B` in tenure Y (different from X, and not X's parent) at height h.
3. Advance only the **wall clock** (mock `get_epoch_time_secs`) past `tenure_last_block_proposal_timeout` without changing any node-side state.
4. Drive `B` through pre-commit to threshold and call `handle_block_pre_commit`.
5. Assert that the mock `StacksClient` records a call to `get_tenure_tip`/`get_sortition_by_burn_hash` for `A`'s tenure (it must, to prove liveness) — and assert the test **fails** today because no such call occurs and `info_b.signed_self.is_some()` becomes true despite `A` being provably still live, demonstrating the signer signed a conflicting sibling based on elapsed time alone rather than re-derived chain state.

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
