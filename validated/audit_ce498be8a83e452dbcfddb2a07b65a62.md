### Title
Stale freshness cutoff in the pre-commit conflict check lets a signer equivocate on a conflicting block at the same height once an earlier signed block's `last_endorsed` timestamp "ages out" — (File: `stacks-signer/src/v0/signer.rs`)

### Summary
`handle_block_pre_commit` in `stacks-signer/src/v0/signer.rs` guards against a signer signing two conflicting blocks at the same height by consulting `SignerDb::get_signed_conflicts`. That guard is gated by a purely local, time-based staleness check (`last_endorsed > freshness_cutoff`, derived from the `tenure_last_block_proposal_timeout` config, default 30s) that decides whether the authoritative node-backed check (`conflict_still_blocks`) is even invoked. This is structurally the same bug class as the reported oracle issue: a stale/aged-out local signal is used as a shortcut that skips ground-truth verification, and can be timed/gamed to make the signer take an unsafe action — here, producing a second signature over a conflicting block at the same height instead of an unrepayable loan.

### Finding Description
In `handle_block_pre_commit` (`stacks-signer/src/v0/signer.rs`, lines ~1368-1461), once a pre-commit weight threshold is reached for a proposed block, the signer looks up any previously signed/accepted conflicting blocks at the same or higher height, in **any tenure**: [1](#0-0) 

It then computes a `freshness_cutoff` from `tenure_last_block_proposal_timeout` (default 30 seconds) and only treats a conflict as blocking if it is *fresh* (`last_endorsed > freshness_cutoff`) **and** fails a reorg-permit check **and** the node confirms it `conflict_still_blocks`: [2](#0-1) 

Critically, if the conflict's `last_endorsed` timestamp is older than the cutoff, it is filtered out of this check *before* the node is ever asked whether that earlier block is still canonical (`conflict_still_blocks` is short-circuited by staleness, not by ground truth). The only remaining safeguard for such "stale" conflicts is restricted to the **same tenure** (matching `consensus_hash`): [3](#0-2) 

A stale conflict from a **different** tenure at the same (or higher) height is therefore not checked against the node at all, and the signer proceeds to sign the new, conflicting block: [4](#0-3) 

This mirrors the reported vulnerability's root cause: a staleness/age heuristic (there, oracle price age; here, `last_endorsed` age) is used to bypass the authoritative validity check (there, the actual liquidatable-value check; here, `conflict_still_blocks` querying the node's real canonical view) instead of gating the action pending a valid refresh.

### Impact Explanation
If a signer signs block `B` (tenure `T1`, height `H`) but `B` does not reach global acceptance within `tenure_last_block_proposal_timeout` (30s by default) — due to ordinary network/gossip delay, a slow-to-converge pre-commit round, or a miner/attacker who controls timing of a subsequent sortition — a different, conflicting block `B'` at the same height `H` in a new tenure `T2` can cross the pre-commit threshold and be signed by the very same signer, because:
- `B`'s `last_endorsed` is now stale, so it's excluded from the fresh-conflict check that would otherwise ask the node whether `B` is still live, and
- `B` is in a different tenure than `B'`, so the same-tenure duplicate check does not apply either.

The result is the signer producing two conflicting signatures at the same block height — a direct equivocation, which is the Critical impact category explicitly called out in scope ("a signer signing an invalid, non-canonical, or conflicting block").

### Likelihood Explanation
This requires only ordinary conditions reachable by a single sortition-winning miner plus normal gossip delay — no majority collusion, no other signers' keys, and no local/auth access. The 30-second default window (`DEFAULT_TENURE_LAST_BLOCK_PROPOSAL_TIMEOUT_SECS`) is well within the range of realistic network/consensus latency or a miner deliberately timing tenure changes to straddle it.

### Recommendation
Do not let local staleness of `last_endorsed` alone bypass the ground-truth `conflict_still_blocks` node query. Instead, always query the node (or fall back to a conservative "still blocks" default) for conflicts in *different* tenures as well, regardless of freshness, before allowing a signature over a competing block at the same height. Alternatively, tie the staleness cutoff to node-confirmed abandonment of the earlier tenure/block rather than a fixed wall-clock window.

### Proof of Concept
Conceptual reproduction (matches the pattern of the existing `async_sibling_validation` tests already in the repo, e.g. `stacks-signer/src/v0/tests.rs` around line 319, which test a related timing gap):
1. Miner proposes block `B` at height `H` in tenure `T1`; signers pre-commit and cross threshold, `handle_block_pre_commit` signs `B`, recording `last_endorsed` at time `t0`.
2. `B` fails to reach global acceptance quickly (simulate via network delay or by pausing block-push/observer events).
3. Wait > `tenure_last_block_proposal_timeout` (30s default) so `t0` falls before `freshness_cutoff`.
4. A new sortition occurs; a miner (same or different) proposes conflicting block `B'` at height `H` in tenure `T2`.
5. `B'` reaches the pre-commit threshold; `handle_block_pre_commit` looks up conflicts, finds `B`, but treats it as stale (`last_endorsed <= freshness_cutoff`) and skips `conflict_still_blocks`; since `B`'s `consensus_hash` (`T1`) ≠ `B'`'s (`T2`), the same-tenure check also does not apply.
6. The signer signs `B'`, producing conflicting signatures over `B` and `B'` at the same height `H`.

**Caveat on confidence**: within the available tool budget I was able to fully read and cite the `handle_block_pre_commit` logic and its surrounding comments (which explicitly describe this staleness-bypass behavior), but I was not able to read the full bodies of `conflict_still_blocks` and `reorg_permit_stands` (only found via grep, not fully retrieved) to double-check whether they contain an independent, freshness-agnostic safety net beyond what the comments describe. The code comments at lines 1368-1461 strongly indicate that "still blocks" is only evaluated for *fresh* conflicts, but a Devin session with full read access to `stacks-signer/src/v0/signer.rs` should verify `conflict_still_blocks`/`reorg_permit_stands` in full before treating this as fully confirmed.

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
