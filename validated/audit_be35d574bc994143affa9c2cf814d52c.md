### Title
Malicious miner can force signers to sign two conflicting blocks at the same height by exploiting the pre-commit staleness fallback - ([File: stacks-signer/src/v0/signer.rs])

### Summary
The signer's pre-commit conflict guard is designed to prevent a signer from placing a signature on two blocks at the same height ("no double-sign"), but it deliberately lifts that guard once the earlier signature goes stale and the signer's node has not yet observed that block as the canonical tenure tip — a fallback built for Bitcoin-reorg recovery. A miner who holds the tenure and controls block proposal timing can weaponize this liveness fallback: get block A signed by the signer set, prevent/delay it from becoming the observable canonical tip until the freshness window (`tenure_last_block_proposal_timeout`) elapses, then propose a conflicting sibling block B at the same height/tenure. Signers will sign B too, producing two fully signer-signed, mutually conflicting blocks at the same height.

### Finding Description
In `Signer::handle_block_pre_commit`, once a proposal crosses the 70% pre-commit threshold, the signer looks for previously *signed* conflicts at the same or higher height via `SignerDb::get_signed_conflicts` [1](#0-0) . A conflict only vetoes the new signature while it is both *fresh* (`last_endorsed > freshness_cutoff`) and *still live* per `conflict_still_blocks` [2](#0-1) .

If the earlier conflicting signature has gone stale, the guard falls through to a same-tenure-only check: it asks the node for the tenure's canonical tip height via `get_tenure_tip`, and only refuses to sign if that tip is already at or above the proposed height [3](#0-2) . If the previously-signed sibling block was never confirmed by the node (tip still below the new proposal's height), the signer proceeds to sign the new, conflicting block [4](#0-3) .

This exact behavior is intentional and covered by a unit test that demonstrates two conflicting same-tenure, same-height, same-parent sibling blocks (A and B) both getting `signed_self` set, driven purely by (1) A's freshness timeout expiring and (2) the node reporting a tip still below A's height [5](#0-4) . The design rationale documented alongside the code explicitly frames this as "the reorg-recovery case": rather than stalling the tenure forever, the signer signs the replacement once the first block's confirmation appears to have failed [6](#0-5) .

The mirror concern from the external report — an operator (a role trusted to reliably resolve/complete a request) intentionally preventing resolution of one signed request so that a second, conflicting one is also accepted — maps directly onto the miner here: the miner is the party that controls tenure timing and block proposal content, and the "resolution" being stalled is confirmation of the previously signed block reaching the node's observable tip.

### Impact Explanation
If exploited, this breaks the fundamental "one signed block per height per tenure" safety invariant the pre-commit guard exists to enforce (documented explicitly: "signing both would be the double-sign this guard is for" [7](#0-6) ). The result is two blocks, both carrying a full ≥70%-weight signer signature set, that conflict with each other at the same chain height. This is exactly the class of finding the scan targets as Critical: a signer signing a conflicting block. Whichever of the two blocks the miner (or anyone with the aggregated signatures) chooses to broadcast/push later can be selected adversarially, undermining the assumption that a signer-signed block is uniquely canonical for that height and enabling equivocation/fork scenarios that the signer set is supposed to prevent.

### Likelihood Explanation
The path is reachable by a single sortition-winning miner using only its ordinary privileges (control over block proposal content/timing across a tenure, i.e., proposing A then, after a bounded and configurable timeout, proposing a conflicting B) plus the natural, unprivileged possibility that a signed block's push/confirmation to the node lags or fails transiently (network delay, node restart, momentary node unavailability) — no majority of signers, no signer key, and no node-auth token are required. The vulnerable branch is exercised by the codebase's own test (`stale_sibling_replaced_when_canonical_tip_below`), confirming the scenario is not merely theoretical but a directly triggerable code path under realistic timing conditions.

### Recommendation
Reconsider the asymmetry in the own-tenure fallback: unlike `conflict_still_blocks` (which defaults to "still blocks" — the safe direction — whenever the node cannot be consulted or the block's status is ambiguous), the same-tenure staleness fallback in `handle_block_pre_commit` defaults to "sign" whenever the node reports (or fails to report) a tip below the proposal height, even though "tip below" does not prove the earlier block is dead — it may simply not yet be confirmed. Consider requiring stronger proof that the earlier signed sibling is actually dead (e.g., proof of a burnchain-level fact, analogous to the cross-tenure `conflict_still_blocks` logic) rather than treating "not yet observed as canonical" as equivalent to "abandoned," before allowing a same-tenure sibling at the same height to be signed.

### Proof of Concept
1. Miner proposes tenure-start block A (height h, tenure T). Signers pre-commit, cross 70% threshold, and sign A (`LocallyAccepted`, `signed_self` set) — as in `run_sibling_scenario` [8](#0-7) .
2. A's confirmation/push to the node does not complete before `tenure_last_block_proposal_timeout` elapses (network delay, or the miner declines to relay/confirm), so A's endorsement becomes stale and the node's tenure tip is still the parent (height h-1), not A.
3. Miner proposes conflicting sibling block B at the same height h, same tenure T, same parent, differing only in transactions/timestamp.
4. Signers re-run `handle_block_pre_commit` for B: A is stale, `reorg_permit_stands` is false (no legitimate reorg was sanctioned), and `get_tenure_tip` reports height h-1 (< h), so the refusal branch is skipped and B is signed [9](#0-8) .
5. Both A and B now hold independent ≥70%-weight signer signature sets over conflicting blocks at height h, reproduced deterministically by `stale_sibling_replaced_when_canonical_tip_below` [5](#0-4) .

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

**File:** stacks-signer/src/v0/signer.rs (L1432-1470)
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
```

**File:** stacks-signer/src/v0/tests.rs (L603-660)
```rust
    fn run_sibling_scenario(
        tenure_last_block_proposal_timeout: Duration,
        serve_sibling_as_tip: bool,
        re_propose_b_after: Option<Duration>,
    ) -> (BlockInfo, BlockInfo, Option<BlockInfo>) {
        let miner = StacksPrivateKey::from_seed(&[0, 1]);
        let tenure = ConsensusHash([1; 20]);
        let parent_tenure = ConsensusHash([0; 20]);

        // The parent block of the tenure (height 9); both siblings build on it at height 10.
        let mut parent_header = NakamotoBlockHeader {
            version: 1,
            chain_length: 9,
            burn_spent: 10,
            consensus_hash: parent_tenure.clone(),
            parent_block_id: StacksBlockId([9; 32]),
            tx_merkle_root: Sha512Trunc256Sum([0; 32]),
            state_index_root: TrieHash([0; 32]),
            timestamp: 9,
            miner_signature: MessageSignature::empty(),
            signer_signature: vec![],
            pox_treatment: BitVec::ones(1).unwrap(),
            problematic_txs: vec![],
        };
        parent_header.sign_miner(&miner).unwrap();
        let parent_id = parent_header.block_id();

        // Two conflicting sibling tenure-start blocks: same tenure, parent, and height; the only
        // difference is the timestamp (hence the hash). The timestamps are current so that a
        // re-proposal of B passes the proposal age check.
        let now = get_epoch_time_secs();
        let block_a = tenure_start(&miner, &tenure, &parent_tenure, &parent_id, now);
        let block_b = tenure_start(&miner, &tenure, &parent_tenure, &parent_id, now + 1);
        let hash_a = block_a.header.signer_signature_hash();
        let hash_b = block_b.header.signer_signature_hash();
        assert_ne!(hash_a, hash_b);
        assert_eq!(block_a.header.consensus_hash, block_b.header.consensus_hash);
        assert_eq!(block_a.header.chain_length, block_b.header.chain_length);

        // The parent tenure's tip is always the parent block, so the tenure-change parent
        // check passes for both siblings. The current tenure's tip is what the signing-time
        // check consults once a conflicting signature has timed out: either A itself (it
        // became canonical) or still the parent (it did not).
        let parent_tip = BlockHeaderWithMetadata {
            anchored_header: parent_header.clone().into(),
            burn_view: Some(tenure.clone()),
        };
        let tenure_tip = if serve_sibling_as_tip {
            BlockHeaderWithMetadata {
                anchored_header: block_a.header.clone().into(),
                burn_view: Some(tenure.clone()),
            }
        } else {
            BlockHeaderWithMetadata {
                anchored_header: parent_header.into(),
                burn_view: Some(tenure.clone()),
            }
        };
```

**File:** stacks-signer/src/v0/tests.rs (L809-826)
```rust
    #[test]
    fn stale_sibling_replaced_when_canonical_tip_below() {
        // A zero timeout makes A's signature stale immediately, and the node's canonical tip
        // is still the parent (height 9): A failed to be confirmed, so the signer must sign
        // the replacement rather than stall the tenure (the reorg-recovery case).
        let (info_a, info_b, _) = run_sibling_scenario(Duration::ZERO, false, None);
        assert_a_signed(&info_a);
        assert_eq!(
            info_b.state,
            BlockState::LocallyAccepted,
            "block B should be signed: the conflicting sibling timed out and is not canonical, got: {}",
            info_b.state
        );
        assert!(
            info_b.signed_self.is_some(),
            "block B should carry our signature after the conflict timed out unconfirmed"
        );
    }
```

**File:** docs/signer-flows.md (L310-320)
```markdown
2. **Does the node's canonical Stacks chain still reach the block itself?**
   - **it does** — real chain state; keep blocking;
   - **it does not, and the block was globally accepted** — the node once _did_
     have it, so a reorg moved past it. That is proof it is dead;
   - **it does not, and the block was never globally accepted** — a block is
     not handed to the node until the whole signer set has signed it, so this
     may mean "not yet seen" rather than "dead". A sibling at the same height
     therefore keeps blocking, since signing both would be the double-sign this
     guard exists for; a block _above_ the proposal does not, because it is no
     sibling and abandoning an unconfirmed block to restart beneath it is a
     reorg, not an equivocation.
```
