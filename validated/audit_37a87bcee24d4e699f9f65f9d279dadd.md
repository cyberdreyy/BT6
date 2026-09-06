### Title
Stale cross-tenure signed conflicts bypass `conflict_still_blocks` node liveness check, enabling a double-sign at the same Stacks height across tenures - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`handle_block_pre_commit` gates the entire liveness re-check (`conflict_still_blocks`) behind a freshness comparison via short-circuit `&&`, so once a previously-signed block's `last_endorsed` timestamp falls past `tenure_last_block_proposal_timeout`, the signer never asks the node whether that block is still canonical before signing a same-height sibling in a *different* tenure. Only same-tenure ("own tenure") conflicts get an unconditional node round-trip (`get_tenure_tip`) regardless of staleness; cross-tenure conflicts get none once stale.

### Finding Description
The uniqueness invariant this code is meant to enforce is: a signer must never place two signatures over two different blocks at the same `stacks_height` unless it has independently established the first is dead. `get_signed_conflicts` returns every signed block at or above the target height across ALL tenures, exactly to catch a sibling tenure-change block starting a competing tenure atop the same parent [1](#0-0) .

In `handle_block_pre_commit`, the guard that would refuse to sign is:
```
conflict.last_endorsed > freshness_cutoff
    && !self.reorg_permit_stands(stacks_client, conflict)
    && self.conflict_still_blocks(stacks_client, conflict, block_info.block.header.chain_length)
``` [2](#0-1) 

Because Rust `&&` short-circuits, once `conflict.last_endorsed > freshness_cutoff` is `false` (i.e. the conflicting block B's signature is stale), `conflict_still_blocks` — the only code that actually asks the node whether B's tenure/sortition is still canonical and whether B is still reachable — is never invoked. The separate, always-run node check that follows only applies `conflict.consensus_hash == block_info.block.header.consensus_hash` — i.e. only for a conflict in the proposal's *own* tenure [3](#0-2) . A cross-tenure conflict (B in tenure T1, B' a competing tenure-change block in tenure T2, both at height h off the same parent) never triggers this branch.

The earlier chainstate re-check (`check_block_against_signer_db_state` → `check_tenure_change_confirms_parent`) also cannot see B: for a tenure-change block it only inspects the *parent* tenure's last signed block via `get_tenure_last_block_info`, not the sibling tenure that actually holds B [4](#0-3) . The project's own documentation confirms this is a known blind spot: "a signed sibling at the same height in a third tenure is invisible to it" and states that the cross-tenure `get_signed_conflicts` guard is supposed to be "the silent backstop" for exactly this case [5](#0-4) . But that backstop itself disables its own liveness check once staleness is reached, per the freshness-first short-circuit comment: "Check freshness first: it is a local timestamp comparison... so stale conflicts cost no round-trips" [6](#0-5) .

The existing test suite only exercises this cross-tenure path with the freshness window pinned wide open (`Duration::from_secs(100_000)`), i.e. `run_cross_tenure_scenario` never drives the stale case, unlike the same-tenure sibling tests which explicitly cover both fresh and stale timing (`stale_sibling_still_refused_when_canonical_tip_at_height`, `stale_sibling_replaced_when_canonical_tip_below`) [7](#0-6) [8](#0-7) . There is no test proving the signer refuses to sign a stale cross-tenure conflict that the node still reports as canonical.

Exploit flow: attacker wins one sortition slot for tenure T2 atop the same parent that legitimately produced tenure T1's block B (this can happen naturally, e.g., a slow/legitimate miner race or an attacker-controlled miner racing the same slot window). The attacker crafts B' as a tenure-change proposal with `prev_tenure_consensus_hash = parent`, `chain_length = h` (same as B), gossips it after B's `last_endorsed` has aged past `tenure_last_block_proposal_timeout` but while B is still node-canonical. Each signer independently: (1) fails to catch the conflict via the parent-tenure chainstate check (B isn't in the parent tenure), (2) evaluates `get_signed_conflicts(h, hash_B')`, finds B, but skips `conflict_still_blocks` because `last_endorsed <= freshness_cutoff`, (3) finds no own-tenure conflict (consensus hashes differ), and signs B'.

### Impact Explanation
If enough signers hit the same staleness window (plausible since `last_endorsed` is derived from the same real-world signing time for every honest signer that already signed B), a second quorum can independently form over B' at the same height as an already-quorate B, in a different tenure. This breaks the core uniqueness/canonicity guarantee the pre-commit/conflict-check machinery exists to enforce and can produce two validly group-signed, mutually exclusive blocks at the same Stacks height — a Critical chain-safety issue (conflicting block signed) per the stated severity taxonomy.

### Likelihood Explanation
Preconditions: an attacker needs to win a single miner slot (one BTC block, achievable with their own hashpower/spend as stated) to produce a competing tenure-change block, and must time its gossip so the pre-commit threshold is crossed after B's `last_endorsed` ages past `tenure_last_block_proposal_timeout` (a configurable, often short window). No majority-signer collusion, no auth_token, no local host access is required — only crafting and gossiping a BlockProposal, which is within the stated attacker capability. The main uncertainty is whether the signer's own proposal-time validation (`SortitionsView`/`GlobalStateView::check_proposal`, which is signer-side but derived from burn-view state) would independently reject B' as an illegitimate tenure-change when T1's sortition is still canonical and unreorged; that check was not fully traced in this pass and is the primary caveat on exploitability. If that check does reject non-reorg competing tenures at proposal time, this reduces to a narrower reorg-timing race; if it does not, the finding stands as described.

### Recommendation
Remove the short-circuit dependency of `conflict_still_blocks` on freshness for cross-tenure conflicts: always invoke `conflict_still_blocks` (or an equivalent node-canonicity check) for every conflict returned by `get_signed_conflicts`, independent of `last_endorsed`, before excluding it as stale. At minimum, treat staleness only as a cache-avoidance hint and still perform the tenure/sortition-liveness query when the conflict is in a different tenure than the block being signed, mirroring the always-on `get_tenure_tip` check already used for the own-tenure branch.

### Proof of Concept
Add a new test in `stacks-signer/src/v0/tests.rs` mirroring `run_cross_tenure_scenario` but parameterized with a short `tenure_last_block_proposal_timeout` (e.g. `Duration::ZERO`) so A's (`B`'s) signature is immediately stale, while configuring the mock node's `/v3/tenures/tip_metadata` and sortition endpoints to report tenure 1 (A's tenure) as still canonical and its sortition still live (mirroring `TenureAFate::SortitionStillCanonical` but under a stale timeout instead of a wide-open one):
```rust
#[test]
fn stale_cross_tenure_sibling_should_still_be_refused_when_canonical() {
    // Zero timeout makes A's signature stale immediately, but the node still reports
    // tenure 1's sortition as canonical and A as reachable -- the signer must NOT sign B.
    let (info_a, info_b) = run_cross_tenure_scenario_with_timeout(
        Duration::ZERO,
        TenureAFate::SortitionStillCanonical,
    );
    assert_a_signed(&info_a);
    assert_eq!(
        info_b.state,
        BlockState::PreCommitted,
        "block B must NOT be promoted while A's tenure is still canonical, got: {}",
        info_b.state
    );
    assert!(
        info_b.signed_self.is_none(),
        "block B must NOT be signed: A is a live conflict in a different tenure at the same height"
    );
}
```
This requires factoring `run_cross_tenure_scenario` to accept a `tenure_last_block_proposal_timeout` parameter instead of hardcoding `Duration::from_secs(100_000)`. Under the current implementation this test is expected to fail (B gets signed) because `conflict_still_blocks` is skipped once A is stale, demonstrating the double-sign path.

### Citations

**File:** stacks-signer/src/signerdb.rs (L1587-1605)
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
```

**File:** stacks-signer/src/v0/signer.rs (L1398-1402)
```rust
        // A fresh signature only blocks while the block it covers could still be part of the
        // chain: see `conflict_still_blocks`, which asks the node whether it is. Check
        // freshness first: it is a local timestamp comparison, while `reorg_permit_stands`
        // and `conflict_still_blocks` each query the node, so stale conflicts cost no
        // round-trips.
```

**File:** stacks-signer/src/v0/signer.rs (L1403-1411)
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

**File:** stacks-signer/src/chainstate/mod.rs (L317-364)
```rust
    /// Get the last signed block from the given tenure if it has not timed out.
    /// Even globally accepted blocks are allowed to be timed out, as that
    /// triggers the signer to consult the Stacks node for the latest globally
    /// accepted block. This is needed to handle Bitcoin reorgs correctly.
    ///
    /// The timeout window is measured from the last time a signature actually covered the
    /// block: our own (`signed_self`) or the observed group/global acceptance
    /// (`signed_group`), whichever is later, matching how `get_signed_conflicts` measures
    /// endorsement freshness. `approved_time` is deliberately not used: it is stamped at
    /// pre-commit, which carries no signature, so it would close the window early. This also
    /// means a globally accepted block we never signed ourselves gets a full window from the
    /// time its acceptance was observed, rather than timing out instantly for lack of a
    /// timestamp.
    pub fn get_tenure_last_block_info(
        consensus_hash: &ConsensusHash,
        signer_db: &SignerDb,
        tenure_last_block_proposal_timeout: Duration,
    ) -> Result<Option<BlockInfo>, ClientError> {
        // Get the last signed block in the tenure
        let last_signed_block = signer_db
            .get_last_signed_block(consensus_hash)
            .map_err(|e| ClientError::InvalidResponse(e.to_string()))?;

        let Some(block_info) = last_signed_block else {
            return Ok(None);
        };

        // `approved_time` may hold the pre-commit time; use the actual signature time.
        let Some(signed_over_time) = block_info.signed_self.max(block_info.signed_group) else {
            return Ok(None);
        };

        if signed_over_time.saturating_add(tenure_last_block_proposal_timeout.as_secs())
            > get_epoch_time_secs()
        {
            // The last accepted block is not timed out, return it
            Ok(Some(block_info))
        } else {
            // The last accepted block is timed out
            info!(
                "Last accepted block has timed out";
                "signer_signature_hash" => %block_info.block.header.signer_signature_hash(),
                "signed_over_time" => signed_over_time,
                "state" => %block_info.state,
            );
            Ok(None)
        }
    }
```

**File:** docs/signer-flows.md (L280-286)
```markdown
- the re-check only ever looks at _one_ tenure (a tenure-change block's parent,
  or any other block's own), so a signed sibling at the same height in a third
  tenure is invisible to it;
- the `DuplicateBlockFound` check that would catch a second block in the same
  tenure lives in `check_proposal` and runs only at proposal arrival, never
  again. A block that crosses the pre-commit threshold minutes later has no
  other guard, which is what the own-tenure branch above covers.
```

**File:** stacks-signer/src/v0/tests.rs (L791-826)
```rust
    #[test]
    fn stale_sibling_still_refused_when_canonical_tip_at_height() {
        // A zero timeout makes A's signature stale immediately, but the node reports A as the
        // canonical tip at the same height, so the replacement must still be refused.
        let (info_a, info_b, _) = run_sibling_scenario(Duration::ZERO, true, None);
        assert_a_signed(&info_a);
        assert_eq!(
            info_b.state,
            BlockState::PreCommitted,
            "block B should be pre-committed but not promoted, got: {}",
            info_b.state
        );
        assert!(
            info_b.signed_self.is_none(),
            "block B must NOT be signed: the conflicting sibling is canonical at this height"
        );
    }

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

**File:** stacks-signer/src/v0/tests.rs (L902-959)
```rust
    /// Drive the cross-tenure race: block A starts tenure 1 and block B starts tenure 2, both
    /// at height 10 off the same parent, so they are siblings in different tenures. A is signed
    /// first, then B's validation returns. Neither tenure's Stacks blocks are known to the mock
    /// node (the realistic case: a block we accepted locally is not handed to the node until
    /// the whole signer set has signed it), so what the node can answer is decided by `fate`:
    /// whether tenure 1's sortition is still canonical, and whether A was ever handed over.
    /// Returns the resulting `BlockInfo` for A and for B.
    fn run_cross_tenure_scenario(fate: TenureAFate) -> (BlockInfo, BlockInfo) {
        let miner = StacksPrivateKey::from_seed(&[0, 1]);
        let parent_tenure = ConsensusHash([0; 20]);
        let tenure_a = ConsensusHash([1; 20]);
        let tenure_b = ConsensusHash([2; 20]);

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

        let now = get_epoch_time_secs();
        let block_a = tenure_start(&miner, &tenure_a, &parent_tenure, &parent_id, now);
        let block_b = tenure_start(&miner, &tenure_b, &parent_tenure, &parent_id, now + 1);
        let hash_a = block_a.header.signer_signature_hash();
        let hash_b = block_b.header.signer_signature_hash();
        assert_ne!(block_a.header.consensus_hash, block_b.header.consensus_hash);
        assert_eq!(block_a.header.chain_length, block_b.header.chain_length);

        // Only the shared parent tenure has a tip, so the tenure-change parent check passes for
        // both blocks. Tenures 1 and 2 are unknown to the node (404).
        let parent_tip = BlockHeaderWithMetadata {
            anchored_header: parent_header.into(),
            burn_view: Some(parent_tenure.clone()),
        };
        let tips = vec![
            (
                format!("/v3/tenures/tip_metadata/{parent_tenure}"),
                serde_json::to_string(&parent_tip).unwrap(),
            ),
            (
                "/v3/blocks/upload".to_string(),
                format!(r#"{{"stacks_block_id":"{parent_id}","accepted":true}}"#),
            ),
        ];

        // The freshness window is wide open: A's signature is fresh throughout, so only the
        // orphan record can decide whether it still blocks B.
        let mut node = MockNode::new(tips, Duration::from_secs(100_000));
```
