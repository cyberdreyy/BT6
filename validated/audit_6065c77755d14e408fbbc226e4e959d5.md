### Title
V1 chainstate's tenure-change duplicate check misses locally-accepted blocks, allowing a signer to sign two conflicting tenure-start blocks in the same tenure - (File: `stacks-signer/src/chainstate/v1.rs`)

### Summary
The external report's root cause is a component that behaves differently depending on which "chain"/context it runs in (Ethereum vs OP wrapper), so a code path that is safe in one context silently fails/misbehaves in the other. The stacks-signer chainstate module has the same class of asymmetry between its two protocol-version implementations: `v1.rs` and `v2.rs` perform the tenure-change "duplicate block" guard differently, and only the `v2` path was hardened against a known gap.

### Finding Description
`docs/signer-flows.md` explicitly documents the asymmetry between the two chainstate implementations that guard against a miner re-proposing a competing tenure-start block in an already-started tenure: [1](#0-0) 

i.e. `validate_tenure_change_payload` in v2 counts both locally- and globally-accepted blocks (`get_last_signed_block`), while the v1 implementation only counts globally-accepted blocks (`get_last_globally_accepted_block`). This gap in v2 was itself a real, fixed regression, proven by the dedicated regression test: [2](#0-1) 

The test comment states plainly: "previously, the check used `get_last_globally_accepted_block`, which would miss blocks in `LocallyAccepted` or `PreCommitted` state and incorrectly allow a duplicate tenure change." That defect was fixed for v2, but the v1 code path — which is still live and reachable whenever the network negotiates down to protocol version 1, e.g. through `SortitionStateVersion::from_protocol_version` / `determine_active_signer_protocol_version` — still calls `validate_tenure_change_payload` in `stacks-signer/src/chainstate/v1.rs`: [3](#0-2) 

and this is invoked inside `check_proposal`, the very function that gates whether a signer is willing to sign a proposed block: [4](#0-3) 

The active protocol version — and thus whether the "safe" v2 check or the "leaky" v1 check governs — is a negotiated, majority-driven value that a single lagging/downgraded signer minority (or a rollback/downgrade scenario, as exercised by `downgrade_signer_protocol_version` and `rollover_signer_protocol_version`) can put back in play: [5](#0-4) 

`determine_active_signer_protocol_version` falls back to the *local* version when no majority consensus is reached and the local state does not use global state: [6](#0-5) 

### Impact Explanation
Under the v1 path, `validate_tenure_change_payload`'s duplicate-tenure-start guard only looks at globally-accepted blocks. If a signer (or a subset of signers still on v1) has already `LocallyAccepted` or `PreCommitted` a first tenure-start block for a tenure — but that block has not yet crossed the 70% global-acceptance threshold — a miner can propose a second, conflicting tenure-start block for the *same tenure*. Under v1, this second proposal is not caught by the "already accepted a block in this tenure" check (only the fixed v2 path does that), so a v1-governed signer can end up pre-committing/signing a block that conflicts with a block it (or its peers) already locally accepted in the same tenure. This directly breaks the "one certified/signed tenure-start block per tenure" equality that the state machine is built to guarantee (see `docs/signer-flows.md` sections 3 and 7, which state the duplicate check is only run once, at proposal time, and is not re-checked at validate-ok or signing) — meeting the "signer signing a conflicting block" criticality bar.

### Likelihood Explanation
This requires no majority collusion: it only requires (a) the network's negotiated active signer protocol version to be 1 (via natural downgrade, restart, or a blocking minority still running older signer software, per the `downgrade_signer_protocol_version`/`rollover_signer_protocol_version` tests) and (b) a single miner racing a second tenure-start proposal into the window before the first tenure-start block reaches global acceptance. This is a normal, one-slot-miner-triggerable race rather than an attack needing key compromise or a signer majority.

### Recommendation
Backport the v2 fix to v1: change `validate_tenure_change_payload` in `stacks-signer/src/chainstate/v1.rs` to use the same "locally-or-globally accepted" lookup (`get_last_signed_block`/equivalent) that v2 now uses, instead of `get_last_globally_accepted_block`, so the duplicate-tenure-start guard behaves identically regardless of which protocol version is currently active.

### Proof of Concept
1. Force (or wait for) the network to negotiate `active_signer_protocol_version = 1` (e.g. simulate a blocking minority downgrade as in `downgrade_signer_protocol_version`), landing signers on `SortitionStateVersion::V1` / `chainstate/v1.rs`.
2. Miner proposes tenure-start block `A` for tenure `T`; a signer `mark_locally_accepted`s it but global 70% acceptance is not yet reached (e.g., network is briefly partitioned or slow to gossip acceptances).
3. Miner (or a colluding/faulty miner) proposes a second tenure-start block `B` for the same tenure `T`, with a different parent-tenure-end/transaction set.
4. `SortitionsView::check_proposal` in `v1.rs` calls `validate_tenure_change_payload`, which only queries `get_last_globally_accepted_block`; since `A` is only locally accepted, the duplicate check does not fire, and `B` is not rejected with `DuplicateBlockFound`.
5. The signer proceeds to pre-commit/sign `B`, producing signer signatures over two conflicting blocks for the same tenure — reproducing, in the signer's chainstate logic, exactly the "same interface, different behavior per version/context" bug class from the external report.

### Citations

**File:** docs/signer-flows.md (L428-431)
```markdown
- `validate_tenure_change_payload` rejects with `DuplicateBlockFound` when we
  have already accepted a block in the tenure a tenure-change block is starting.
  v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1
  counts only globally accepted ones (`get_last_globally_accepted_block`).
```

**File:** stacks-signer/src/chainstate/tests/v2.rs (L746-850)
```rust
}

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

**File:** stacks-signer/src/chainstate/v1.rs (L134-143)
```rust
impl SortitionsView {
    /// Apply checks from the SortitionsView on the block proposal.
    pub fn check_proposal(
        &mut self,
        client: &StacksClient,
        signer_db: &mut SignerDb,
        block: &NakamotoBlock,
        reset_view_if_wrong_consensus_hash: bool,
        replay_set: ReplayTransactionSet,
    ) -> Result<(), RejectReason> {
```

**File:** stacks-signer/src/chainstate/v1.rs (L319-326)
```rust
        if let Some(tenure_change) = block.get_tenure_change_tx_payload() {
            self.validate_tenure_change_payload(
                &proposed_by,
                tenure_change,
                block,
                signer_db,
                client,
            )?;
```

**File:** stacks-node/src/tests/signer/v0/mod.rs (L8223-8232)
```rust
/// Tests that signers negotiate their **active** signer protocol version based on the
/// **majority of locally supported** signer protocol versions.
///
/// Scenario (10 signers):
/// 1) Baseline: all signers start on `SUPPORTED_SIGNER_PROTOCOL_VERSION` and can sign blocks.
/// 2) Downgrade 30%: non-blocking minority downgraded. Stays at `SUPPORTED_SIGNER_PROTOCOL_VERSION`.
/// 3) Downgrade 40%: blocking minority downgraded. Downgrades to `SUPPORTED_SIGNER_PROTOCOL_VERSION - 1`.
/// 4) Upgrade 70%: majority upgraded. Upgrades back to `SUPPORTED_SIGNER_PROTOCOL_VERSION`.
#[test]
#[ignore]
```

**File:** stacks-signer/src/v0/signer.rs (L783-807)
```rust
    fn determine_active_signer_protocol_version(&mut self) -> Option<SortitionStateVersion> {
        let local_version = self.get_signer_protocol_version();
        if let Ok(update) = self
            .local_state_machine
            .try_into_update_message_with_version(local_version)
        {
            self.global_state_evaluator
                .insert_update(self.stacks_address.clone(), update);
        };
        let local_state_version = SortitionStateVersion::from_protocol_version(local_version);
        self
            .global_state_evaluator
            .determine_latest_supported_signer_protocol_version().map(|version| {
                SortitionStateVersion::from_protocol_version(version)
            })
            .or_else(|| {
                // Don't default if we are in a global consensus activation state as its pointless
                if local_state_version.uses_global_state() {
                    None
                } else {
                    warn!("{self}: No consensus on signer protocol version. Defaulting to local state version: {local_version}.");
                    Some(local_state_version)
                }
            })
    }
```
