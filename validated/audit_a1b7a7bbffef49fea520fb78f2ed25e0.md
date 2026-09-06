### Title
v1 `validate_tenure_change_payload` uses `get_last_globally_accepted_block` instead of `get_last_signed_block`, letting a signer be coaxed into signing a second, conflicting tenure-start block - ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
`v1::SortitionsView::validate_tenure_change_payload` guards against duplicate tenure-start blocks by checking only `signer_db.get_last_globally_accepted_block`, whereas the parallel v2 implementation was fixed to check `signer_db.get_last_signed_block` (locally OR globally accepted). This means that when a signer has already locally accepted (signed) a block in the current tenure but global acceptance has not yet been observed, the v1 duplicate check finds nothing and lets a second, conflicting tenure-start block proceed to signing.

### Finding Description
The intended equality is: "has this signer already signed a block in this tenure?" (locally OR globally accepted), which is exactly what the v2 path enforces at `stacks-signer/src/chainstate/v2.rs:344-345` via `signer_db.get_last_signed_block(&block.header.consensus_hash)`. That comment even states the rationale explicitly: only blocks the signer *has signed* (locally or globally accepted) should count, since a pre-commit carries no signature and is safely supersedable.

The v1 path breaks this equality. In `validate_tenure_change_payload` (`stacks-signer/src/chainstate/v1.rs:505-518`), the duplicate check calls:
```
let last_in_current_tenure = signer_db
    .get_last_globally_accepted_block(&block.header.consensus_hash)...
if let Some(last_in_current_tenure) = last_in_current_tenure {
    ...
    return Err(RejectReason::DuplicateBlockFound);
}
```
This only detects blocks that have reached *global* acceptance (i.e., a threshold of signers has signed), not blocks that this signer has locally accepted/signed but which haven't yet crossed the global threshold. `get_last_globally_accepted_block` in `stacks-signer/src/signerdb.rs` filters by `BlockState::GloballyAccepted`, skipping `LocallyAccepted` (and `PreCommitted`) states.

Exploit flow (attacker = the single miner slot holder for the tenure, no privileged role required):
1. Attacker wins a sortition slot and gossips tenure-start `BlockProposal` A for tenure T. The victim signer locally accepts and signs A (`mark_locally_accepted`), but global acceptance (threshold of signatures across the signer set) has not yet been observed by the victim (e.g., other signers are slow, or attacker withholds broadcasting the aggregated signatures).
2. Attacker crafts a second tenure-start `BlockProposal` B for the same tenure T (same `tenure_consensus_hash`/`prev_tenure_consensus_hash`), with different transactions, and gossips it to the same victim signer.
3. The victim signer's `check_proposal` → `validate_tenure_change_payload` calls `get_last_globally_accepted_block`, which returns `None` (A is only locally accepted, not yet globally accepted), so the duplicate-block guard is bypassed and the signer proceeds to validate and sign B.
4. The signer has now signed two conflicting tenure-start blocks (A and B) at the same height/tenure, exactly the equivocation the duplicate-block guard exists to prevent.

Existing guards do not close this gap for v1: `check_tenure_change_confirms_parent` only validates the *parent*-tenure confirmation, not sibling conflicts within the *same* tenure; and per `docs/signer-flows.md:425-437`, the duplicate check "never runs again" at validate-ok or signing time, so there is no second chance to catch it later in the v1 pipeline.

This is exactly analogous to the bug that was already identified and fixed for v2 (see `stacks-signer/CHANGELOG.md:48`: "When checking tenure change blocks, ensure there are no locally accepted blocks in the tenure, not just globally accepted blocks."), and confirmed by the existing regression test `check_tenure_change_rejects_when_locally_accepted_block_exists` in `stacks-signer/src/chainstate/tests/v2.rs:756-850`. That fix and test exist only for v2; `stacks-signer/src/chainstate/tests/v1.rs` has no equivalent test, and v1's `validate_tenure_change_payload` still contains the vulnerable pre-fix logic.

### Impact Explanation
This breaks the UNIQUENESS/equivocation-guard safety property: a signer can be made to produce two valid signatures over two conflicting tenure-start blocks at the same height in the same tenure before global acceptance is observed. If enough signers are coaxed the same way (each needs only to receive both proposals in the right order relative to their own local acceptance), the signature weight can split between two candidate blocks, risking a chain split/equivocation at that height — a Critical chain-safety violation ("a signer signing an invalid, non-canonical, or conflicting block"). The attack is repeatable each time the attacker wins a miner slot and can craft a second competing tenure-start proposal before global acceptance propagates.

### Likelihood Explanation
Preconditions: the target signer must be running the v1 chainstate path (used when the local/global signer-state-machine activation version has not yet reached the v0/v2 gating, i.e., pre-`GLOBAL_SIGNER_STATE_ACTIVATION_VERSION`), and the attacker must win a single miner slot for tenure T. The attacker only needs the ability to craft two `BlockProposal`s and gossip them — well within the unprivileged, single-miner-slot threat model. No majority of signers, no node-operator access, and no auth token are required. The precondition of "victim has locally but not yet globally accepted the first block" is a normal, expected timing window (network propagation delay between local signature and reaching the global threshold), not a contrived edge case, making this readily reproducible.

### Recommendation
Change `stacks-signer/src/chainstate/v1.rs`'s `validate_tenure_change_payload` to call `signer_db.get_last_signed_block(&block.header.consensus_hash)` instead of `get_last_globally_accepted_block`, mirroring the v2 fix at `stacks-signer/src/chainstate/v2.rs:344-345`, so the duplicate-block guard fires on any locally- or globally-accepted (i.e., signed) block in the tenure, not solely globally-accepted ones. Add a v1-specific regression test analogous to `check_tenure_change_rejects_when_locally_accepted_block_exists`.

### Proof of Concept
```rust
// stacks-signer/src/chainstate/tests/v1.rs

#[test]
fn check_tenure_change_rejects_when_locally_accepted_block_exists_v1() {
    let MockServerClient { server, client: stacks_client, config: _ } = MockServerClient::new();

    let (_stacks_client, mut signer_db, block_sk, mut view, mut block) =
        setup_test_environment(function_name!());

    block.header.consensus_hash = view.cur_sortition.data.consensus_hash.clone();
    let parent_block_header = make_parent_header_meta(&block_sk, &mut block);
    let response = crate::client::tests::build_get_tenure_tip_response(&parent_block_header);

    // Insert a block that has been LOCALLY accepted (signed) but not yet
    // globally accepted, in the same tenure.
    let existing_block_proposal = BlockProposal {
        block: NakamotoBlock::new(
            NakamotoBlockHeader {
                version: 1,
                chain_length: 10,
                burn_spent: 10,
                consensus_hash: view.cur_sortition.data.consensus_hash.clone(),
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
    existing_block_info.mark_locally_accepted(false).unwrap(); // signed, not globally accepted
    signer_db.insert_block(&existing_block_info).unwrap();

    // Build a second, conflicting tenure-start proposal for the SAME tenure.
    let tenure_change_payload = make_tenure_change_payload_for(&view.cur_sortition, &block);
    let tenure_change_tx = make_tenure_change_tx(tenure_change_payload);
    let coinbase_tx = make_coinbase_tx();
    *block.executed_and_skipped_txs_mut() = vec![tenure_change_tx, coinbase_tx];
    block.header.sign_miner(&block_sk).unwrap();

    let j = std::thread::spawn(move || {
        view.check_proposal(&stacks_client, &mut signer_db, &block, false, ReplayTransactionSet::none())
    });
    crate::client::tests::write_response(server, response.as_bytes());
    let result = j.join().unwrap();

    // BEFORE FIX: this wrongly returns Ok(()) because get_last_globally_accepted_block
    // does not see the LocallyAccepted block.
    // AFTER FIX (using get_last_signed_block): must return DuplicateBlockFound.
    assert!(
        matches!(result, Err(RejectReason::DuplicateBlockFound)),
        "Expected DuplicateBlockFound for a conflicting tenure-start block when a locally-accepted \
         (signed) block already exists in the tenure, got: {result:?}"
    );
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** stacks-signer/CHANGELOG.md (L43-48)
```markdown
### Fixed

* Fix duplicated binary name when running `stacks-signer --version` cli command
* Fixed an issue in the signer where it would return early if it detected a message from an unrecognized signer.
* Fixed flakiness in `check_capitulate_miner_view` test.
* When checking tenure change blocks, ensure there are no locally accepted blocks in the tenure, not just globally accepted blocks.
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
