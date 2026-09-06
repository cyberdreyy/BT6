### Title
v1 `validate_tenure_change_payload` uses `get_last_globally_accepted_block` instead of `get_last_signed_block`, letting a signer sign a duplicate tenure-start block - (File: `stacks-signer/src/chainstate/v1.rs`)

### Summary
`SortitionsView::validate_tenure_change_payload` in v1 (`stacks-signer/src/chainstate/v1.rs:505-506`) checks for an already-existing tenure-start block using `signer_db.get_last_globally_accepted_block`, while the v2 equivalent (`stacks-signer/src/chainstate/v2.rs:344-345`) correctly uses `get_last_signed_block`, which additionally covers `LocallyAccepted` blocks. Because `LocallyAccepted` in this codebase already means the block carries a valid ≥70%-weight aggregate signature (it is only waiting on the node to process it, per `docs/signer-flows.md:367,428-431`), a v1 signer that has already signed such a block will not recognize its own prior signature as a duplicate and can be tricked into countersigning a second, competing tenure-start block for the same tenure.

### Finding Description
The invariant that should hold identically regardless of protocol version is: *"if I (this signer) have already put a signature over a tenure-start block for consensus_hash X, I must reject any other tenure-start block for X with `DuplicateBlockFound`."* This is enforced by the query `signer_db.get_last_signed_block`/`get_last_globally_accepted_block` in `validate_tenure_change_payload`.

- v2 (`stacks-signer/src/chainstate/v2.rs:340-357`): queries `get_last_signed_block`, which returns blocks in `GloballyAccepted` **or** `LocallyAccepted` state [1](#0-0) . The v2 regression test explicitly documents that using only `get_last_globally_accepted_block` "would miss blocks in `LocallyAccepted` or `PreCommitted` state and incorrectly allow a duplicate tenure change" [2](#0-1) .
- v1 (`stacks-signer/src/chainstate/v1.rs:505-518`) still performs exactly the flawed query the v2 test warns about: `get_last_globally_accepted_block(&block.header.consensus_hash)` [3](#0-2) .

`LocallyAccepted` is reached once the signer observes ≥70% signed weight for a block and marks it accordingly, before the node has processed/confirmed it (`mark_locally_accepted`, `broadcast_signed_block` in the block-response flow) [4](#0-3) . A signer's own record of having signed a block therefore lands in `LocallyAccepted`, not `GloballyAccepted`, until the node catches up. On a v1 signer, `get_last_globally_accepted_block` misses this record.

Exploit flow: the miner who wins the tenure's sortition slot (a party that can legitimately author competing candidate blocks for that tenure, per the threat model) proposes tenure-start block A, gathers enough signatures across the fleet to reach the 70% local-accept threshold, and some v1 signers mark A as `LocallyAccepted` in their own `signer_db`. The same miner then proposes a second, competing tenure-start block B for the identical `consensus_hash`. On the v1 signer's `check_proposal → validate_tenure_change_payload`, `get_last_globally_accepted_block` returns `None` (A is only locally accepted, not yet globally accepted), so the duplicate check at `chainstate/v1.rs:510` is skipped and the proposal is not rejected with `DuplicateBlockFound`. That v1 signer can proceed to also sign B, producing two conflicting signed tenure-start blocks for the same tenure from the same signer — an equivocation that a v2 signer, using `get_last_signed_block`, would correctly refuse (`DuplicateBlockFound`).

Existing guards that fail to catch this: `check_tenure_change_confirms_parent` and `check_parent_tenure_choice` (both run earlier in `validate_tenure_change_payload`) validate the *parent* tenure linkage, not whether *this* tenure already has a signed block — they do not substitute for the duplicate check. The duplicate check is explicitly a proposal-time-only check that is never re-run at validate-ok or signing time (`docs/signer-flows.md:425-437`), so there is no second chance to catch it.

### Impact Explanation
This breaks the **uniqueness** safety property: the same signer, running the v1 chainstate implementation, can be induced to place its signature (an equivocation) on two different tenure-start blocks for the same tenure. If enough v1-running signers are tricked this way, a second block for that tenure can independently gather its own ≥70% signature weight, i.e., two conflicting, both "signed" tenure-start blocks exist simultaneously. This is a chain-safety violation ("signing a conflicting block"), matching the Critical category. It is repeatable by the same attacker in any tenure they win a sortition slot for, as long as the target signers are still negotiated onto `SortitionStateVersion::V1`.

### Likelihood Explanation
Preconditions:
- The active negotiated signer-protocol version for the affected signers must resolve to `SortitionStateVersion::V1` (i.e., below `GLOBAL_SIGNER_STATE_ACTIVATION_VERSION`), which is a real, tested state — either pre-upgrade baseline or during a downgrade/negotiation transition (`downgrade_signer_protocol_version` test in `stacks-node/src/tests/signer/v0/mod.rs:8223-8282`, `SortitionStateVersion::from_protocol_version` in `stacks-signer/src/chainstate/mod.rs:532-540`).
- Block A must reach ≥70% local-accept signature weight before the miner submits block B, and enough time before global acceptance/node confirmation.
- Attacker cost: exactly one miner slot (own BTC) plus the ability to gossip two competing `BlockProposal`s — no signer compromise, no majority of signers, no auth_token.
- Repeatable per tenure the attacker wins.

I could not fully verify from the available index whether `GLOBAL_SIGNER_STATE_ACTIVATION_VERSION` is set such that V1 is still reachable on current mainnet/testnet deployments, or whether it is retained purely for historical/compat reasons and is provably unreachable in practice; this affects the real-world likelihood but not the code-level correctness of the finding.

### Recommendation
Change `stacks-signer/src/chainstate/v1.rs`'s `validate_tenure_change_payload` to use `signer_db.get_last_signed_block` instead of `get_last_globally_accepted_block`, matching the v2 implementation, so the duplicate-tenure-start check considers `LocallyAccepted` (already-signed) blocks in both protocol versions.

### Proof of Concept
Add a test to `stacks-signer/src/chainstate/tests/v1.rs` mirroring the existing v2 regression test `check_tenure_change_rejects_when_locally_accepted_block_exists` in `stacks-signer/src/chainstate/tests/v2.rs:756-850`:

1. Build identical fixtures via `setup_test_environment` for both `chainstate/tests/v1.rs` and `chainstate/tests/v2.rs`.
2. Insert a `BlockInfo` for a first tenure-start block, call `mark_locally_accepted(false)` (not globally accepted), and `signer_db.insert_block(...)`.
3. Construct a second, competing tenure-start `BlockProposal` (same `consensus_hash`, tenure-change with `cause = BlockFound` + coinbase) as in the v2 test.
4. Call `sortitions_view.check_proposal(&stacks_client, &mut signer_db, &block, ...)` on both the v1 `SortitionsView` and v2 `GlobalStateView`.
5. Assert:
   - v2: `assert!(matches!(result, Err(RejectReason::DuplicateBlockFound)))` (already passes today).
   - v1 (new test): assert the same `Err(RejectReason::DuplicateBlockFound)` — this assertion will **fail** against current `v1.rs` (returns `Ok(())`), proving the discrepancy.

### Citations

**File:** stacks-signer/src/signerdb.rs (L1564-1585)
```rust
    /// Return the last signed block in a tenure (identified by its consensus hash).
    /// A block is considered signed if it is locally or globally accepted. Blocks that
    /// have only been pre-committed are excluded, because a pre-commit does not put a
    /// signature over the block and may be safely superseded by a competing proposal.
    ///
    /// This answers "what is the tenure's signed tip?", a different question from
    /// [`SignerDb::has_signed_block_in_tenure`]'s "does a signature bind us to this tenure?",
    /// which is why the predicates deliberately differ on rejected blocks (see there).
    pub fn get_last_signed_block(
        &self,
        tenure: &ConsensusHash,
    ) -> Result<Option<BlockInfo>, DBError> {
        let query = "SELECT block_info FROM blocks WHERE consensus_hash = ?1 AND state IN (?2, ?3) ORDER BY stacks_height DESC LIMIT 1";
        let args = params![
            tenure,
            &BlockState::GloballyAccepted.to_string(),
            &BlockState::LocallyAccepted.to_string(),
        ];
        let result: Option<String> = query_row(&self.db, query, args)?;

        try_deserialize(result)
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

**File:** docs/signer-flows.md (L363-372)
```markdown
    OLD -- no --> GRP{"signed_group already set?"}
    GRP -- yes --> N1(["done"])
    GRP -- no --> TALLY{"signature weight ≥ 70%?"}
    TALLY -- no --> N2(["wait for more"])
    TALLY -- yes --> BCAST["mark_locally_accepted(group),<br/>broadcast_signed_block →<br/>handle_post_block (push to node)"]:::good
    KIND -- "Rejected" --> HBR["handle_block_rejection:<br/>verify, store via<br/>add_block_rejection_signer_addr"]
    HBR --> RT{"rejection weight makes<br/>70% approval impossible?"}
    RT -- no --> N3(["wait"])
    RT -- yes --> GREJ["mark_globally_rejected;<br/>pre-global-state versions also<br/>update miner status"]:::bad
    BCAST --> NB["node processes block →<br/>NewBlock event →<br/>mark_globally_accepted"]:::good
```
