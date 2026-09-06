### Title
Signer protocol v1 duplicate-tenure guard checks only globally-accepted state, allowing a stale local acceptance to be bypassed by a duplicate tenure-start proposal - (File: `stacks-signer/src/chainstate/v1.rs`)

### Summary
`SortitionsView::validate_tenure_change_payload` (the v1 chainstate proposal-time duplicate-tenure guard) rejects a tenure-start block only if `SignerDb::get_last_globally_accepted_block` finds a prior block in the same tenure, instead of `SignerDb::get_last_signed_block` (locally *or* globally accepted) as the v2 path uses. This is the direct structural analog of the LSP8 report: a component that is supposed to inherit/reuse the "full" check (all signed states) instead uses a narrower "core" subset (only one state), silently dropping coverage that the sibling implementation (v2) provides.

### Finding Description
`check_proposal` is the gate a v1 signer runs on every incoming block proposal before it will even ask the node to validate it [1](#0-0) . For tenure-change blocks this delegates to `validate_tenure_change_payload`, whose comment states its purpose is to check "if the tenure change confirms the expected parent block ... and if the parent tenure was a valid choice" [2](#0-1) . Its final duplicate-block guard queries only `get_last_globally_accepted_block`: [3](#0-2) 

The v2 equivalent, `GlobalStateView`'s `validate_tenure_change_payload`, was deliberately fixed to query `get_last_signed_block` (locally- or globally-accepted) instead, with an explicit code comment and regression test explaining why the globally-accepted-only check is wrong: [4](#0-3) [5](#0-4) 

The project's own documentation records this v1/v2 asymmetry as a known, intentional difference rather than treating it as fixed everywhere: "v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1 counts only globally accepted ones (`get_last_globally_accepted_block`)" [6](#0-5) . Crucially, this proposal-time duplicate check is never re-run later: "Two things belong to the proposal path only and are **not** re-run at validate-ok or at signing: `validate_tenure_change_payload` rejects with `DuplicateBlockFound`..." [7](#0-6) . `check_block_against_signer_db_state`, which does re-run at validate-ok and at pre-commit time, only re-checks parent-tenure confirmation for tenure-change blocks (`check_tenure_change_confirms_parent`), not the own-tenure duplicate rule [8](#0-7) .

The only remaining backstop is the cross-tenure conflict scan at the pre-commit→signature transition (`get_signed_conflicts`), which is gated on *freshness*: a conflict only blocks signing while it is still fresh, and once stale, the code falls through to checking the block's own tenure against the node's tenure tip - defaulting to **sign** if the node is unreachable or the tenure was never confirmed there [9](#0-8)  and the OWN/TIP branch of the same flow [10](#0-9) .

### Impact Explanation
A v1-protocol signer that has already **locally accepted** (signed) a tenure-start block `A` for tenure `T` — but `A` has not yet reached *global* acceptance (e.g. it's stuck below the 70% signature threshold, or the node hasn't observed enough signatures) — will have `get_last_globally_accepted_block(T)` return `None`. If a second, conflicting tenure-start proposal `B` for the same tenure `T` then arrives, v1's `validate_tenure_change_payload` will pass it through at proposal time (no `DuplicateBlockFound`), because it only checks the globally-accepted predicate, not the locally-accepted one that v2 was fixed to use. The only remaining defense is the freshness-gated conflict scan in `handle_block_pre_commit`/`handle_block_signature`, which is bypassed once `A`'s signature goes stale (`tenure_last_block_proposal_timeout`) and the node either cannot be reached or has not yet recorded `A`'s tenure at that height (both explicitly documented as "assume higher"/"SIGN" fallbacks). In that window the signer can end up placing its signature over two conflicting tenure-start blocks (`A` and `B`) for the same tenure — a direct break of the one-signature-per-tenure/height invariant that the rest of the design (fresh-conflict guard, reorg-permit bookkeeping) is built to enforce.

### Likelihood Explanation
This requires only a single miner slot plus normal network delay/timing (no supermajority of signers, no stolen keys): propose `A`, let it be locally accepted by a v1 signer without reaching global acceptance, wait past `tenure_last_block_proposal_timeout` (or exploit a node RPC hiccup), then propose the duplicate/competing tenure-start block `B` for the same tenure. This is exactly the kind of "one-slot miner (plus gossip)" scenario the task scope calls out, and the codebase's own comments/tests confirm the v2 fix was motivated by recognizing this exact gap — it simply was not carried into v1.

### Recommendation
Change `SortitionsView::validate_tenure_change_payload` in `stacks-signer/src/chainstate/v1.rs` (line 505-506) to query `SignerDb::get_last_signed_block` instead of `SignerDb::get_last_globally_accepted_block`, mirroring the v2 implementation, so a locally-accepted-but-not-yet-globally-accepted block in the same tenure also triggers `RejectReason::DuplicateBlockFound` for v1 signers.

### Proof of Concept
1. Run a signer on protocol v1 (`SortitionStateVersion::V1`).
2. Miner proposes tenure-start block `A` for tenure `T`; signer's own validation succeeds and it reaches the pre-commit weight threshold, so it signs `A` (`mark_locally_accepted`), but `A` never accumulates the 70% signature weight needed for global acceptance (e.g. other signers are slow/offline).
3. Let `tenure_last_block_proposal_timeout` elapse so `A`'s local acceptance is considered "stale" for the freshness-gated conflict guard, and ensure the stacks-node has not recorded `A` as the tenure's confirmed tip (still plausible since `A` is not globally accepted).
4. Miner (or a colluding party) proposes a second tenure-start block `B` also claiming tenure `T` (same `prev_tenure_consensus_hash`, valid parent-tenure choice).
5. `SortitionsView::check_proposal` → `validate_tenure_change_payload` calls `get_last_globally_accepted_block(T)`, which returns `None` (since `A` is only locally accepted), so the duplicate check passes and `B` proceeds to node validation and pre-commit.
6. At the pre-commit/signing decision for `B`, `get_signed_conflicts` finds `A` as a same-height conflict, but because `A`'s freshness window has expired and the node either is unreachable or does not yet report the tenure confirmed at that height, the signer proceeds to sign `B` as well — producing two signatures from the same signer over conflicting tenure-start blocks for tenure `T`.

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L136-143)
```rust
    pub fn check_proposal(
        &mut self,
        client: &StacksClient,
        signer_db: &mut SignerDb,
        block: &NakamotoBlock,
        reset_view_if_wrong_consensus_hash: bool,
        replay_set: ReplayTransactionSet,
    ) -> Result<(), RejectReason> {
```

**File:** stacks-signer/src/chainstate/v1.rs (L457-468)
```rust
    /// in tenure changes, we need to check:
    /// (1) if the tenure change confirms the expected parent block (i.e.,
    /// the last globally accepted block in the parent tenure)
    /// (2) if the parent tenure was a valid choice
    fn validate_tenure_change_payload(
        &self,
        proposed_by: &ProposedBy,
        tenure_change: &TenureChangePayload,
        block: &NakamotoBlock,
        signer_db: &mut SignerDb,
        client: &StacksClient,
    ) -> Result<(), RejectReason> {
```

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

**File:** stacks-signer/src/chainstate/v2.rs (L340-358)
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
        Ok(())
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

**File:** docs/signer-flows.md (L256-267)
```markdown
    SORT -- "canonical, or we never<br/>saved its burn block" --> LIVE{"question 2: does the node's chain<br/>still reach the block itself?<br/>get_tenure_tip(its tenure)"}
    SORT -- "could not ask, or 404 with the<br/>node's tip still below the burn block" --> HOLD1
    LIVE -- "yes — real chain state" --> HOLD1["refuse to sign for now<br/>(may sign once conflict is stale)"]:::hold
    LIVE -- "no, and it was<br/>globally accepted" --> OWN
    LIVE -- "no, only locally accepted<br/>— but above this height" --> OWN
    LIVE -- "no, only locally accepted<br/>and a sibling at this height" --> HOLD1
    LIVE -- "could not ask" --> HOLD1
    FRESH -- "no — all stale" --> OWN{"a conflict in this block's<br/>OWN tenure?"}
    OWN -- yes --> TIP{"own tenure confirmed<br/>at ≥ this height?<br/>get_tenure_tip(own tenure)"}
    TIP -- yes --> HOLD2["refuse to sign"]:::hold
    TIP -- "no — never confirmed" --> SIGN
    TIP -- "node unreachable" --> SIGN
```

**File:** docs/signer-flows.md (L425-431)
```markdown
Two things belong to the proposal path only and are **not** re-run at validate-ok
or at signing:

- `validate_tenure_change_payload` rejects with `DuplicateBlockFound` when we
  have already accepted a block in the tenure a tenure-change block is starting.
  v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1
  counts only globally accepted ones (`get_last_globally_accepted_block`).
```

**File:** stacks-signer/src/v0/signer.rs (L1432-1466)
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
```

**File:** stacks-signer/src/v0/signer.rs (L1799-1840)
```rust
    /// WARNING: This is an incomplete check. Do NOT call this function PRIOR to check_proposal or block_proposal validation succeeds.
    ///
    /// Re-verify a block's chain length against the last signed block within signerdb.
    /// This is required in case a block has been approved since the initial checks of the block validation endpoint.
    fn check_block_against_signer_db_state(
        &mut self,
        stacks_client: &StacksClient,
        proposed_block: &NakamotoBlock,
    ) -> Option<BlockRejection> {
        let signer_signature_hash = proposed_block.header.signer_signature_hash();
        // If this is a tenure change block, ensure that it confirms the correct number of blocks from the parent tenure.
        if let Some(tenure_change) = proposed_block.get_tenure_change_tx_payload() {
            // Ensure that the tenure change block confirms the expected parent block
            match SortitionData::check_tenure_change_confirms_parent(
                tenure_change,
                proposed_block,
                &mut self.signer_db,
                stacks_client,
                self.proposal_config.tenure_last_block_proposal_timeout,
                self.proposal_config.reorg_attempts_activity_timeout,
            ) {
                Ok(true) => return None,
                Ok(false) => {
                    return Some(self.create_block_rejection(
                        RejectReason::SortitionViewMismatch,
                        proposed_block,
                    ))
                }
                Err(e) => {
                    warn!("{self}: Error checking block proposal: {e}";
                        "signer_signature_hash" => %signer_signature_hash,
                        "block_id" => %proposed_block.block_id()
                    );
                    return Some(self.create_block_rejection(
                        RejectReason::ConnectivityIssues(
                            "error checking block proposal".to_string(),
                        ),
                        proposed_block,
                    ));
                }
            }
        }
```
