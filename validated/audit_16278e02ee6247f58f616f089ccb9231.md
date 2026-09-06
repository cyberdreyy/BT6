## Analog Found

### Title
v1 signers admit a duplicate tenure-start proposal because `validate_tenure_change_payload` matches only on `GloballyAccepted` state while the block was already matched (signed) as `LocallyAccepted` - ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
The external report's bug class is: a two-sided equality check (matching address on chain A vs chain B) is only ever *written* on one side, so a legitimate later request that is truly "already matched" slips through the check because the check looks at the wrong/narrower predicate. The stacks-signer v1 chainstate module has the same shape: the duplicate-tenure-start guard checks only the strict `GloballyAccepted` state instead of "any state that carries our signature" (`LocallyAccepted` or `GloballyAccepted`), even though `LocallyAccepted` is exactly the state produced when *this* signer has already put a signature over a block in that tenure.

### Finding Description
`SortitionsView::validate_tenure_change_payload` (v1) rejects a second tenure-start block for a tenure with `RejectReason::DuplicateBlockFound` only if a **globally accepted** block already exists in that tenure: [1](#0-0) 

Compare this to the v2 implementation, which was fixed to use `get_last_signed_block` (i.e., `LocallyAccepted` OR `GloballyAccepted`): [2](#0-1) 

The v2 change was explicitly a regression fix, documented by a dedicated test that states the old (v1-style) behavior "would miss blocks in `LocallyAccepted` or `PreCommitted` state and incorrectly allow a duplicate tenure change": [3](#0-2) 

`LocallyAccepted` is reached the moment *this* signer decides to sign, which only requires crossing the **pre-commit weight threshold** (≥70% of pre-commit weight, a lower bar than the signature/acceptance threshold) — not global signer-set agreement: [4](#0-3) [5](#0-4) 

So a v1 signer can already hold `signed_self` over tenure-start block **A** while A never reaches the group signature threshold (e.g. network/timing issues prevent enough other signers from signing). `get_last_globally_accepted_block` then still returns `None` for that tenure, so a second tenure-start proposal **B** for the *same* tenure sails through `check_proposal`'s duplicate check — exactly the "matched on one predicate, unmatched on the narrower one" gap in the external report.

The only remaining backstop is the pre-commit-time conflict guard (`get_signed_conflicts` / `conflict_still_blocks`), which does treat `LocallyAccepted` as a live conflict — but only while it is **fresh** (within `tenure_last_block_proposal_timeout`): [6](#0-5) 

Once that freshness window elapses, the guard falls back to asking the node whether it reaches the conflicting block. Since A was never globally accepted, the node never received it, so the node has no record and the code path returns "never confirmed" → **SIGN is allowed**: [7](#0-6) [8](#0-7) 

The net effect: after the freshness timeout passes, the same v1 signer can end up placing its signature over **both** sibling tenure-start blocks A and B in the same tenure — a direct equivocation/double-sign, breaking the "one signature per height/tenure" invariant that the pre-commit conflict guard exists to protect.

### Impact Explanation
This is a **Critical** finding per the rubric: a signer ends up signing two conflicting (non-canonical/sibling) blocks for the same tenure. If enough v1 signers independently hit the same timing window (each locally-accepted a different sibling and let it go stale), a genuine consensus fork/equivocation at the signer layer becomes possible, undermining the one-signature-per-height guarantee that the whole pre-commit/conflict-guard machinery in `signer.rs` is designed to enforce.

### Likelihood Explanation
Triggering requires only a single miner (no majority signer collusion, no key compromise): propose tenure-start block A, wait long enough for A to reach the pre-commit threshold at some signers (`LocallyAccepted`, `signed_self` set) but fail to reach the global signature threshold, then re-propose a sibling tenure-start block B for the same tenure after `tenure_last_block_proposal_timeout` has elapsed. This is squarely within a single miner's control given normal timing variance across the signer set. The only precondition is that the affected signer(s) are running/negotiated to protocol v1 (mixed-fleet fallback), which the codebase explicitly supports and keeps live (`docs/signer-flows.md` "outdated-peer fallback keeps mixed-version fleets live").

### Recommendation
In `stacks-signer/src/chainstate/v1.rs::validate_tenure_change_payload`, replace the `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)` duplicate check with `signer_db.get_last_signed_block(&block.header.consensus_hash)`, mirroring the v2 fix, so the check matches on "any block we have signed" (`LocallyAccepted` or `GloballyAccepted`) rather than only the strict global-acceptance state.

### Proof of Concept
1. Miner proposes tenure-start block A for tenure T.
2. Enough peers pre-commit to A that this v1 signer crosses the 70% **pre-commit** weight threshold and signs A → `BlockInfo.state = LocallyAccepted`, `signed_self` set (per `stacks-signer/src/v0/signer.rs:1340-1366` / `signerdb.rs` state predicates).
3. A never accumulates the full group **signature** threshold (e.g. other signers are slow/partitioned), so it never reaches `GloballyAccepted`.
4. `tenure_last_block_proposal_timeout` elapses.
5. Miner proposes sibling tenure-start block B for the same tenure T. `validate_tenure_change_payload` (v1) calls `get_last_globally_accepted_block(T)` → returns `None` → the `DuplicateBlockFound` check is skipped and B passes `check_proposal` (`stacks-signer/src/chainstate/v1.rs:505-518`).
6. B is validated and reaches the pre-commit threshold. `handle_block_pre_commit` calls `get_signed_conflicts`, finds A as a conflict, but A is now stale (`last_endorsed <= freshness_cutoff`), so freshness filtering drops it from the blocking set (`stacks-signer/src/v0/signer.rs:1403-1421`, `1137-1206`); since the node never had A (never globally accepted), `get_tenure_tip` shows no confirmed block for tenure T at/above B's height, so the signer proceeds to sign B.
7. This signer now holds signatures over both A and B — two conflicting/sibling blocks in the same tenure T.

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

**File:** stacks-signer/src/v0/signer.rs (L1340-1366)
```rust
        // The chain and signer db state may have changed materially since this block passed the
        // proposal-time checks (e.g. between validation and reaching the pre-commit threshold we
        // may have signed a block that this one would reorg). Re-run the chainstate checks
        // before putting a signature over the block, and respond with a rejection if they no
        // longer pass, just as the block validation response handler does.
        if let Some(block_rejection) =
            self.check_block_against_signer_db_state(stacks_client, &block_info.block)
        {
            warn!(
                "{self}: Reached the pre-commit threshold for a block, but it no longer passes the chainstate checks. Rejecting.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "reject_code" => %block_rejection.reason_code,
                "reject_reason" => &block_rejection.reason,
            );
            if let Err(e) = block_info.mark_locally_rejected() {
                if !block_info.has_reached_consensus() {
                    warn!("{self}: Failed to mark block as locally rejected: {e:?}");
                }
            };
            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
            self.handle_block_rejection(&block_rejection, sortition_state);
            self.send_block_response(&block_info.block, block_rejection.into());
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1368-1421)
```rust
        // A pre-commit may be superseded by a competing proposal at the same height (e.g. a
        // re-proposed tenure-start block after the first failed to reach consensus), but a
        // signature must not be superseded while it's still "fresh". A signed block at the
        // same or higher height in ANY tenure is a conflict: two blocks at the same height are
        // siblings no matter which tenure they belong to (e.g. the next tenure's tenure-start
        // block conflicts with the current tenure's block at the same height). Blocks in
        // tenures whose reorg we sanctioned under the reorg-timing rules are excluded, but
        // only while the sortition the permit was granted to is still canonical
        // (`check_parent_tenure_choice` records the permit, `reorg_permit_stands` re-derives
        // its validity from the node); every other question about whether a conflict is
        // still live is derived from the node in `conflict_still_blocks`.
        //
        // Unlike the chainstate check above, a refusal here is "for now" rather than a
        // broadcast rejection: a later pre-commit re-evaluation may still sign the block once
        // the conflicting signature has gone stale.
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

**File:** stacks-signer/src/signerdb.rs (L1571-1585)
```rust
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

**File:** docs/signer-flows.md (L253-272)
```markdown
    PERM -- no --> FRESH{"any of them still fresh?<br/>last_endorsed > cutoff"}
    FRESH -- yes --> SORT{"conflict_still_blocks, question 1:<br/>is its tenure's sortition still on the<br/>canonical burn chain?<br/>get_sortition_by_burn_hash"}
    SORT -- "404, with the node's burnchain tip<br/>at or past the burn block — a fork<br/>orphaned the tenure" --> OWN
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
    OWN -- no --> SIGN["SIGN: mark_locally_accepted,<br/>handle_block_signature,<br/>broadcast acceptance"]:::good
    classDef good fill:#17a45c22,stroke:#1d9d5f,stroke-width:1.5px;
    classDef bad fill:#d84a3f22,stroke:#c9473d,stroke-width:1.5px;
    classDef hold fill:#8a95a51f,stroke:#8a95a5,stroke-dasharray:4 3;
```
```
