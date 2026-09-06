### Title
Predictable staleness cutoff on `get_signed_conflicts()` lets a miner front-run the cross-tenure double-sign guard and obtain a second signer signature on a conflicting sibling block - ([File: stacks-signer/src/v0/signer.rs])

### Summary
Analogous to the reference bug (a suddenly-triggered, unpredictable-but-actually-deterministic loss/bonus event in `unlockDeposit()` that an attacker front-runs by timing a withdrawal), the signer's pre-commit-threshold signing path in `handle_block_pre_commit` gates its double-sign protection behind a purely local, deterministic wall-clock cutoff derived from `tenure_last_block_proposal_timeout`. Once a previously-signed conflicting block's `last_endorsed` timestamp crosses that cutoff, the *only* remaining re-verification against the node is scoped to conflicts in the **same tenure** as the new proposal; a stale conflict in a **different tenure** is unconditionally treated as dead and never re-queried via `conflict_still_blocks`. Because the cutoff is a fixed, publicly-known config value and is measured from a timestamp the signer itself stamps and broadcasts (via pre-commit/acceptance messages), a miner can time a second, competing tenure's proposal at the same height to land exactly after that window elapses, causing the signer to sign a second, conflicting block without ever asking the node whether the first one is still alive.

### Finding Description
In `stacks-signer/src/v0/signer.rs`, the pre-commit-threshold signing logic (`handle_block_pre_commit`) queries `SignerDb::get_signed_conflicts(chain_length, hash)` for any previously signed/accepted block at the **same or higher height, in any tenure**: [1](#0-0) 

It then computes a `freshness_cutoff` from `tenure_last_block_proposal_timeout` and only applies the strong, node-verified `conflict_still_blocks` check to conflicts whose `last_endorsed` timestamp is *still fresh* (`last_endorsed > freshness_cutoff`): [2](#0-1) 

If a conflict fails that freshness test (i.e. is "stale"), the code falls through to a second check that only re-queries the node for conflicts **in the same tenure as the new proposal** (`conflict.consensus_hash == block_info.block.header.consensus_hash`): [3](#0-2) 

Any stale conflict belonging to a *different* tenure never reaches `conflict_still_blocks` at all and is logged and treated as no longer blocking, after which the signer proceeds straight to `mark_locally_accepted` / signing the new block: [4](#0-3) 

The design intent is explicitly documented as a liveness trade-off ("a stale conflict in another tenure in particular no longer speaks for us"), but the trigger for it — elapsing `tenure_last_block_proposal_timeout` since the signer's own `last_endorsed`/`approved_time` — is a deterministic, single-actor-observable clock event, not a majority-only or consensus-gated one: [5](#0-4) 

`BlockInfo::mark_locally_accepted`/`mark_pre_committed` stamp `approved_time`/`signed_self` the instant the signer commits (docs section 2), and `tenure_last_block_proposal_timeout` is a static config value the miner also knows: [6](#0-5) 

This produces the front-running window: a miner (a single, one-slot actor, no majority or other signer's key required) that gets tenure-start block A signed at height h in tenure X, then withholds/delays getting A pushed to the node (broadcast of the aggregated signature set to the node is done by whichever signer crosses the 70% threshold; a slow/partitioned/uncooperative network path, or simply racing the natural delay before `broadcast_signed_block` succeeds, keeps A's node-side confirmation pending), can wait exactly `tenure_last_block_proposal_timeout` past A's `last_endorsed` time and then propose a *second* tenure's tenure-start block B at the same height h. Because A's conflict record is now "stale" and belongs to a different tenure than B, the cross-tenure liveness backstop `conflict_still_blocks` is never consulted for it — the code signs B outright. If A subsequently does reach the node (e.g., the delay was only network jitter, not a real orphaning event), the chain now has two independently signed, height-h blocks from two different tenures, i.e. the exact double-sign/equivocation this guard exists to prevent, achieved purely by timing the proposal against a public, deterministic clock — the direct analog of front-running `unlockDeposit()`'s deterministic-but-unpredictable-to-victims loss distribution.

### Impact Explanation
This breaks the "no double signature at the same height" safety invariant the pre-commit/conflict-guard machinery is built to enforce — the strongest reachable equality in scope: a signer producing a signature over a conflicting/non-canonical block. Per the severity rubric this maps to **Critical** ("a signer signing an invalid, non-canonical, or conflicting block"): the signer ends up having signed two blocks at the same chain height in two different tenures, one of which cannot be canonical.

### Likelihood Explanation
**Medium**: no majority of signers, no other signer's key, and no auth token are needed — a single miner controls both proposals and only needs to control the timing of the second proposal relative to a fixed, known config value (`tenure_last_block_proposal_timeout`) and to arrange (via ordinary network delay, not necessarily malicious network control) that the first signed block hasn't yet reached the node's canonical tenure tip when the second is proposed. The exact same race is already acknowledged and partially tested for the *same-tenure* case (`stale_sibling_replaced_when_canonical_tip_below`), showing the timing window is realistic and already reachable in practice; the cross-tenure gap is simply left unguarded by design.

### Recommendation
Do not let a conflict's tenure scope determine whether `conflict_still_blocks` is consulted. Either: (a) always re-verify staleness against the node via `conflict_still_blocks`/`get_tenure_tip` for *all* signed conflicts at or above the proposed height regardless of tenure, only skipping the node round-trip once the conflict is provably dead (e.g. its sortition was orphaned), or (b) extend the same-tenure-only check at lines 1432–1457 to iterate over every stale, same-or-higher-height conflict across all tenures before allowing the sign to proceed, mirroring the fresh-path logic instead of special-casing "same tenure."

### Proof of Concept
1. Miner proposes tenure-start block A (tenure X, height h). Signers pre-commit and reach the 70% threshold; this signer calls `mark_locally_accepted` on A, stamping `signed_self`/`last_endorsed` at time `t0` [7](#0-6) . Broadcasting A's aggregated signatures to the node is delayed (network jitter/partial partition), so the node's canonical tenure tip for X stays below height h.
2. Miner waits until `t0 + tenure_last_block_proposal_timeout` (a value it can read from `sample/conf/signer/mainnet-signer-conf.toml`/config docs) has elapsed, then proposes a second tenure-start block B in a different tenure Y at the same height h, built on a different parent.
3. This signer's `handle_block_pre_commit` for B calls `get_signed_conflicts(h, hash_B)`, finds A as a conflict, but `A.last_endorsed <= freshness_cutoff` now, so the fresh-check `find()` at lines 1403–1421 skips A entirely.
4. The second check at lines 1432–1457 only inspects conflicts where `conflict.consensus_hash == B.consensus_hash` (tenure Y) — A (tenure X) never matches, so it is not re-queried against the node at all.
5. The signer proceeds to `mark_locally_accepted(false)` and signs B (lines 1467–1478), even though A may still reach the node and become canonical once the earlier network delay clears — producing two independently signed blocks at height h in different tenures. [8](#0-7)

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1383-1478)
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
        }
        self.signer_db
            .insert_block(&block_info)
            .unwrap_or_else(|e| self.handle_insert_block_error(e));
        let accepted = self.create_block_acceptance(&block_info.block);
        // have to save the signature _after_ the block info
        self.handle_block_signature(stacks_client, sortition_state, &accepted);
        self.send_block_response(&block_info.block, accepted.into());
```

**File:** docs/signer-flows.md (L274-297)
```markdown
Order matters here: the chainstate re-check runs first and produces an explicit
(sticky) rejection when the block now conflicts with a signed one. The conflict
guard behind it is the silent backstop for what that re-check cannot see, and
silence keeps the door open to sign later once the conflict goes stale. Two
blind spots make the guard necessary:

- the re-check only ever looks at _one_ tenure (a tenure-change block's parent,
  or any other block's own), so a signed sibling at the same height in a third
  tenure is invisible to it;
- the `DuplicateBlockFound` check that would catch a second block in the same
  tenure lives in `check_proposal` and runs only at proposal arrival, never
  again. A block that crosses the pre-commit threshold minutes later has no
  other guard, which is what the own-tenure branch above covers.

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

**File:** stacks-signer/src/signerdb.rs (L272-289)
```rust
    /// Mark this block as valid, record the approved time timestamp if not already set and attempt to mark it as pre-committed.
    pub fn mark_pre_committed(&mut self) -> Result<(), String> {
        self.valid = Some(true);
        self.approved_time.get_or_insert(get_epoch_time_secs());
        self.move_to(BlockState::PreCommitted)
    }

    /// Mark this block as valid and the appropriate timestamps if they aren't already set, and attempt to mark it as locally accepted.
    pub fn mark_locally_accepted(&mut self, group_signed: bool) -> Result<(), String> {
        if group_signed {
            self.signed_group.get_or_insert(get_epoch_time_secs());
        } else {
            self.valid = Some(true);
            self.approved_time.get_or_insert(get_epoch_time_secs());
            self.signed_self.get_or_insert(get_epoch_time_secs());
        }
        self.move_to(BlockState::LocallyAccepted)
    }
```
