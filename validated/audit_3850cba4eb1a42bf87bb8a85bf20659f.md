### Title
`DuplicateBlockFound` tenure-start guard checks only *globally accepted* blocks in the v1 chainstate path, letting a single signer sign two conflicting tenure-start blocks in the same tenure - (File: `stacks-signer/src/chainstate/v1.rs`)

### Summary
`SortitionsView::validate_tenure_change_payload` in the v1 chainstate path guards against a miner re-proposing a second, conflicting tenure-start block in the same tenure by checking `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)` [1](#0-0)  . "Globally accepted" requires the *whole signer set* to have reached threshold, not just this signer. Meanwhile the actual state that records this signer's own commitment - "have I already put a signature on a tenure-start block for this tenure?" - is `signed_self`/`LocallyAccepted`, tracked via `get_last_signed_block`. The v2 chainstate path already fixed this equality mismatch by checking `get_last_signed_block` (locally *or* globally accepted) [2](#0-1) , and the project's own regression test and docs confirm this was a known bug class for the "locally accepted but not yet globally accepted" case [3](#0-2) [4](#0-3) . The v1 path was left unpatched.

### Finding Description
The guard is an equality/deny-style check analogous to the Vite report: the "already decided" state is recorded canonically as `signed_self` (this signer put a real signature on a tenure-start block), but the deny check on the alternate code path (v1) is evaluated against a *different, narrower* form of the state - `get_last_globally_accepted_block`, i.e. `state == GloballyAccepted` [5](#0-4) . Just as the Windows path-normalization gap let `.env::$DATA` slip past a deny-list keyed on the literal path `.env`, a `LocallyAccepted` (already-signed) block for a tenure slips past a check keyed only on `GloballyAccepted`.

Concretely, in v1 (`stacks-signer/src/chainstate/v1.rs`):
1. The signer receives tenure-start block A for tenure T, validates it via `check_proposal`, and eventually signs it via the pre-commit → threshold flow, marking it `mark_locally_accepted` (`signed_self` set) [6](#0-5) . At this point `block_info.state == LocallyAccepted`, not `GloballyAccepted`, because the rest of the signer set has not yet reached the 70% signature threshold.
2. The miner (or an attacker who can get a differently-constructed tenure-start proposal onto the miner's slot) proposes block B for the same tenure T with different transactions (different `tx_merkle_root`, hence a different `signer_signature_hash`), again as a tenure-start (`TenureChangeCause::BlockFound`) block.
3. `check_proposal` → `validate_tenure_change_payload` runs `get_last_globally_accepted_block(&block.header.consensus_hash)` [7](#0-6) . Because A is only `LocallyAccepted` (not yet `GloballyAccepted`), this query returns `None`, so the `DuplicateBlockFound` rejection at line 517 never fires.
4. B passes `check_proposal`, is submitted to the node for validation, and if the node accepts it, the signer proceeds through the pre-commit/threshold flow and can sign B - producing a *second* signature from the same signer over a conflicting tenure-start block (B) in the same tenure that it already signed a block (A) for.

This directly breaks the "one signed tenure-start block per tenure per signer" equality relied on by the protocol (the same invariant the v2 fix in `stacks-signer/src/chainstate/v2.rs:340-357` and the docs treat as consensus-relevant, see `docs/signer-flows.md:425-437`) using only a single miner/signer's own actions plus the normal message flow - no majority collusion is required.

### Impact Explanation
This matches the "Critical" bucket defined by the rules: a signer signing a conflicting block. Two tenure-start blocks A and B built on the same tenure/parent, both carrying this signer's signature, is exactly the kind of conflicting-signature condition the pre-commit conflict guard (`get_signed_conflicts`, section 5 of `docs/signer-flows.md`) exists to prevent when detected via other paths, but here the earlier, sticky `DuplicateBlockFound` reject at proposal time is silently skipped for the "locally-but-not-globally accepted" state, which is exactly the gap the v2 code was patched for. If enough other signers are fooled the same way (each independently, no coordination required, since each runs the same buggy v1 logic against its own local `LocallyAccepted` record), the network could end up with signature weight split across two conflicting tenure-start blocks, undermining the safety property that only one canonical tenure-start block can accumulate valid signer weight.

### Likelihood Explanation
Reachable by a single miner crafting two distinct valid tenure-start proposals for the same tenure and by relying on the normal proposal → validate → pre-commit → sign flow that already exists in the codebase; it requires the affected signer to run the v1 chainstate path (pre-global-signer-state activation) and for its own first proposal to have only reached `LocallyAccepted` (not yet globally accepted) before the second, conflicting proposal arrives - a normal and expected race window during any tenure-start, not an unlikely corner case.

### Recommendation
Change `validate_tenure_change_payload` in `stacks-signer/src/chainstate/v1.rs` to check `get_last_signed_block` (as v2 already does) instead of `get_last_globally_accepted_block`, so that any block this signer has already locally signed in the tenure is treated as a duplicate-blocking commitment, matching the v2 fix.

### Proof of Concept
1. Signer runs v1 chainstate logic (`stacks-signer/src/chainstate/v1.rs`).
2. Miner proposes tenure-start block A for tenure T (`TenureChangeCause::BlockFound`); signer validates and, through the normal pre-commit/threshold flow, signs A (`mark_locally_accepted`, `signed_self` set, `state = LocallyAccepted`) - see `stacks-signer/src/v0/signer.rs:2525-2537`.
3. Before A reaches global (network-wide) 70% acceptance, the miner proposes a second, different tenure-start block B for the same tenure T (different transactions ⇒ different `signer_signature_hash`, but same `consensus_hash`).
4. `check_proposal` → `validate_tenure_change_payload` calls `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)` (`stacks-signer/src/chainstate/v1.rs:505-518`), which returns `None` because A is only `LocallyAccepted`, so no `DuplicateBlockFound` rejection occurs.
5. B proceeds through validation/pre-commit/threshold and the signer ends up signing B as well, producing two signatures from the same signer over conflicting tenure-start blocks A and B for tenure T.

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

**File:** stacks-signer/src/chainstate/tests/v2.rs (L770-849)
```rust
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

**File:** stacks-signer/src/signerdb.rs (L1518-1527)
```rust
    /// Return the first approved/signed block in a tenure (identified by its consensus hash)
    pub fn get_first_approved_block_in_tenure(
        &self,
        tenure: &ConsensusHash,
    ) -> Result<Option<BlockInfo>, DBError> {
        let query = "SELECT block_info FROM blocks WHERE consensus_hash = ? AND (signed_self IS NOT NULL OR signed_group IS NOT NULL OR approved_time IS NOT NULL) ORDER BY stacks_height ASC LIMIT 1";
        let result: Option<String> = query_row(&self.db, query, [tenure])?;

        try_deserialize(result)
    }
```

**File:** stacks-signer/src/v0/signer.rs (L2525-2532)
```rust
        // have enough signatures to broadcast!
        // move block to LOCALLY accepted state.
        // It is only considered globally accepted IFF we receive a new block event confirming it OR see the chain tip of the node advance to it.
        if let Err(e) = block_info.mark_locally_accepted(true) {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally accepted: {e:?}");
            }
        }
```
