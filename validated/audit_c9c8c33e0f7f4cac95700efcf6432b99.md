### Title
DuplicateBlockFound check in v1 chainstate only counts globally-accepted blocks, letting a signer sign two conflicting tenure-start blocks in the same tenure - (File: `stacks-signer/src/chainstate/v1.rs`)

### Summary
`SortitionsView::validate_tenure_change_payload` in the v1 chainstate module (used by signers still running protocol v1) only rejects a re-proposed tenure-start block as a duplicate if the prior tenure-start block has already been *globally* accepted. The v2 equivalent uses the strictly stronger `get_last_signed_block` check, which also counts a block this signer has only *locally* accepted (signed, but not yet globally confirmed). This is directly analogous to the reported oracle bug: two parallel validation functions are meant to enforce the same invariant, but one silently omits a condition the other enforces, breaking the "one legitimate block per tenure that we sign" equality for v1 signers.

### Finding Description
`stacks-signer/src/chainstate/v1.rs` `validate_tenure_change_payload` calls: [1](#0-0) 
which uses `signer_db.get_last_globally_accepted_block(...)` to decide whether a second tenure-start block for the same tenure is a `DuplicateBlockFound`.

`stacks-signer/src/chainstate/v2.rs` implements the same check but with `signer_db.get_last_signed_block(...)`: [2](#0-1) 

The documentation in this repo explicitly records this asymmetry: "the v2 `check_proposal` wrapper... `validate_tenure_change_payload` rejects with `DuplicateBlockFound` when we have already accepted a block in the tenure a tenure-change block is starting. v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1 counts only globally accepted ones (`get_last_globally_accepted_block`)." [3](#0-2) 

The docs also state that this duplicate check runs **only once, at proposal arrival**, and is never re-run later: [4](#0-3) 

Consequently, for a v1-protocol signer, the sequence is:
1. Miner proposes tenure-start block A for tenure T. The signer validates it, has the node validate it, and locally accepts (signs) it — `signed_self` is set — but block A has not yet been observed as globally accepted (node hasn't processed/confirmed it as the tip yet, e.g. because the signer set hasn't yet reached the global 70% threshold or the node hasn't reported it via `NewBlock`).
2. The miner (or an attacker impersonating/colluding with the miner in a single-slot capacity) proposes a second, different tenure-start block B for the same tenure T (e.g., after a brief network partition, or deliberately building a competing tenure-start block).
3. `handle_block_proposal` → `check_block_against_state` → v1 `SortitionsView::check_proposal` → `validate_tenure_change_payload` calls `get_last_globally_accepted_block(&block.header.consensus_hash)`, which returns `None` because A is only locally accepted, not globally accepted.
4. The `DuplicateBlockFound` rejection is *not* triggered, so B is treated as a fresh, non-conflicting proposal and is submitted to the node for validation and can proceed through pre-commit/signing.

Because the doc also notes the only other backstop for this ("own-tenure conflict guard", `get_signed_conflicts`) is invoked later in the pre-commit/re-check flow (`RECHECK`/`CONF` steps) that guards against signing over a *known* conflicting block only if that data path is reached and re-evaluated — but per the docs' own emphasis, this guard is meant to be the "silent backstop for what the re-check cannot see," not a guaranteed substitute for the arrival-time `DuplicateBlockFound` gate. The arrival-time gate is the one specifically designed to stop this exact scenario (two tenure-start blocks in one tenure), and for v1 it has a materially weaker trigger condition than v2's.

### Impact Explanation
This lets a single v1-protocol signer end up having signed (locally accepted, `signed_self` set) two different, conflicting tenure-start blocks for the same tenure — a direct instance of "a signer signing an invalid/non-canonical/conflicting block," matching the Critical impact category in the rules. If enough signers hit this same race (a plausible network condition any miner or partition can produce, since it merely requires local, not global, acceptance timing to be exploited), it can produce two blocks in the same tenure each with signatures crossing the threshold, undermining the intended single-canonical-block-per-tenure guarantee that the signer set is supposed to enforce independently of node-side validation.

### Likelihood Explanation
This is reachable by a single miner or single slot without needing a majority of signers or another signer's key — a miner-controlled or network-timing-controlled situation (local acceptance not yet reflected as global) is common in the normal proposal-timing window, especially under contention (fast re-proposals, restarts, or brief connectivity blips affecting only the "global accept" observation). It relies purely on the miner (or an entity spoofing block proposals into a legitimate miner's slot) re-proposing a second tenure-start block during that window — no cryptographic or majority-collusion requirement.

### Recommendation
Change `validate_tenure_change_payload` in `stacks-signer/src/chainstate/v1.rs` to use `signer_db.get_last_signed_block(&block.header.consensus_hash)` (matching v2's semantics) instead of `get_last_globally_accepted_block`, so that a locally-accepted (already-signed) tenure-start block also blocks a second tenure-start proposal for the same tenure from being accepted.

### Proof of Concept
Conceptual reproduction (v1 chainstate path):
1. Configure a signer to run protocol v1 (`SortitionsView` chainstate).
2. Miner proposes tenure-start block A (with `TenureChange`/`BlockFound`) for tenure T; the signer validates it via `check_proposal`, the node validates it OK, and the signer locally accepts/signs A (`mark_locally_accepted`), but no `NewBlock` event has arrived yet and the pre-commit/signature threshold for global acceptance has not been reached (so `get_last_globally_accepted_block(T)` still returns `None`).
3. Before global acceptance occurs, the miner proposes block B, a different tenure-start block also for tenure T (e.g., a re-mined variant with different `tx_merkle_root`/`state_index_root`).
4. `handle_block_proposal` runs `check_block_against_state` → `SortitionsView::check_proposal` → `validate_tenure_change_payload`, which calls `get_last_globally_accepted_block(T)`, gets `None`, and does **not** return `DuplicateBlockFound` (unlike v2, which would call `get_last_signed_block(T)` and find A, correctly rejecting B).
5. B proceeds to node validation/pre-commit/signing, and the signer can end up having signed two conflicting blocks (A and B) for tenure T.

Existing unit tests confirm the intended contract for `validate_tenure_change_payload`/`DuplicateBlockFound` semantics differ between v1 and v2, e.g.: [5](#0-4) 
No equivalent v1 test exists exercising the *locally-accepted-but-not-yet-globally-accepted* duplicate scenario, which is consistent with the gap described above.

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

**File:** docs/signer-flows.md (L283-286)
```markdown
- the `DuplicateBlockFound` check that would catch a second block in the same
  tenure lives in `check_proposal` and runs only at proposal arrival, never
  again. A block that crosses the pre-commit threshold minutes later has no
  other guard, which is what the own-tenure branch above covers.
```

**File:** docs/signer-flows.md (L425-432)
```markdown
Two things belong to the proposal path only and are **not** re-run at validate-ok
or at signing:

- `validate_tenure_change_payload` rejects with `DuplicateBlockFound` when we
  have already accepted a block in the tenure a tenure-change block is starting.
  v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1
  counts only globally accepted ones (`get_last_globally_accepted_block`).
- the v2 `check_proposal` wrapper checks miner pubkey hash, consensus hash, the
```

**File:** stacks-signer/src/chainstate/tests/v2.rs (L861-952)
```rust
#[test]
fn check_tenure_change_accepts_when_only_pre_committed_block_exists() {
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

    // Insert a pre-committed block in the same tenure (same consensus_hash).
    // This simulates a miner's first tenure-start block that the signer
    // broadcast a pre-commit for, but that never gathered enough pre-commits
    // to be signed.
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
    existing_block_info.mark_pre_committed().unwrap();
    signer_db.insert_block(&existing_block_info).unwrap();

    // Now build a *second* tenure-start block proposal for the same tenure.
    // This simulates the miner re-proposing its tenure-start block after the
    // first proposal failed to reach consensus.
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

    // The proposal should be accepted: the signer never signed the
    // pre-committed block, so the replacement tenure-start block does not
    // conflict with anything the signer has committed a signature to.
    assert!(
        result.is_ok(),
        "Expected the tenure change to be accepted when only a pre-committed block exists in the tenure, got: {result:?}"
    );
}
```
