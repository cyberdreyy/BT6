### Title
Signer can sign two conflicting tenure-start blocks in the same tenure because v1's `DuplicateBlockFound` pre-check only looks at globally-accepted blocks — ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
`SortitionsView::validate_tenure_change_payload` (the v1/pre-global-state chainstate check, used by `check_block_against_local_state`) rejects a second tenure-change proposal in the same tenure only if a **globally accepted** block already exists there: [1](#0-0) 

This is exactly the bug class in the referenced report: the pre-check tests a narrower/stricter condition (`get_last_globally_accepted_block`) than the real invariant that must hold ("have I already put a signature on a block in this tenure" = locally **or** globally accepted). The v2 path was already fixed to check this correctly: [2](#0-1) 

and there is even a dedicated regression test documenting that the old (globally-accepted-only) behavior is wrong: [3](#0-2) 

but v1.rs still contains the buggy version of the check.

### Finding Description
`check_block_against_local_state` is the code path exercised by signers still on the pre-global-signer-state protocol version: [4](#0-3) 

For a tenure-change (tenure-start) block, `check_proposal` (v1) delegates to `validate_tenure_change_payload`, whose final guard is meant to stop a miner from getting a *second*, different tenure-start block signed for the same tenure. It does this by looking for `get_last_globally_accepted_block(&block.header.consensus_hash)`. But a block the signer has already **signed and broadcast an acceptance for** (`LocallyAccepted`, `signed_self` set) is *not yet* globally accepted — global acceptance only happens once the network as a whole reaches the 70% threshold and the node processes it. So if block A (tenure-start) in tenure T has already been locally accepted/signed by this signer but has not yet become globally accepted (e.g., still gathering signatures, or the node hasn't ingested it), a competing tenure-start block B for the same tenure T sails through this duplicate check.

Per `docs/signer-flows.md`, this `DuplicateBlockFound` check is special: it is only evaluated at proposal-arrival time and is never re-run at validate-ok or at signing: [5](#0-4) 

The only backstop for a duplicate discovered later is the pre-commit "own-tenure conflict" guard, which asks the **stacks-node** whether its tenure tip is already at/above this height: [6](#0-5) 

Since A is only locally accepted (not yet processed by the node), `get_tenure_tip` will not show A, so this guard reports "never confirmed" and lets the signer proceed to sign B. The chainstate re-check that does run before signing (`check_block_against_signer_db_state`) only validates the tenure-change block against its **parent** tenure's tip, never against duplicates within its **own** tenure — that gap is only supposed to be covered by the proposal-time `DuplicateBlockFound` check, which is the one that is broken here for v1.

The result: a v1-protocol signer can end up placing valid signatures over two different, conflicting tenure-start blocks (A and B) in the same tenure T — breaking the one-tenure-start-block-per-tenure invariant that the whole reorg/duplicate-detection logic exists to enforce.

### Impact Explanation
This breaks the safety property that a signer never signs two conflicting blocks in the same tenure at the same height. This is a Critical-class issue per the scope rules ("a signer signing an invalid, non-canonical, or conflicting block"): a single miner, by re-proposing a different tenure-start block for the same tenure before the first one is globally accepted, can get an honest v1 signer to sign both, contributing to a fork/equivocation.

### Likelihood Explanation
Triggerable by a single miner (one-slot) with normal proposal/re-proposal behavior plus gossip timing (proposing block B before block A reaches global acceptance) — no majority collusion, no key compromise, and no auth-token/local access is required. It only affects signers still running the v1 (pre-global-state) protocol version, but that population exists as long as any signers haven't upgraded, and the report's own precedent (v2's regression test) shows this exact scenario was already hit and fixed once — just not backported to v1.

### Recommendation
In `stacks-signer/src/chainstate/v1.rs::validate_tenure_change_payload`, replace `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)` with a check equivalent to v2's `signer_db.get_last_signed_block(&block.header.consensus_hash)` (i.e., counting locally accepted blocks too, not only globally accepted ones), matching the fix already applied to v2.

### Proof of Concept
1. Signer runs the v1 (pre-global-state) protocol and enters `check_block_against_local_state`.
2. Miner proposes tenure-start block A for tenure T. Signer validates it (`check_proposal` passes since no prior block exists), submits to node, gets `Ok`, pre-commits, reaches the pre-commit threshold locally, and signs A (`mark_locally_accepted`). At this point `get_last_globally_accepted_block(T)` is still `None` because global acceptance requires network-wide threshold + node ingestion, which hasn't happened yet.
3. Before A is globally accepted, the miner proposes a different tenure-start block B for the same tenure T (e.g., different transactions/timestamp).
4. `validate_tenure_change_payload` for B calls `get_last_globally_accepted_block(T)` — returns `None` — so the `DuplicateBlockFound` rejection at [1](#0-0)  is skipped, and B is accepted for validation/signing.
5. B goes through validate-ok → pre-commit → threshold. The chainstate re-check (`check_block_against_signer_db_state`) for B (a tenure-change block) only checks B's **parent** tenure tip, not tenure T's own duplicate state.
6. At the pre-commit threshold, the "own-tenure conflict" guard asks the node's `get_tenure_tip(T)`; since A has not yet been globally processed by the node, `tip.height() < block.header.chain_length` may still hold, so the guard does not block signing (`Ok(true)` at [7](#0-6) ).
7. The signer signs B, having already signed A for the same tenure T — a conflicting-block signature.

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

**File:** stacks-signer/src/chainstate/tests/v2.rs (L748-850)
```rust
/// Test that a tenure change proposal is rejected when a locally-accepted
/// (but not globally-accepted) block already exists in the same tenure.
///
/// This is a regression test: previously, the check used
/// `get_last_globally_accepted_block`, which would miss blocks in
/// `LocallyAccepted` or `PreCommitted` state and incorrectly allow
/// a duplicate tenure change.
#[test]
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

**File:** stacks-signer/src/v0/signer.rs (L872-939)
```rust
    /// Check if block should be rejected based on the local view of the sortition state
    /// Will return a BlockRejection if the block is invalid, none otherwise.
    /// This is the pre-global signer state activation path.
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

**File:** docs/signer-flows.md (L425-437)
```markdown
Two things belong to the proposal path only and are **not** re-run at validate-ok
or at signing:

- `validate_tenure_change_payload` rejects with `DuplicateBlockFound` when we
  have already accepted a block in the tenure a tenure-change block is starting.
  v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1
  counts only globally accepted ones (`get_last_globally_accepted_block`).
- the v2 `check_proposal` wrapper checks miner pubkey hash, consensus hash, the
  pox bitvec, and tenure-extend rules before delegating here.

Because the duplicate check never runs again, a block that crosses the pre-commit
threshold long after it was proposed relies on section 5's own-tenure conflict
guard to cover the same ground.
```

**File:** stacks-signer/src/chainstate/mod.rs (L450-477)
```rust
        let tip = match client.get_tenure_tip(tenure_id) {
            Ok(tip) => tip.anchored_header,
            Err(e) => {
                warn!(
                    "Failed to fetch the tenure tip for the parent tenure: {e:?}. Assuming proposal is higher than the parent tenure for now.";
                    "proposed_block_consensus_hash" => %block.header.consensus_hash,
                    "signer_signature_hash" => %block.header.signer_signature_hash(),
                    "parent_tenure" => %tenure_id,
                );
                return Ok(true);
            }
        };
        if let Some(nakamoto_tip) = tip.as_stacks_nakamoto() {
            // If we have seen this block already, make sure its state is updated to globally accepted.
            // Otherwise, don't worry about it.
            if let Ok(Some(mut block_info)) =
                signer_db.block_lookup(&nakamoto_tip.signer_signature_hash())
            {
                if block_info.state != BlockState::GloballyAccepted {
                    if let Err(e) = block_info.mark_globally_accepted() {
                        warn!("Failed to mark block as globally accepted: {e}");
                    } else if let Err(e) = signer_db.insert_block(&block_info) {
                        warn!("Failed to update block info in db: {e}");
                    }
                }
            }
        }
        Ok(tip.height() < block.header.chain_length)
```
