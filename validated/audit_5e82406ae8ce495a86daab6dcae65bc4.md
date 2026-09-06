### Title
Stale cross-tenure conflicts skip `conflict_still_blocks`, letting a signer sign a second block at the same height while the first is still canonical - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`Signer::handle_block_pre_commit` only asks the node whether a conflicting block is actually still alive (`conflict_still_blocks`) while that conflict is *fresh* (`last_endorsed > freshness_cutoff`). Once a conflict in a tenure other than the proposed block's own tenure goes stale, it is dropped from consideration entirely and no node check is ever performed, so the signer can sign a second, conflicting block at the same `stacks_height` even though the first block is still fully canonical.

### Finding Description
The equality the freshness guard is supposed to preserve is: "distinct blocks signed per `stacks_height` == at most one," enforced across *all* tenures via `SignerDb::get_signed_conflicts` [1](#0-0) .

In `Signer::handle_block_pre_commit`, conflicts are first filtered by freshness, and only fresh conflicts are checked against the node via `reorg_permit_stands`/`conflict_still_blocks`: [2](#0-1) 

Once a conflict is stale (`last_endorsed <= freshness_cutoff`), the *only* remaining guard checks conflicts whose `consensus_hash` equals the **proposed block's own tenure**: [3](#0-2) 

A conflict belonging to a *different* tenure than the one being proposed (a genuine third-tenure sibling, e.g. two competing tenure-change blocks from a natural Bitcoin fork both confirming the same parent tenure at the same height) is invisible both to this own-tenure re-check and to the earlier `check_block_against_signer_db_state` chainstate re-check, which the code comment itself documents only ever inspects the parent tenure (for tenure-change blocks) or the block's own tenure — never an unrelated sibling tenure (see comment at lines 1425-1426 and `docs/signer-flows.md` lines 280-282). Once such a conflict goes stale, it falls straight through to the unconditional signing path at the bottom of the function: [4](#0-3) 

`conflict_still_blocks` — the function that actually asks the node whether a conflicting block's tenure/sortition is still canonical and whether the chain still reaches it — is never invoked for a stale, cross-tenure conflict: [5](#0-4) 

**Exploit flow:** An attacker who wins a single miner slot (via a legitimate but adversarially-timed Bitcoin fork producing tenure Y and a competing tenure Z, both confirming the same parent tenure X) can:
1. Get the honest signer to sign block A (tenure Y, height H) at time T (`signed_self = T`).
2. Craft/propose a conflicting tenure-change block B (tenure Z, height H) and time its gossip/pre-commits so the 70% pre-commit threshold is reached at `T + freshness_cutoff + 1`.
3. At evaluation time, `get_signed_conflicts(H, hash_B)` returns A with `last_endorsed = T`, which is now `<= freshness_cutoff` (stale).
4. The fresh-conflict `find` at lines 1403-1411 excludes A (correctly, per its own logic) — but no other check reaches A at all, because A's `consensus_hash` (Y) does not match B's `consensus_hash` (Z), so the own-tenure branch at line 1432 also skips it.
5. The signer signs B, producing two signed, conflicting blocks (A and B) at the same `stacks_height`, even though tenure Y's sortition remains canonical and A is still (or will become) globally accepted — i.e. `conflict_still_blocks` would have returned `true` had it been asked.

This breaks the UNIQUENESS property the whole `get_signed_conflicts` + freshness + liveness design exists to guarantee, purely via timing, with no majority-signer collusion, no compromised key, and no local access — only crafted proposal timing plus normal gossip.

### Impact Explanation
This is a Critical chain-safety violation: a single signer can be induced to place valid signatures over two conflicting blocks at the same height. If enough signers are independently timed this way (each needs only to observe the same staleness window, which is a fixed, publicly-known `tenure_last_block_proposal_timeout`), both A and B could separately accumulate the 70% signature threshold, producing two valid, aggregatable quorum certificates for conflicting blocks at the same height — a genuine equivocation that can be used to justify two different canonical histories to different observers, undermining the chain's safety guarantee.

### Likelihood Explanation
The attacker needs: one Bitcoin sortition win (a normal permissionless step available to "a party who can win a single miner slot with their own BTC"), knowledge of the public `tenure_last_block_proposal_timeout` (a configured constant, not secret), and the ability to control when their proposal's pre-commits reach the 70% threshold (via gossip timing, which unprivileged parties can influence by controlling their own proposal/pre-commit broadcast timing). No majority of signers, no auth_token, no compromised keys, and no local host access are required. This is fully repeatable each time a natural tenure/fork boundary produces a genuine third-tenure sibling situation, which the codebase itself acknowledges as a real, expected scenario (docs/signer-flows.md's discussion of "a signed sibling at the same height in a third tenure").

### Recommendation
Do not let staleness alone clear a cross-tenure conflict. For any conflict whose `consensus_hash` differs from the proposed block's tenure (and is not covered by a standing reorg permit), still invoke `conflict_still_blocks` (or an equivalent node-derived liveness check) regardless of freshness, mirroring the two-question liveness check already used for fresh conflicts and for same-tenure stale conflicts. Only treat a conflict as cleared once the node confirms it is dead (orphaned sortition, or reorged-past and previously globally accepted), not merely because the local timestamp aged out.

### Proof of Concept
Extend `stacks-signer/src/v0/tests.rs`'s existing `run_cross_tenure_scenario`/`TenureAFate` harness (used by `fresh_conflict_in_another_tenure_blocks_signing` and `conflict_whose_sortition_is_canonical_still_blocks_signing`) with a new scenario that pairs a **stale** freshness window with a **still-canonical** tenure:

```rust
#[test]
fn stale_cross_tenure_conflict_wrongly_unblocks_signing_while_still_canonical() {
    // Use a zero/near-zero tenure_last_block_proposal_timeout so A's signature is stale by
    // the time B reaches pre-commit threshold, but report tenure A's sortition as still
    // canonical AND its tenure tip still reaching the conflicting height (i.e.
    // conflict_still_blocks would return true if it were ever asked).
    let (info_a, info_b) = run_cross_tenure_scenario_with_timeout(
        TenureAFate::SortitionStillCanonical,
        Duration::ZERO, // forces staleness
    );

    assert_a_signed(&info_a);

    // EXPECTED (safety property): B must still be refused, because A's tenure remains
    // canonical -- conflict_still_blocks(&conflict_a) would return true.
    assert_b_refused(
        &info_b,
        "A's sortition is still canonical even though the signature aged out",
    );

    // ACTUAL (current code): B gets signed because the stale, cross-tenure conflict is
    // never re-checked against the node.
    // assert_eq!(info_b.state, BlockState::LocallyAccepted);
    // assert!(info_b.signed_self.is_some());
}
```

This requires adding a timeout parameter to `run_cross_tenure_scenario` (mirroring `run_sibling_scenario`'s existing `Duration` parameter) so the freshness window can be forced to zero while the mock node still serves tenure A's burn block/sortition/tip as canonical via the existing `TenureAFate::SortitionStillCanonical` mock wiring [6](#0-5) . Running it against current code should demonstrate `info_b.signed_self.is_some()`, proving the guard depends solely on `last_endorsed` recency rather than actual liveness for cross-tenure conflicts.

### Citations

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

**File:** stacks-signer/src/v0/tests.rs (L981-1018)
```rust
        match fate {
            TenureAFate::Live => {}
            TenureAFate::SortitionStillCanonical
            | TenureAFate::SortitionOrphaned
            | TenureAFate::SortitionOrphanedNodeBehind => {
                // We heard tenure 1's burn block arrive, so the signing path can ask the node
                // whether its sortition is still canonical. The mock node answers only for
                // the burn hash it serves: everything else 404s (= orphaned). A 404 is only
                // trusted once the node's burnchain tip (`/v2/info`) has reached the burn
                // block's height, so the orphan fates also decide what that reports.
                let tenure_a_burn_hash = BurnchainHeaderHash([0xaa; 32]);
                node.signer
                    .signer_db
                    .insert_burn_block(
                        &tenure_a_burn_hash,
                        &tenure_a,
                        1,
                        &SystemTime::now(),
                        &BurnchainHeaderHash([0xa9; 32]),
                    )
                    .unwrap();
                let mut tips = node.tips.lock().unwrap();
                match fate {
                    TenureAFate::SortitionStillCanonical => {
                        tips.push((
                            format!("/v3/sortitions/burn/{}", tenure_a_burn_hash.to_hex()),
                            canonical_sortition_response(&tenure_a_burn_hash, 1),
                        ));
                    }
                    TenureAFate::SortitionOrphaned => {
                        tips.push(("/v2/info".to_string(), peer_info_response(1)));
                    }
                    TenureAFate::SortitionOrphanedNodeBehind => {
                        tips.push(("/v2/info".to_string(), peer_info_response(0)));
                    }
                    _ => unreachable!(),
                }
            }
```
