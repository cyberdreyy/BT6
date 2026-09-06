### Title
Time-based staleness cutoff bypasses the equivocation guard, letting a delayed miner induce a signer to sign a conflicting/duplicate block at the same height - ([File: stacks-signer/src/v0/signer.rs])

### Summary
The pre-commit → signature guard in `handle_block_pre_commit` decides whether a previously signed/accepted block still "blocks" a new conflicting proposal at the same (or higher) height using a **local timestamp check** (`last_endorsed > freshness_cutoff`) that short-circuits the query to the node (`conflict_still_blocks`) whenever the conflict is older than `tenure_last_block_proposal_timeout`. This mirrors the RLQ-1 pattern exactly: a decision that should depend on the *current* state of the world (is the conflicting block still canonical?) is instead gated by a stale, locally-recorded "last update" time, so a party who controls timing (here, the miner, by waiting out the timeout and then broadcasting a competing block) can cause a signer to skip the freshness check that would otherwise have kept it from re-signing.

### Finding Description
`store_and_process_block_signature`/`handle_block_pre_commit` in `stacks-signer/src/v0/signer.rs` re-checks for conflicting, already-signed/accepted blocks before releasing a new signature: [1](#0-0) 

The conflict-blocking predicate is:
```
conflict.last_endorsed > freshness_cutoff
    && !self.reorg_permit_stands(stacks_client, conflict)
    && self.conflict_still_blocks(stacks_client, conflict, block_info.block.header.chain_length)
```
Because Rust's `&&` short-circuits, `conflict_still_blocks` (the only check that actually asks the node whether the earlier signed/accepted block is still part of the canonical chain, per `conflict_still_blocks`'s own doc comment at [2](#0-1)  is never even invoked once `last_endorsed <= freshness_cutoff`. The freshness cutoff is purely `now - tenure_last_block_proposal_timeout`, a local wall-clock comparison unrelated to whether the earlier block is truly dead.

This is the direct analog of RLQ-1's "reward based on last-updated level, not current level" bug class: the guard's decision is driven by *when it was last touched* rather than by *the current, verifiable state*. In RLQ-1 a user could withhold calling `updatePosition` to be rewarded at a stale (higher) level; here a miner can withhold/delay a competing proposal until a signer's `last_endorsed` timestamp for the block it already signed goes stale (past `tenure_last_block_proposal_timeout`), and then push a second, conflicting block at the same height. The signer will skip `conflict_still_blocks` entirely and evaluate only the tenure-level check (`conflicts.iter().any(... consensus_hash == ...)` at [3](#0-2)  ), which only fires for same-tenure conflicts — a sibling block in a *different* tenure at the same height sails through unblocked, even though the originally signed block may still be perfectly canonical and reachable from the node's tip.

### Impact Explanation
This lets a signer sign two conflicting blocks at the same stacks height (once a fresh one, once the "stale" replacement) purely because of elapsed time rather than any real change in chain state — the exact "a signer signing an invalid/non-canonical/conflicting block" Critical scenario called out in the rules. If enough signers hit staleness at slightly different times (their `last_endorsed` clocks differ, and `tenure_last_block_proposal_timeout` is a fixed constant each independently applies), a miner controlling timing of proposals can split signatures across two blocks at one height.

### Likelihood Explanation
The trigger requires nothing beyond what a single miner (plus normal StackerDB gossip of a second `BlockProposal`) can already do: propose a block, get it pre-committed/signed by the honest majority, then simply wait past `tenure_last_block_proposal_timeout` before broadcasting a second, conflicting block at the same height (e.g., from a different, later tenure). No majority of signers, no key compromise, and no auth token is needed. However, I was not able to fully verify (due to running out of tool budget) exactly which events update `last_endorsed`/populate `SignedConflictInfo` in `signerdb.rs`, nor confirm whether some other code path re-freshens `last_endorsed` on every subsequent proposal evaluation (which would narrow or close this window). This uncertainty means the practical likelihood/exploitability could be lower than described if `last_endorsed` is refreshed more aggressively than assumed, or if `conflict_still_blocks`'s node-level check is otherwise reached via a different code path I didn't inspect (e.g., `check_block_against_signer_db_state`, called earlier in the same function at [4](#0-3) , might independently catch some — but not all — of these cases).

### Recommendation
Do not let `conflict_still_blocks` (the authoritative, node-derived liveness check) be skipped purely because a local timer has expired. Either: (1) always query `conflict_still_blocks` regardless of freshness and use `last_endorsed` only as a secondary/advisory signal, or (2) require that staleness be corroborated by an independent, verifiable fact (e.g., the node's tip has definitively moved past the conflicting block, or the conflicting tenure's sortition is confirmed orphaned) rather than by elapsed wall-clock time alone, mirroring the RLQ-1 recommendation to evaluate "current" state rather than a lazily-cached snapshot.

### Proof of Concept
Not independently reproduced against a running node/test harness within this investigation; the analysis is based on static code review of `handle_block_pre_commit`'s conflict-check logic. A concrete PoC would need to be built in `stacks-node/src/tests/signer/v0/mod.rs` using the existing signer test harness: (1) have the miner get block A signed at height N; (2) advance time past `tenure_last_block_proposal_timeout` without confirming A is orphaned; (3) propose block B (different tenure) at height N; (4) show a signer's `handle_block_pre_commit` signs B without ever calling `conflict_still_blocks` for A, and that the node still considers A canonical/reachable, i.e., B and A now both hold signer signatures at the same height. This exact PoC construction is not verified end-to-end due to running out of investigation budget.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1137-1206)
```rust
    fn conflict_still_blocks(
        &self,
        stacks_client: &StacksClient,
        conflict: &SignedConflictInfo,
        proposed_height: u64,
    ) -> bool {
        if let Ok(burn_block) = self
            .signer_db
            .get_burn_block_by_ch(&conflict.consensus_hash)
        {
            match stacks_client.get_sortition_by_burn_hash(&burn_block.block_hash) {
                Ok(_) => {
                    // The tenure's sortition is still canonical: the conflict is live at the
                    // burn chain level, so fall through to the block-level questions.
                }
                Err(ClientError::RequestFailure(reqwest::StatusCode::NOT_FOUND)) => {
                    // A 404 only proves the sortition was orphaned if the node's burnchain
                    // view actually covers the burn block's height: a node still catching up
                    // 404s canonical burn blocks it hasn't processed yet (and the
                    // endpoint also 404s on internal data misses). Only trust it once the
                    // node's burnchain tip is at or past the stored burn block.
                    match stacks_client.get_peer_info() {
                        Ok(peer_info) if peer_info.burn_block_height >= burn_block.block_height => {
                            info!("{self}: A conflicting block's tenure was orphaned by a burnchain fork. The conflict no longer blocks.";
                                "conflicting_consensus_hash" => %conflict.consensus_hash,
                                "conflicting_block_height" => conflict.stacks_height,
                                "burn_block_hash" => %burn_block.block_hash,
                            );
                            return false;
                        }
                        Ok(peer_info) => {
                            info!("{self}: The node does not know a conflicting block's burn block, but its burnchain tip has not reached that height, so this does not prove the tenure was orphaned. Leaving the conflict in place.";
                                "conflicting_consensus_hash" => %conflict.consensus_hash,
                                "burn_block_hash" => %burn_block.block_hash,
                                "burn_block_height" => burn_block.block_height,
                                "node_burn_block_height" => peer_info.burn_block_height,
                            );
                            return true;
                        }
                        Err(e) => {
                            warn!("{self}: Failed to fetch the node's burnchain tip while checking a conflicting block's tenure: {e:?}. Leaving the conflict in place.";
                                "conflicting_consensus_hash" => %conflict.consensus_hash,
                            );
                            return true;
                        }
                    }
                }
                Err(e) => {
                    warn!("{self}: Failed to check whether a conflicting block's tenure is still canonical: {e:?}. Leaving the conflict in place.";
                        "conflicting_consensus_hash" => %conflict.consensus_hash,
                    );
                    return true;
                }
            }
        }
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
        node_reaches_conflict
            || (!conflict.globally_accepted && conflict.stacks_height <= proposed_height)
    }
```

**File:** stacks-signer/src/v0/signer.rs (L1345-1366)
```rust
        if let Some(block_rejection) =
            self.check_block_against_signer_db_state(stacks_client, &block_info.block)
        {
            warn!(
                "{self}: Reached the pre-commit threshold for a block, but it no longer passes the chainstate checks. Rejecting.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "reject_code" => %block_rejection.reason_code,
                "reject_reason" => &block_rejection.reason,
            );
            if let Err(e) = block_info.mark_locally_rejected() {
                if !block_info.has_reached_consensus() {
                    warn!("{self}: Failed to mark block as locally rejected: {e:?}");
                }
            };
            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
            self.handle_block_rejection(&block_rejection, sortition_state);
            self.send_block_response(&block_info.block, block_rejection.into());
            return;
        }
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
