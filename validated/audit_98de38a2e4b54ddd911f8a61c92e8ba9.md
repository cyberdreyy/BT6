### Title
Staleness-based conflict clearance in `handle_block_pre_commit` lets a signer sign two conflicting blocks at the same height - ([File: stacks-signer/src/v0/signer.rs])

### Summary
The pre-commit-threshold signing path in `handle_block_pre_commit` clears a "signed conflict" purely by wall-clock staleness (`tenure_last_block_proposal_timeout`) for conflicts outside the block's own tenure, without ever asking the node whether the earlier signed block is actually dead. A single miner that delays a competing proposal until a signer's own `last_endorsed` timestamp for the first block ages past this local cutoff can induce that signer to place a second, conflicting signature at the same height — mirroring the report's "stay under the threshold to escape the check, then combine the pieces" pattern (small transfers below `_minimumTaintedTransferAmount`, here: elapsed time past `tenure_last_block_proposal_timeout`).

### Finding Description
`handle_block_pre_commit` [1](#0-0)  computes `freshness_cutoff = now - tenure_last_block_proposal_timeout` and only runs the expensive, node-backed liveness checks (`reorg_permit_stands` / `conflict_still_blocks`, which ask the node whether the conflicting tenure's sortition and block are still canonical/live) against conflicts whose `last_endorsed > freshness_cutoff`. Any signed conflict that is merely *stale by the local clock* skips those checks entirely.

For a conflict in a *different* tenure than the one being evaluated, once it is stale the code path is:
`FRESH -- "no — all stale" --> OWN` [2](#0-1)  — the `OWN` branch only checks conflicts sharing the *same* `consensus_hash` as the block being signed; a stale conflict in a third tenure falls through with no live-check at all and the code proceeds straight to `mark_locally_accepted` [3](#0-2) .

`last_endorsed` is `MAX(signed_self, signed_group)` per signer, recorded in `get_signed_conflicts` [4](#0-3) . Crucially, once a signer places its own signature over block A, that signature keeps counting toward A's 70% threshold forever — "a signature is a bearer instrument that can still be aggregated toward the 70% threshold" [5](#0-4) . Nothing revokes it when it goes locally "stale"; staleness only changes whether *this signer* still treats it as a veto for *new* signing decisions.

This creates the exploitable gap: if block A's pre-commit round is slow to reach 70% (e.g. one or two straggling signers, achievable by a miner selectively delaying/dropping gossip to a subset of signers — allowed under "plus gossip"), then once `tenure_last_block_proposal_timeout` elapses for a signer that *already signed A*, a subsequently proposed conflicting sibling block B at the same height can cross that same signer's pre-commit threshold and get signed too, with no query to the node about A's fate (because A's tenure ≠ B's tenure, so the `OWN` same-tenure check does not apply, and the fresh-only `SORT`/`LIVE` checks are skipped). The signer now holds two signatures over two mutually exclusive blocks at the same height. If A later receives its remaining needed weight from signers who never saw/signed B, A reaches global acceptance; if B independently gathers 70% from signers whose A-conflict went stale the same way, B also reaches global acceptance — a genuine equivocation/fork, breaking the "one signed block per height" invariant that the whole conflict-guard machinery in section 5 of `docs/signer-flows.md` exists to protect.

The design doc frames the stale-skip as a deliberate stall-recovery mechanism ("a dead signature must not stall the chain restarting beneath it until it goes stale") [6](#0-5) , but that framing assumes staleness implies the old block is actually dead. It does not: staleness is a purely local, per-signer clock condition and is silent about whether A is dead network-wide or merely slow to finish collecting the last few percent of weight — exactly the "small enough to slip under the check, but the underlying prohibited action still completes" gap the source report describes.

### Impact Explanation
This is Critical per the given rubric: "a signer signing an invalid, non-canonical, or conflicting block." A signer can end up with valid signatures over two blocks that conflict at the same height, and because signatures are bearer instruments that never get revoked, both blocks can independently accumulate the 70% threshold and both be pushed to the node, producing a Stacks-chain equivocation/fork enabled entirely by timing rather than by compromising any keys or requiring a signer majority.

### Likelihood Explanation
The trigger condition needs only:
- normal variance in how fast different signers complete a pre-commit round (already exists in a live network, and can be amplified by a single miner delaying/withholding gossip to some signers, which is within the stated "one-slot miner (plus gossip)" scope), and
- `tenure_last_block_proposal_timeout` being reasonably short relative to how long full 70% aggregation can take for a subset of signers.

No majority-controlled keys, no auth token, and no node bug are required — only ordinary asynchrony plus a miner that can time a competing proposal. The smaller `tenure_last_block_proposal_timeout` is configured, the more easily this window opens, directly analogous to the source report's warning that a large `_minimumTaintedTransferAmount` opens the window for the split-transfer bypass.

### Recommendation
Do not let staleness alone clear a cross-tenure signed conflict. Either:
- Always run the node-backed liveness check (`conflict_still_blocks`) for any conflict regardless of freshness before signing a new block at/above its height, only skipping the *veto* semantics (not the *liveness verification*) once local staleness is established; or
- Before signing a stale-but-cross-tenure conflict's sibling, explicitly re-query the node to confirm the earlier block's tenure/sortition is provably dead (the same `get_sortition_by_burn_hash` / `get_tenure_tip` checks already used for fresh conflicts), rather than skipping straight to `mark_locally_accepted`.

### Proof of Concept
1. Miner tenure T1 proposes block A at height H. Signers S1..S4 pre-commit and sign quickly (their `signed_self` timestamp = t0); signer S5 is network-delayed and only signs at t0+ε, so A sits at just under 70% weight for a while.
2. Miner starts tenure T2 (a fork/sibling at the same height H) and withholds proposing B until `now > t0 + tenure_last_block_proposal_timeout` for S1..S4 (their `last_endorsed` for A is now stale) but continues delaying delivery of A's completion to S1..S4.
3. Miner proposes block B (height H, tenure T2) to S1..S4. In `handle_block_pre_commit`, `get_signed_conflicts` returns A as a conflict, but `last_endorsed <= freshness_cutoff`, so the fresh-only liveness check is skipped; the `OWN` check compares `consensus_hash` and does not match (T2 ≠ T1), so S1..S4 proceed to `mark_locally_accepted` on B [7](#0-6) .
4. Miner now finally forwards S5's stale-but-still-valid signature-completion path for A: S5 was never asked to sign B and has no conflict record forcing revocation of A; A can still reach 70% if enough of S1..S4's earlier A-signatures are aggregated (they remain valid bearer signatures per `docs/signer-flows.md`).
5. Result: both A (via S1-S5's earlier signatures) and B (via S1-S4's later signatures) can independently reach the 70% threshold and be pushed — two conflicting globally-accepted blocks at height H.

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

**File:** stacks-signer/src/v0/signer.rs (L1423-1471)
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
```

**File:** stacks-signer/src/signerdb.rs (L1606-1625)
```rust
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

**File:** docs/signer-flows.md (L288-296)
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
