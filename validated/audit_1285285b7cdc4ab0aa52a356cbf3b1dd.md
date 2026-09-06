Confirmed: this is a genuine analog, not a false positive.

### Title
V1 signer state machine's `DuplicateBlockFound` guard checks only globally-accepted blocks, letting a signer pre-commit toward a rival tenure-start block that a peer has already locally accepted - ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
`SortitionsView::validate_tenure_change_payload` (v1 chainstate path) rejects a competing tenure-change proposal only if `SignerDb::get_last_globally_accepted_block` finds a prior block in the same tenure. [1](#0-0)  The v2 path was patched to use `get_last_signed_block`, which also covers `LocallyAccepted` blocks, precisely because the old check "would miss blocks in `LocallyAccepted` or `PreCommitted` state and incorrectly allow a duplicate tenure change." [2](#0-1) [3](#0-2)  V1 was never given the equivalent fix, so a fleet still running the v1 (local-state) protocol path — `check_block_against_local_state` — remains exposed to exactly the regression the v2 test guards against. [4](#0-3) 

### Finding Description
The signer's proposal-time duplicate-tenure-start guard is supposed to be the first line of defense against a miner (or a forked view of one) submitting two competing "tenure change / BlockFound" proposals for the same tenure. In the v2 (global-state) path this guard was fixed to treat any block this signer has *signed* — including merely `LocallyAccepted` ones that never reached the 70% group threshold — as blocking a second tenure-start proposal, per the code comment and regression test. [2](#0-1) [5](#0-4) 

The v1 path's `validate_tenure_change_payload` still queries only `get_last_globally_accepted_block`, which is `None` whenever the first tenure-start block was signed by this node (or observed as signed by others) but never crossed the group threshold — the exact scenario the v2 fix targeted. [1](#0-0)  A miner can therefore get a second tenure-start block past the v1 proposal-time check with `Ok(())` when the first one is only `LocallyAccepted`.

The documented "last line of defense" is the pre-commit-time `get_signed_conflicts` guard in `signer.rs`, which does look at any block ever signed (`signed_self`/`signed_group`, not just globally accepted). [6](#0-5) [7](#0-6)  However, that guard only blocks while the conflicting signature is *fresh* (`last_endorsed > freshness_cutoff`, driven by `tenure_last_block_proposal_timeout`); once it goes stale, the code deliberately falls through to `conflict_still_blocks`, which allows signing the replacement whenever the node has not yet been handed the first block — precisely the case for a `LocallyAccepted`-but-not-globally-accepted first block. [8](#0-7) [9](#0-8)  This "stale sibling replaced when canonical tip below" fallback is explicitly tested as intended reorg-recovery behavior. [10](#0-9) 

So the combination is: (1) v1's proposal-time gate does not reject the rival proposal early (unlike v2), and (2) the pre-commit-time gate is only a *temporary* block that expires after `tenure_last_block_proposal_timeout`. On a v1-protocol signer, once that timeout elapses without the first (`LocallyAccepted`) block reaching the stacks-node, the second, rival tenure-start block can be signed — something the v2 fix was specifically written to prevent from ever reaching pre-commit in the first place, keeping the exposure window (and the reliance on network-timing races between miner, peers, and the node) far shorter than on v1.

### Impact Explanation
This is a "signer signing a conflicting/non-canonical block" class of impact: the same signer can end up with a fresh (within-timeout) opinion locked onto one tenure-start block via `LocallyAccepted`, while its own v1 proposal check fails to stop a second, competing tenure-start proposal from being validated and pre-committed, relying entirely on the shorter-lived pre-commit guard to eventually catch it rather than rejecting at the door as v2 does. Any timing window where the first block's local acceptance goes stale before global acceptance (e.g., other signers slow, network partition, or the node simply not yet having been handed the block because the group threshold was never reached) lets the signer contribute its weight to a second, conflicting tenure-start block — undermining the "one signed block per tenure-start" invariant that both `get_last_signed_block`(v2) and `get_signed_conflicts` are designed to protect.

### Likelihood Explanation
Triggering requires only a single miner (holding the current tenure's block-commit slot) proposing two tenure-start blocks for the same sortition — a routine capability of the block-proposing role, no majority-of-signers or key compromise needed — combined with the first proposal failing to reach global (70%) acceptance quickly (e.g., a signer's own or a peer's slow response, a network hiccup, or the node not yet reachable), which is a normal operational condition, not an adversarial majority.

### Recommendation
Update `stacks-signer/src/chainstate/v1.rs`'s `validate_tenure_change_payload` to use `SignerDb::get_last_signed_block` (as v2 already does) instead of `get_last_globally_accepted_block`, so a `LocallyAccepted` prior block in the tenure is also treated as a duplicate at proposal time, closing the same regression window that was patched in v2. [1](#0-0) 

### Proof of Concept
1. Configure a fleet of signers still negotiating/using the v1 (local sortition-state) protocol path, i.e. `check_block_against_local_state` is exercised (`determine_active_signer_protocol_version` resolves to a non-global-state version). [11](#0-10) 
2. Miner wins a sortition and proposes tenure-start block A with a `TenureChange`/`BlockFound` payload. The signer under test locally accepts and signs A (`mark_locally_accepted`), but a network delay/partition prevents the 70% group threshold from being reached, so A never reaches `GloballyAccepted` and is never handed to the stacks-node.
3. Before A's signature time is `tenure_last_block_proposal_timeout` seconds old, the pre-commit conflict guard (`get_signed_conflicts`) still blocks a rival — but wait past that timeout (or exploit network jitter to delay peer pre-commits) so A's endorsement becomes stale.
4. Miner proposes a second, competing tenure-start block B for the same tenure (different transactions/coinbase). `validate_tenure_change_payload` in `v1.rs` calls `get_last_globally_accepted_block`, which returns `None` (A is only `LocallyAccepted`), so the `DuplicateBlockFound` rejection at proposal time is skipped — unlike what `check_tenure_change_rejects_when_locally_accepted_block_exists` verifies happens on v2. [5](#0-4) 
5. B proceeds to validation; at the pre-commit stage, `conflict_still_blocks` finds A's stale, unconfirmed-by-node signature and does not block B (per `stale_sibling_replaced_when_canonical_tip_below`). [10](#0-9)  The signer signs B, producing a signature over a second, conflicting tenure-start block for the same tenure that v1's proposal-time gate should have rejected outright as v2 does.

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L505-518)
```rust
        let last_in_current_tenure = signer_db
            .get_last_globally_accepted_block(&block.header.consensus_hash)
            .map_err(|e| {
                SignerChainstateError::from(ClientError::InvalidResponse(e.to_string()))
            })?;
        if let Some(last_in_current_tenure) = last_in_current_tenure {
            warn!(
                "Miner block proposal contains a tenure change, but we've already signed a block in this tenure. Considering proposal invalid.";
                "proposed_block_consensus_hash" => %block.header.consensus_hash,
                "proposed_block_signer_signature_hash" => %block.header.signer_signature_hash(),
                "last_in_tenure_signer_signature_hash" => %last_in_current_tenure.block.header.signer_signature_hash(),
            );
            return Err(RejectReason::DuplicateBlockFound);
        }
```

**File:** stacks-signer/src/chainstate/v2.rs (L340-357)
```rust
        // We already confirmed in check miner activity that the current tenure is valid. So check we are not
        // reorging the tenure blocks. Only blocks we have signed (locally or globally accepted) count
        // here: a block we have merely pre-committed to carries no signature from us, so it is safe to
        // accept a competing tenure-start block in its place if it failed to reach consensus.
        let last_in_current_tenure = signer_db
            .get_last_signed_block(&block.header.consensus_hash)
            .map_err(|e| {
                SignerChainstateError::from(ClientError::InvalidResponse(e.to_string()))
            })?;
        if let Some(last_in_current_tenure) = last_in_current_tenure {
            warn!(
                "Miner block proposal contains a tenure change, but we've already signed a block in this tenure. Considering proposal invalid.";
                "proposed_block_consensus_hash" => %block.header.consensus_hash,
                "proposed_block_signer_signature_hash" => %block.header.signer_signature_hash(),
                "last_in_tenure_signer_signature_hash" => %last_in_current_tenure.block.header.signer_signature_hash(),
            );
            return Err(RejectReason::DuplicateBlockFound);
        }
```

**File:** stacks-signer/src/chainstate/tests/v2.rs (L748-754)
```rust
/// Test that a tenure change proposal is rejected when a locally-accepted
/// (but not globally-accepted) block already exists in the same tenure.
///
/// This is a regression test: previously, the check used
/// `get_last_globally_accepted_block`, which would miss blocks in
/// `LocallyAccepted` or `PreCommitted` state and incorrectly allow
/// a duplicate tenure change.
```

**File:** stacks-signer/src/chainstate/tests/v2.rs (L756-850)
```rust
fn check_tenure_change_rejects_when_locally_accepted_block_exists() {
    let MockServerClient {
        server,
        client: stacks_client,
        config: _,
    } = MockServerClient::new();
    let rand_int = server.local_addr().unwrap().port();

    let (_stacks_client, mut signer_db, block_sk, mut block, cur_sortition, _, sortitions_view) =
        setup_test_environment(&format!("{}_{rand_int}", function_name!()));

    // Set up the block in the current tenure
    block.header.consensus_hash = cur_sortition.data.consensus_hash.clone();
    let parent_block_header = make_parent_header_meta(&block_sk, &mut block);
    let response = crate::client::tests::build_get_tenure_tip_response(&parent_block_header);

    // Insert a locally-accepted block in the same tenure (same consensus_hash).
    // This simulates a miner's first tenure-start block that the signer has
    // locally accepted but that hasn't yet gathered enough signatures to be
    // globally accepted. In practice this block would contain a tenure-change
    // and coinbase tx, but we omit them here because `get_last_accepted_block`
    // only queries by consensus_hash and block state — the block's transactions
    // are irrelevant to the duplicate check.
    let existing_block_proposal = BlockProposal {
        block: NakamotoBlock::new(
            NakamotoBlockHeader {
                version: 1,
                chain_length: 10,
                burn_spent: 10,
                consensus_hash: cur_sortition.data.consensus_hash.clone(),
                parent_block_id: StacksBlockId([0; 32]),
                tx_merkle_root: Sha512Trunc256Sum([0; 32]),
                state_index_root: TrieHash([0; 32]),
                timestamp: 11,
                miner_signature: MessageSignature::empty(),
                signer_signature: vec![],
                pox_treatment: BitVec::ones(1).unwrap(),
                problematic_txs: vec![],
            },
            vec![],
        ),
        burn_height: 2,
        reward_cycle: 1,
        block_proposal_data: BlockProposalData::empty(),
    };
    let mut existing_block_info = BlockInfo::from(existing_block_proposal);
    existing_block_info.mark_locally_accepted(false).unwrap();
    signer_db.insert_block(&existing_block_info).unwrap();

    // Now build a *second* tenure-start block proposal for the same tenure.
    // This simulates the miner attempting to replace their first block (e.g.,
    // with different transactions). The tenure change tx must have
    // cause=BlockFound with a coinbase to be recognized as a tenure-start block.
    let tenure_change_payload = TenureChangePayload {
        tenure_consensus_hash: cur_sortition.data.consensus_hash.clone(),
        prev_tenure_consensus_hash: cur_sortition.data.parent_tenure_id.clone(),
        burn_view_consensus_hash: cur_sortition.data.consensus_hash.clone(),
        previous_tenure_end: block.header.parent_block_id.clone(),
        previous_tenure_blocks: 1,
        cause: TenureChangeCause::BlockFound,
        pubkey_hash: Hash160::from_node_public_key(&StacksPublicKey::from_private(&block_sk)),
    };
    let tenure_change_tx = make_tenure_change_tx(tenure_change_payload);
    let coinbase_tx = StacksTransaction::new(
        TransactionVersion::Testnet,
        TransactionAuth::Standard(TransactionSpendingCondition::new_initial_sighash()),
        TransactionPayload::Coinbase(CoinbasePayload([0; 32]), None, Some(VRFProof::empty())),
    );
    *block.executed_and_skipped_txs_mut() = vec![tenure_change_tx, coinbase_tx];
    block.header.sign_miner(&block_sk).unwrap();

    let exit_flag = Arc::new(AtomicBool::new(false));
    let moved_exit_flag = exit_flag.clone();

    let serve = std::thread::spawn(move || {
        crate::client::tests::write_response_nonblockinig(
            &server,
            response.as_bytes(),
            moved_exit_flag,
        );
    });

    let result = sortitions_view.check_proposal(&stacks_client, &mut signer_db, &block);

    exit_flag.store(true, Ordering::SeqCst);
    serve.join().unwrap();

    // The proposal should be rejected because there's already a locally-accepted
    // block in this tenure. Before the fix, this would have incorrectly passed
    // because get_last_globally_accepted_block would not find the locally-accepted block.
    assert!(
        matches!(result, Err(RejectReason::DuplicateBlockFound)),
        "Expected DuplicateBlockFound rejection when a locally-accepted block exists in the tenure, got: {result:?}"
    );
}
```

**File:** stacks-signer/src/v0/signer.rs (L865-870)
```rust
        if state_version.uses_global_state() {
            self.check_block_against_global_state(stacks_client, &block_info.block)
        } else {
            self.check_block_against_local_state(stacks_client, sortition_state, &block_info.block)
        }
    }
```

**File:** stacks-signer/src/v0/signer.rs (L875-939)
```rust
    fn check_block_against_local_state(
        &mut self,
        stacks_client: &StacksClient,
        sortition_state: &mut Option<SortitionsView>,
        block: &NakamotoBlock,
    ) -> Option<BlockRejection> {
        let signer_signature_hash = block.header.signer_signature_hash();
        let block_id = block.block_id();
        // Get sortition view if we don't have it
        if sortition_state.is_none() {
            *sortition_state =
                SortitionsView::fetch_view(self.proposal_config.clone(), stacks_client)
                    .inspect_err(|e| {
                        warn!(
                            "{self}: Failed to update sortition view: {e:?}";
                            "signer_signature_hash" => %signer_signature_hash,
                            "block_id" => %block_id,
                        )
                    })
                    .ok();
        }

        // Check if proposal can be rejected now if not valid against sortition view
        if let Some(sortition_state) = sortition_state {
            match sortition_state.check_proposal(
                stacks_client,
                &mut self.signer_db,
                block,
                true,
                self.global_state_evaluator
                    .get_global_tx_replay_set()
                    .unwrap_or_default(),
            ) {
                // Error validating block
                Err(RejectReason::ConnectivityIssues(e)) => {
                    warn!(
                        "{self}: Error checking block proposal: {e}";
                        "signer_signature_hash" => %signer_signature_hash,
                        "block_id" => %block_id,
                    );
                    Some(self.create_block_rejection(RejectReason::ConnectivityIssues(e), block))
                }
                // Block proposal is bad
                Err(reject_code) => {
                    warn!(
                        "{self}: Block proposal invalid";
                        "signer_signature_hash" => %signer_signature_hash,
                        "block_id" => %block_id,
                        "reject_reason" => %reject_code,
                        "reject_code" => ?reject_code,
                    );
                    Some(self.create_block_rejection(reject_code, block))
                }
                // Block proposal passed check, still don't know if valid
                Ok(_) => None,
            }
        } else {
            warn!(
                "{self}: Cannot validate block, no sortition view";
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_id,
            );
            Some(self.create_block_rejection(RejectReason::NoSortitionView, block))
        }
    }
```

**File:** stacks-signer/src/v0/signer.rs (L1108-1136)
```rust
    /// Whether a block we signed still conflicts at `proposed_height`.
    ///
    /// The guard exists to stop us endorsing two blocks that could both end up in the chain. It
    /// must not, however, outlive the block it protects: a Bitcoin reorg can kill a block we
    /// signed, and a dead signature must not stall the chain restarting beneath it.
    ///
    /// Two questions, each answerable by the node at any time:
    ///
    /// 1. Is the tenure's sortition still on the canonical burn chain? We saved the tenure's
    ///    burn block when it arrived, and `/v3/sortitions` resolves it against the node's
    ///    canonical fork. A 404 means a burnchain fork orphaned the tenure: everything it built
    ///    is void, so the conflict is dead no matter what state its block is in.
    ///
    /// 2. Does the node's canonical Stacks chain still reach the block?
    ///    * If it does, the block is real chain state, so it keeps blocking. (If the reorg-timing
    ///      rules sanctioned replacing it, the tenure is recorded as superseded and the conflict
    ///      never reaches this check at all.)
    ///    * If it does not, and the block was once globally accepted, the node had it and a
    ///      reorg moved past it. That is proof it is dead, so it stops blocking.
    ///    * If it does not, and the block was never globally accepted, the node may simply never
    ///      have been handed it, since that only happens once the whole signer set has signed. We
    ///      cannot tell "dead" from "not yet known", so a sibling at the same height keeps
    ///      blocking (signing both would be the double-sign this guard is for), while a block
    ///      above the proposal does not: it is no sibling, and abandoning an unconfirmed block to
    ///      restart beneath it is a reorg rather than an equivocation.
    ///
    /// If we have no saved burn block, or the node is unreachable, the conflict keeps blocking.
    /// That only delays the replacement until our signature goes stale, whereas wrongly signing
    /// cannot be taken back.
```

**File:** stacks-signer/src/v0/signer.rs (L1192-1206)
```rust
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
