### Title
Signer bypasses the same-tenure equivocation guard using an unrelated cross-tenure reorg permit, allowing it to sign a conflicting sibling block - (File: `stacks-signer/src/v0/signer.rs`)

### Summary
`handle_block_pre_commit`'s own-tenure conflict check (the guard that stops a signer from signing two competing blocks at the same height in the same tenure) exempts any conflict for which `reorg_permit_stands` returns true. But a reorg permit is granted per *tenure* (via `mark_tenure_superseded`/`get_signed_conflicts`) to let an **external, replacing tenure** bypass a stale foreign conflict — it says nothing about whether a specific sibling *inside* the permitted tenure has already become canonical. Reusing it here lets the signer sign a second, conflicting sibling in its own tenure even when the node's canonical tip for that tenure is already at or above the sibling's height.

### Finding Description
The invariant that should hold is: *a signer never signs two conflicting blocks at the same height in the same tenure once the tenure's canonical tip already covers that height.* This is enforced in `handle_block_pre_commit` by the "own tenure" branch: [1](#0-0) 

```
if conflicts.iter().any(|conflict| {
    conflict.consensus_hash == block_info.block.header.consensus_hash
        && !self.reorg_permit_stands(stacks_client, conflict)
}) {
    match stacks_client.get_tenure_tip(&block_info.block.header.consensus_hash) { ... }
}
```
When the guard's single `conflict` (a prior sibling `A`, signed locally or already globally accepted, same `consensus_hash`/tenure as the new proposal `B`) has a standing `superseded_by` record, `!reorg_permit_stands(...)` is `false`, so `.any()` returns `false` and the `get_tenure_tip` check is skipped entirely — the code falls straight through to `mark_locally_accepted` and signs `B`, without ever asking whether the node's canonical tip for that tenure is already at/above `B`'s height.

`reorg_permit_stands` and the `superseded_by` annotation on `SignedConflictInfo` are populated by `check_parent_tenure_choice`/`mark_tenure_superseded`, which only validate that a tenure had at most one globally accepted block, poorly timed, before being legitimately replaced by a *different, later* tenure: [2](#0-1) [3](#0-2) 

The permit record is keyed purely by `consensus_hash` (the whole superseded tenure), not by the specific block that was checked: [4](#0-3) [5](#0-4) 

Exploit flow (attacker needs only the ability to win one sortition and gossip conflicting `BlockProposal`s):
1. Attacker wins tenure `T1`'s sortition and crafts two conflicting tenure-start blocks `A` and `B` at the same height, gossiping each to a different subset of signers (the classic 50/50 split scenario the codebase already anticipates — see `signer_refuses_to_sign_second_sibling_tenure_start`). Neither reaches the 70% global threshold; `T1` ends with 0 or 1 globally-accepted block.
2. The next miner (anyone, no collusion needed) legitimately wins the following sortition for `T2` and proposes a tenure-change block. Because `T1` has ≤1 globally accepted block and/or was poorly timed, `check_parent_tenure_choice` legitimately grants a reorg permit: `T1` → `superseded_by T2`. This is a normal, intended reorg-recovery path.
3. A signer who earlier locally accepted/pre-committed `A` in `T1` now receives `B`'s pre-commits crossing threshold (via re-gossip/re-proposal). `get_signed_conflicts` returns `A` as a same-tenure conflict, now carrying `superseded_by = T2`. Since `T2`'s sortition is still canonical, `reorg_permit_stands` returns `true`, so the own-tenure `.any()` check is bypassed — the `get_tenure_tip(T1)` check that would otherwise catch "T1's tip is already at/above B's height" (as in `stale_sibling_still_refused_when_canonical_tip_at_height`) never runs, and the signer signs `B`.
4. If `A` had already reached global acceptance (permitted even with exactly 1 globally-accepted block, per the `> 1` check), the node's canonical tip for `T1` is already at `A`'s height, yet the signer signs the conflicting `B` anyway — a direct, provable equivocation/non-canonical signature.

Existing guards do not stop this: `check_block_against_signer_db_state` (the chainstate recheck earlier in the same function) only rejects if `A`'s signature is still *fresh* (`get_tenure_last_block_info`); once stale, it does not fire, and the case falls into the buggy own-tenure branch which — unlike the "no permit" path — never re-derives the tenure's real tip.

### Impact Explanation
This breaks chain safety (validity/uniqueness/canonicity): the same signer produces valid signatures over two conflicting/non-canonical siblings at the same height in the same tenure, including cases where one of them is already the node's canonical, globally-accepted block. If several signers hit the same permit-driven bypass (plausible, since all signers observe the same permit and same node state), both siblings can independently accumulate the 70% signature threshold, finalizing two competing blocks — the exact chain-fork condition the entire pre-commit/conflict-guard system exists to prevent. This matches the "Critical: a signer signing an invalid, non-canonical, or conflicting block" category. The bug is repeatable any time a tenure the signer split votes on is legitimately superseded by a later tenure (a routine, expected event, not an edge case).

### Likelihood Explanation
Preconditions are cheap and require no privileged role: an attacker who can win one sortition slot can trivially craft two conflicting `BlockProposal`s for their own tenure and gossip them (splitting the signer set), which is exactly the network condition the reorg-recovery mechanism exists to handle. The subsequent superseding tenure does not need to be attacker-controlled — any honest miner naturally reorging a stalled/split tenure triggers `mark_tenure_superseded`. From that point, the vulnerable code path fires automatically inside `handle_block_pre_commit` on ordinary re-proposal/re-pre-commit of the losing sibling, with no additional attacker action needed. This is fully repeatable and does not require majority signer control, node access, or the auth token.

### Recommendation
Remove the `reorg_permit_stands` exemption from the own-tenure ("same consensus_hash") branch in `handle_block_pre_commit`. A reorg permit should only ever suppress *cross-tenure* conflicts (where `conflict.consensus_hash != block_info.block.header.consensus_hash`), never same-tenure sibling conflicts. The `get_tenure_tip` canonical-tip check must always run for same-tenure conflicts, permit or not, since it answers a materially different question (has this specific sibling already become canonical) than the permit (is some other tenure allowed to replace this whole one).

### Proof of Concept
Rust test plan (in `stacks-signer/src/v0/tests.rs`, extending the existing `run_sibling_scenario`/permit-test harness):
1. Reuse `run_sibling_scenario`-style setup to create tenure `T1` with two conflicting tenure-start siblings `A` and `B` at height 10, and have the mock node serve `A` as `T1`'s canonical tenure tip (`serve_sibling_as_tip`-equivalent for `A`, i.e. `get_tenure_tip(T1) == A`'s height).
2. Sign `A` locally (`process_event` with `validate_ok(&hash_a)`), and separately mark it (or a peer's copy) as globally accepted via `handle_block_signature`/`add_block_signature`, so `A` is recorded with `signed_group` set and the node's mocked `/v3/tenures/tip_metadata/{T1}` returns `A` as the tip.
3. Call `signer_db.mark_tenure_superseded(&T1, height, &T2_consensus_hash, &T2_burn_hash)` directly to simulate a legitimate permit granted by a later tenure `T2`, and mock `/v3/sortitions/burn/{T2_burn_hash}` to return `200 Ok` (canonical), so `reorg_permit_stands` returns `true`.
4. Let `A`'s freshness window expire (or use `Duration::ZERO` for `tenure_last_block_proposal_timeout`) so the chainstate recheck (`check_block_against_signer_db_state`) does not short-circuit before the conflict-guard code.
5. Drive `B` through `validate_ok(&hash_b)`/pre-commit threshold via `process_event`.
6. Assert the bug: `info_b.state == BlockState::LocallyAccepted` and `info_b.signed_self.is_some()`, i.e. the signer signed `B` even though `get_tenure_tip(T1)` (mocked to return `A`'s height) is at/above `B`'s height. Assert this contradicts the control test `stale_sibling_still_refused_when_canonical_tip_at_height`, which (without the permit) correctly leaves `info_b.state == BlockState::PreCommitted` and `info_b.signed_self.is_none()` under the identical tip configuration — the only difference being the presence of the `mark_tenure_superseded` permit for `T1`.

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

**File:** stacks-signer/src/chainstate/mod.rs (L210-223)
```rust
            // disallow reorg if more than one block has already been signed
            let globally_accepted_blocks =
                signer_db.get_globally_accepted_block_count_in_tenure(&tenure.consensus_hash)?;
            if globally_accepted_blocks > 1 {
                warn!(
                    "Miner is not building off of most recent tenure, but a tenure they attempted to reorg has already more than one globally accepted block.";
                    "parent_tenure" => %self.parent_tenure_id,
                    "last_sortition" => %self.prior_sortition,
                    "violating_tenure_id" => %tenure.consensus_hash,
                    "violating_tenure_first_block_id" => ?tenure.first_block_mined,
                    "globally_accepted_blocks" => globally_accepted_blocks,
                );
                return Ok(false);
            }
```

**File:** stacks-signer/src/chainstate/mod.rs (L290-315)
```rust
        // Every reorged tenure cleared the rules, so the reorg is permitted.
        for tenure in superseded_tenures {
            self.record_superseded_tenure(signer_db, tenure);
        }
        Ok(true)
    }

    /// Note that we have sanctioned `self`'s tenure replacing whatever `tenure` built, so a
    /// signature we already placed on one of its blocks must stop counting as a conflict while
    /// `self`'s sortition remains canonical.
    ///
    /// A failure to record only costs a delayed replacement -- the conflict keeps blocking until
    /// the signature goes stale -- so it is logged rather than propagated.
    fn record_superseded_tenure(&self, signer_db: &mut SignerDb, tenure: &TenureForkingInfo) {
        if let Err(e) = signer_db.mark_tenure_superseded(
            &tenure.consensus_hash,
            tenure.burn_block_height,
            &self.consensus_hash,
            &self.burn_block_hash,
        ) {
            warn!("Failed to record a tenure whose reorg we permitted: {e}";
                "superseded_tenure_id" => %tenure.consensus_hash,
                "superseded_by" => %self.consensus_hash,
            );
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
