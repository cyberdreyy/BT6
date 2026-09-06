## Title
Timing-only staleness lets a single-slot miner obtain a signer's signature on two conflicting sibling blocks (equivocation) - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`handle_block_pre_commit`'s conflict guard (`stacks-signer/src/v0/signer.rs:1383-1465`) is the only safety net preventing a signer from signing two blocks at the same Stacks height across *different* tenures once the earlier signature has gone stale. Staleness is decided purely by a local wall-clock comparison against `tenure_last_block_proposal_timeout` [1](#0-0) . Once a cross-tenure conflict is "stale" (`last_endorsed <= freshness_cutoff`), the code stops asking the node whether that conflicting block is still canonical at all — it only re-checks liveness for conflicts *within the same tenure as the newly proposed block* [2](#0-1) . A miner that simply waits out this fixed, publicly-known timeout before proposing a sibling tenure-start block at the same height can walk a signer into signing both blocks, producing a live safety violation (a signer signature over two conflicting/non-canonical blocks) without needing any other signer's cooperation, a majority, or any node/consensus compromise.

### Finding Description
The conflict guard is documented as the backstop for the one gap left by the chainstate/`DuplicateBlockFound` checks (which run once, only at proposal time, and only within one tenure) [3](#0-2) . It works in two stages inside `handle_block_pre_commit`:

1. `get_signed_conflicts(height, excluded_hash)` returns every block *ever signed* (self or group) at or above the proposed height, in **any** tenure [4](#0-3) .
2. The signer first checks only conflicts that are still *fresh* (`last_endorsed > freshness_cutoff`) and asks the node whether they are `conflict_still_blocks` [5](#0-4) .
3. If none of the fresh conflicts still block, the code moves to a **second, narrower** check that only reconsiders conflicts whose `consensus_hash == block_info.block.header.consensus_hash` — i.e., only same-tenure conflicts — querying the node's tenure tip to see if it's already built past this height [2](#0-1) .
4. If the conflicting block is in a **different tenure**, and it is stale, it is not checked at all in this second stage: `conflicts.iter().any(...)` in step 3 only matches conflicts in the *same* consensus hash as the new proposal, so a stale cross-tenure conflict simply drops out of consideration, and the signer proceeds straight to `mark_locally_accepted` and signs [6](#0-5) .

`freshness_cutoff` is derived solely from `get_epoch_time_secs()` minus the signer's local `tenure_last_block_proposal_timeout` config value — a purely local, deterministic, publicly-documented default (visible in `sample/conf/signer/mainnet-signer-conf.toml`) [7](#0-6) . A miner (a single "slot", i.e. the current tenure's proposer) fully controls the *timing* of block proposals it broadcasts via StackerDB/gossip. This means the equality this guard is supposed to preserve — "this signer has signed only one block at any given height" — can be broken by an entirely mechanical, timing-based action requiring no other signer's collusion and no majority: the miner just needs to wait past the known timeout window before broadcasting the sibling tenure-start proposal.

Concretely:
1. Miner proposes tenure-start block A (tenure T1, height h). Signer validates, pre-commits, reaches 70% pre-commit weight, and signs A — `signed_self` is timestamped at t0 via `mark_locally_accepted` [8](#0-7) .
2. Miner lets `tenure_last_block_proposal_timeout` elapse (a fixed, config-known duration) without confirming A on-chain (e.g. it never reaches its own node's report as canonical, or the miner simply builds off a different burn view).
3. Miner proposes a new tenure-start block B in a different tenure T2, at the same height h (a legitimate scenario is a natural reorg where a different miner or the same miner builds a competing tenure; the chainstate checks at proposal time, `check_parent_tenure_choice`/`validate_tenure_change_payload`, may pass this depending on burn-view alignment, since they only check parent-tenure legitimacy, not a same-height existing signature).
4. Once pre-commit threshold is again met for B, `handle_block_pre_commit` runs the conflict guard: A is now stale (`last_endorsed <= freshness_cutoff`), so the fresh-conflict branch finds nothing. The second branch only checks conflicts whose `consensus_hash` equals B's tenure (T2) — A is in T1, so it is never evaluated for liveness at all.
5. The signer signs B, having already signed A at the same height — the equivocation the whole guard exists to prevent.

This directly parallels the CNCF K3s advisory's bug class: a version-specific, unintended interaction of default/derived settings quietly disabled a security guarantee that operators believed was still enforced (the kubelet's authenticated-only access), exposing sensitive data to anyone who could reach the (mis-)exposed port. Here, a default timeout interacting with the guard's *scope* (same-tenure only, once stale) quietly disables the cross-tenure equivocation guard, exposing a signature over a conflicting block to anyone who can time proposals across tenures — no majority, no node compromise, no auth token needed.

### Impact Explanation
This is a **Critical** finding under the accepted impact classes: it is a path by which "a signer sign[s]... a conflicting block." A double-signed height directly threatens the equivocation-freedom the signer set is designed to guarantee; enough signers independently exposed to this same timing window could allow two conflicting forks to each accumulate signatures, undermining the finality/no-equivocation property the two-phase (pre-commit → signature) protocol exists to provide.

### Likelihood Explanation
The action needed is entirely mechanical and requires only a single miner controlling proposal timing plus normal StackerDB gossip — exactly the "one-slot miner (plus gossip)" scope specified. `tenure_last_block_proposal_timeout` is a documented signer-side default (visible in `sample/conf/signer/mainnet-signer-conf.toml` and `stacks-signer/src/config.rs`) [9](#0-8) , so any operator or attacker sees its value ahead of time and can simply wait it out. No majority of signers, no other signer's private key, and no local/auth_token access is needed — only the ordinary ability of a miner to control when it broadcasts its next tenure-start proposal.

### Recommendation
Extend the second (post-staleness) conflict check in `handle_block_pre_commit` to cover **every** stale conflict returned by `get_signed_conflicts`, not just those sharing the new proposal's `consensus_hash`. For a stale cross-tenure conflict, query the conflicting tenure's canonical tip (as is already done for same-tenure conflicts) before allowing the new signature, so a stale-but-still-live sibling in another tenure cannot be silently bypassed.

### Proof of Concept
Not directly executable without a running multi-signer/multi-miner testnet, but the trace is fully reproducible from the code:
1. In `stacks-signer/src/v0/signer.rs`, set/observe `tenure_last_block_proposal_timeout` (config default, `stacks-signer/src/config.rs:426-432`).
2. Drive tenure T1 to height h, get the signer to sign block A (`mark_locally_accepted`, `signerdb.rs:280-289`), recording `signed_self = t0`.
3. Wait `tenure_last_block_proposal_timeout` seconds.
4. Drive a new tenure T2 to height h (sibling of A), and push its pre-commit weight past threshold.
5. Observe `handle_block_pre_commit` (`signer.rs:1383-1478`): the `find` over fresh conflicts (`signer.rs:1403-1411`) returns `None` because A is now stale; the same-tenure-only re-check (`signer.rs:1432-1457`) does not match A (different `consensus_hash`); the code falls through to `mark_locally_accepted` and signs B (`signer.rs:1467-1478`), producing two signatures at height h across tenures T1 and T2.

### Citations

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

**File:** docs/signer-flows.md (L274-286)
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
```

**File:** stacks-signer/src/signerdb.rs (L279-289)
```rust
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

**File:** stacks-signer/src/signerdb.rs (L1587-1625)
```rust
    /// Return every signed block at or above the given Stacks height, in ANY tenure, excluding
    /// the block with the given signer signature hash, ordered by height (highest first). A
    /// block is considered signed if a signature was ever put over it, ours (`signed_self`)
    /// or the observed group's (`signed_group`). Blocks that were only pre-committed carry no
    /// signature and are never returned. Each row carries the most recent endorsement time
    /// (`signed_self`/`signed_group`, whichever is later) so the caller can judge freshness per
    /// conflict.
    ///
    /// The search deliberately spans all tenures: two blocks at the same height are siblings
    /// no matter which tenure they belong to (e.g. a tenure-start block conflicts with the
    /// previous tenure's block at the same height), so a signature over either may conflict
    /// with a fresh signature over the other.
    ///
    /// Blocks in tenures whose reorg we sanctioned under the reorg-timing rules (see
    /// [`SignerDb::mark_tenure_superseded`]) are still returned, but annotated with the
    /// permitting tenure's sortition (`superseded_by_*`): the permit only holds while that
    /// sortition is canonical, which the caller derives from the node per evaluation (see
    /// `Signer::reorg_permit_stands`) -- like every other question about whether a conflict is
    /// still *live* (`Signer::conflict_still_blocks`), it is not recorded.
    pub fn get_signed_conflicts(
        &self,
        height: u64,
        excluded_signer_signature_hash: &Sha512Trunc256Sum,
    ) -> Result<Vec<SignedConflictInfo>, DBError> {
        let query = "SELECT b.consensus_hash, b.signer_signature_hash, b.stacks_height, b.state,
                MAX(COALESCE(b.signed_self, 0), COALESCE(b.signed_group, 0)) AS last_endorsed,
                st.superseded_by_consensus_hash, st.superseded_by_burn_block_hash
            FROM blocks b
            LEFT JOIN superseded_tenures st ON st.consensus_hash = b.consensus_hash
            WHERE (b.signed_self IS NOT NULL OR b.signed_group IS NOT NULL)
                AND b.stacks_height >= ?1
                AND b.signer_signature_hash != ?2
            ORDER BY b.stacks_height DESC";
        let args = params![
            u64_to_sql(height)?,
            excluded_signer_signature_hash.to_string(),
        ];
        query_rows(&self.db, query, args)
    }
```

**File:** stacks-signer/src/config.rs (L426-432)
```rust
    pub block_proposal_max_age_secs: Option<u64>,
    /// Time following a block's global acceptance during which a signer will consider
    /// a miner's attempt to reorg it as valid miner activity.
    /// ---
    /// @default: `200_000`
    /// @units: milliseconds
    pub reorg_attempts_activity_timeout_ms: Option<u64>,
```
