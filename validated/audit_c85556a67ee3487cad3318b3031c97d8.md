## Finding

Based on my investigation, the strongest analog to the "forbidden manager can never use the pool" bug class in this repo is in the signer's **rejection re-evaluation allow-list** in `stacks-signer/src/v0/signer.rs`.

### Title
Terminal rejection classification wedges a signer out of ever re-signing a block whose blocking condition was transient — ([File: stacks-signer/src/v0/signer.rs])

### Summary
`should_reevaluate_reject_reason` hardcodes which `RejectReason` variants are eligible for re-evaluation when a miner re-proposes the *same* block (same `signer_signature_hash`). Two of the reasons permanently excluded from re-evaluation — `SortitionViewMismatch` and `NotLatestSortitionWinner` — describe conditions that are inherently transient/local-clock-dependent, not permanent block-validity defects, yet they are grouped with genuinely permanent defects (`InvalidParentBlock`, `DuplicateBlockFound`, `PubkeyHashMismatch`, etc.). Once a signer records one of these as the rejection reason for a specific block, that exact block can never again be reconsidered by that signer, even after the local condition that caused the rejection resolves.

### Finding Description
`should_reevaluate_reject_reason` ( [1](#0-0) ) classifies rejection reasons into re-evaluable (`ValidationFailed(UnknownParent)`, `ValidationFailed(NotFoundError)`, `NoSortitionView`, `ConnectivityIssues`, `TestingDirective`, `InvalidTenureExtend`, `ConsensusHashMismatch`, `NoSignerConsensus`, `NotRejected`, `Unknown`) versus terminal/non-re-evaluable (`ValidationFailed(_)` otherwise, `RejectedInPriorRound`, `SortitionViewMismatch`, `ReorgNotAllowed`, `InvalidBitvec`, `PubkeyHashMismatch`, `InvalidMiner`, `NotLatestSortitionWinner`, `InvalidParentBlock`, `DuplicateBlockFound`, `IrrecoverablePubkeyHash`, `ProblematicTransactions`, `ProposalTooOld`).

`should_reevaluate_block` ( [2](#0-1) ) consults this classification when a miner re-proposes a block it already has a `BlockInfo` for: if the reason is not re-evaluable and the state is not `PreCommitted`, the signer simply resends its cached prior response via `determine_response` — it never re-runs `check_block_against_state` / `check_proposal` (the actual validity check pipeline, section 3 of `docs/signer-flows.md`) — see the flowchart at [3](#0-2) .

`RejectReason::SortitionViewMismatch` and `RejectReason::NotLatestSortitionWinner` (defined in [4](#0-3) ) are, per their own doc comments, about the *signer's local view* of the sortition/burnchain state at evaluation time ("mismatch with expected sortition view", "Miner is last sortition winner, when the current sortition winner is still valid"). These are exactly the class of catch-up/timing condition that the codebase elsewhere explicitly recognizes as transient and worth re-evaluating: `ValidationFailed(NotFoundError)` and `ValidationFailed(UnknownParent)` were deliberately added to the re-evaluable set to fix a regression where a signer's local chainstate lagging behind (missing burn view) caused a permanent, wrongly-terminal rejection — see the dedicated regression test [5](#0-4)  and its comment: "The rejection is treated as re-evaluable rather than terminal." That same reasoning was never applied to `SortitionViewMismatch` / `NotLatestSortitionWinner`, which are structurally the same class of "my local view is stale" condition, just surfaced by a different check.

A single miner slot can trigger this: the test harness itself demonstrates that a miner can (and, per this test, legitimately does) re-broadcast the exact same block bytes after an initial rejection ( [6](#0-5) , `signer_test.propose_block(block.clone(), ...)`), confirming the re-proposal-of-identical-signature-hash code path is reachable and already an established test pattern in this codebase.

### Impact Explanation
If a signer's local `SortitionsView` is momentarily stale at the moment a valid block is first proposed (a routine, benign occurrence around tenure/burn-block boundaries — not requiring any signer compromise or majority collusion), the signer rejects with `SortitionViewMismatch` or `NotLatestSortitionWinner` and stores that as the block's permanent `reject_reason`. Once the signer's view catches up (the block is in fact canonical and other signers accept it, potentially reaching the 70% signature threshold), a re-proposal of the identical block to this signer will not be re-validated — `should_reevaluate_block` short-circuits straight to resending the stale rejection. This is a "signer wedged into never signing a valid block" liveness defect for that signer/block pair, matching the High-impact class in scope (a signer that can never sign a since-become-valid block, analogous to the forbidden-manager-can-never-use-the-pool pattern: an initial classification with no code path back).

### Likelihood Explanation
The trigger condition (a signer's sortition/burnchain view being momentarily behind at proposal time) is a normal, frequently-occurring race in Nakamoto's tenure-boundary signing flow, not an attacker-privileged action; a miner does not need to do anything adversarial beyond re-announcing the same block once (a behavior the codebase's own test suite exercises). The severity is bounded to a per-signer, per-block wedge rather than a network-wide safety break, since the 70% threshold can still be met by other signers whose views were not stale at evaluation time.

### Recommendation
Move `SortitionViewMismatch` and `NotLatestSortitionWinner` into the re-evaluable branch of `should_reevaluate_reject_reason` in `stacks-signer/src/v0/signer.rs`, mirroring the treatment already given to `ValidationFailed(UnknownParent)` / `ValidationFailed(NotFoundError)` / `NoSortitionView`, since all of these represent the same class of locally-transient, catch-up-resolvable condition rather than a permanent defect in the block itself.

### Proof of Concept
Not independently reproduced in a running node; based on static code-path analysis of `should_reevaluate_reject_reason`/`should_reevaluate_block` ( [1](#0-0) , start="1481" end="1572" />) combined with the existing regression test pattern for the analogous `NotFoundError`/`UnknownParent` fix ( [7](#0-6) ). I was not able to directly inspect the exact call sites in `stacks-signer/src/chainstate/v1.rs` that emit `RejectReason::NotLatestSortitionWinner` (only located via grep, not read in full) before running out of investigation iterations, so the precise triggering conditions for that specific variant are inferred from its doc comment rather than fully traced; a Devin session with full file access would be needed to construct a concrete integration-test PoC analogous to `missing_burn_block_proposal.rs`.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1481-1572)
```rust
    /// Determine if an already tracked block should be re-evaluated based on a new block proposal for it.
    /// Returns true if the block should be re-evaluated, false if it should be ignored.
    fn should_reevaluate_block(
        &mut self,
        stacks_client: &StacksClient,
        sortition_state: &mut Option<SortitionsView>,
        block_info: &BlockInfo,
        block_proposal: &BlockProposal,
    ) -> bool {
        let signer_signature_hash = block_info.block.header.signer_signature_hash();
        if block_info.globally_approved_and_responded() {
            info!("{self}: received a block proposal for a globally accepted block to which we have already responded. Ignoring.";
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_info.block.block_id(),
                "block_height" => block_info.block.header.chain_length,
                "burn_height" => block_proposal.burn_height,
                "consensus_hash" => %block_info.block.header.consensus_hash,
                "timestamp" => block_info.block.header.timestamp,
                "signed_group" => block_info.signed_group,
                "signed_self" => block_info.signed_self,
                "valid" => ?block_info.valid
            );
            return false;
        }
        if !should_reevaluate_reject_reason(block_info) {
            if block_info.state == BlockState::PreCommitted {
                // We validated this block but haven't signed it. Signing requires the
                // pre-commit threshold and the conflict checks in `handle_block_pre_commit`.
                // Re-broadcast our pre-commit and re-run that evaluation instead of
                // responding with a signature directly, so a re-proposed block can't
                // bypass those checks.
                info!(
                    "{self}: received a block proposal for a block we have pre-committed to but not signed. Re-evaluating the pre-commit.";
                    "signer_signature_hash" => %signer_signature_hash,
                    "block_id" => %block_info.block.block_id(),
                    "block_height" => block_info.block.header.chain_length,
                    "burn_height" => block_proposal.burn_height,
                    "consensus_hash" => %block_info.block.header.consensus_hash
                );
                self.send_block_pre_commit(signer_signature_hash.clone());
                let address = self.stacks_address.clone();
                self.handle_block_pre_commit(
                    stacks_client,
                    sortition_state,
                    &address,
                    &signer_signature_hash,
                );
                return false;
            }
            if let Some(block_response) = self.determine_response(block_info) {
                self.send_block_response(&block_info.block, block_response);
                return false;
            } else {
                let is_pending = self
                    .signer_db
                    .has_pending_block_validation(&signer_signature_hash)
                    .unwrap_or_else(|e| {
                        warn!("{self}: Failed to load pending block validations: {e:?}");
                        false
                    });
                if is_pending {
                    debug!(
                        "{self}: received a block proposal for a block for which we is already pending validation. Do nothing.";
                        "signer_signature_hash" => %block_info.block.header.signer_signature_hash(),
                        "block_id" => %block_info.block.block_id()
                    );
                    return false;
                } else {
                    info!(
                        "{self}: received a block proposal for this block before, but we do not have a pending validation for it.";
                        "reject_reason" => ?block_info.reject_reason,
                        "signer_signature_hash" => %signer_signature_hash,
                        "block_id" => %block_info.block.block_id(),
                        "block_height" => block_info.block.header.chain_length,
                        "burn_height" => block_proposal.burn_height,
                        "consensus_hash" => %block_info.block.header.consensus_hash
                    );
                }
            }
        } else {
            info!(
                "{self}: received a block proposal for this block before, but our rejection reason allows us to reconsider";
                "reject_reason" => ?block_info.reject_reason,
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_proposal.block.block_id(),
                "block_height" => block_proposal.block.header.chain_length,
                "burn_height" => block_proposal.burn_height,
                "consensus_hash" => %block_proposal.block.header.consensus_hash
            );
        }
        true
    }
```

**File:** stacks-signer/src/v0/signer.rs (L2706-2739)
```rust
fn should_reevaluate_reject_reason(block_info: &BlockInfo) -> bool {
    if let Some(reject_reason) = &block_info.reject_reason {
        match reject_reason {
            RejectReason::ValidationFailed(ValidateRejectCode::UnknownParent)
            | RejectReason::ValidationFailed(ValidateRejectCode::NotFoundError)
            | RejectReason::NoSortitionView
            | RejectReason::ConnectivityIssues(_)
            | RejectReason::TestingDirective
            | RejectReason::InvalidTenureExtend
            | RejectReason::ConsensusHashMismatch { .. }
            | RejectReason::NoSignerConsensus
            | RejectReason::NotRejected
            | RejectReason::Unknown(_) => true,
            RejectReason::ValidationFailed(_)
            | RejectReason::RejectedInPriorRound
            | RejectReason::SortitionViewMismatch
            | RejectReason::ReorgNotAllowed
            | RejectReason::InvalidBitvec
            | RejectReason::PubkeyHashMismatch
            | RejectReason::InvalidMiner
            | RejectReason::NotLatestSortitionWinner
            | RejectReason::InvalidParentBlock
            | RejectReason::DuplicateBlockFound
            | RejectReason::IrrecoverablePubkeyHash
            | RejectReason::ProblematicTransactions
            | RejectReason::ProposalTooOld => {
                // No need to re-validate these types of rejections.
                false
            }
        }
    } else {
        false
    }
}
```

**File:** docs/signer-flows.md (L176-187)
```markdown
    RC -- yes --> KNOWN{"block already tracked?<br/>block_lookup_by_reward_cycle"}
    KNOWN -- yes --> REEV["should_reevaluate_block"]
    REEV --> DONE1{"globally accepted and<br/>already responded?"}
    DONE1 -- yes --> IGN2(["ignore"])
    DONE1 -- no --> REASON{"prior reject reason<br/>re-evaluable?<br/>should_reevaluate_reject_reason"}
    REASON -- no --> PC{"state = PreCommitted?"}
    PC -- yes --> RESEND["re-send pre-commit, re-run<br/>handle_block_pre_commit → section 5"]
    PC -- no --> PREV["re-send previous response<br/>determine_response, or wait if<br/>validation still pending"]
    REASON -- yes --> FRESH
    KNOWN -- no --> DRAIN["collect early votes<br/>drain_pending_block_responses"] --> FRESH["fresh evaluation:<br/>new BlockInfo, fetch<br/>SortitionsView if needed"]
    FRESH --> CHECK["check_block_against_state:<br/>protocol version consensus (NoSignerConsensus),<br/>static validity, no problematic_txs<br/>(ProblematicTransactions), then<br/>v1 SortitionsView::check_proposal or<br/>v2 GlobalStateView::check_proposal → section 7"]
    CHECK -- invalid --> REJ["send rejection<br/>(not stored)"]:::bad
```

**File:** libsigner/src/v0/messages.rs (L1127-1141)
```rust
    /// The block was rejected due to a mismatch with expected sortition view
    SortitionViewMismatch,
    /// The block was rejected due to a testing directive
    TestingDirective,
    /// The block attempted to reorg the previous tenure but was not allowed
    ReorgNotAllowed,
    /// The bitvec field does not match what is expected
    InvalidBitvec,
    /// The miner's pubkey hash does not match the winning pubkey hash
    PubkeyHashMismatch,
    /// The miner has been marked as invalid
    InvalidMiner,
    /// Miner is last sortition winner, when the current sortition winner is
    /// still valid
    NotLatestSortitionWinner,
```

**File:** stacks-node/src/tests/signer/v0/missing_burn_block_proposal.rs (L64-157)
```rust
///   `ValidationFailed(NotFoundError)`.
/// - Upon reproposal, the block is fully revalidated and rejected again
///   with the same error.
/// - The rejection is treated as re-evaluable rather than terminal.
fn signer_reevaluates_proposal_with_missing_burn_view() {
    if env::var("BITCOIND_TEST") != Ok("1".into()) {
        return;
    }

    tracing_subscriber::registry()
        .with(fmt::layer())
        .with(EnvFilter::from_default_env())
        .init();

    info!("------------------------- Test Setup -------------------------");
    let num_signers = 5;
    let signer_test = SignerTest::new(num_signers, vec![]);
    let all_signers = signer_test.signer_test_pks();
    let conf = signer_test.running_nodes.conf.clone();
    let miner_privk = signer_test.get_miner_key();
    let miner_pubk = StacksPublicKey::from_private(&miner_privk);

    signer_test.boot_to_epoch_3();

    info!("------------------------- Start a new Tenure -------------------------");

    let info_before = get_chain_info(&conf);
    TEST_IGNORE_ALL_BLOCK_PROPOSALS.set(all_signers.clone());
    // Also ignore the signers so we can repropose the block after it is rejected without the miner reproposing it.
    TEST_IGNORE_SIGNERS.set(true);
    signer_test
        .running_nodes
        .btc_regtest_controller
        .build_next_block(1);
    wait_for(30, || {
        Ok(get_chain_info(&conf).burn_block_height >= info_before.burn_block_height)
    })
    .expect("Failed to wait for burn block height to update after mining a block");
    info!("------------------------- Retrieve the block proposal for later proposal -------------------------");
    let block_proposal =
        wait_for_block_proposal(30, info_before.stacks_tip_height + 1, &miner_pubk)
            .expect("Miner 2 did not propose a tenure change block");
    // Pause the proposal again for granular control
    TEST_BROADCAST_PROPOSAL_STALL.set(vec![miner_pubk.clone()]);
    info!("------------------------- Allow signers to consider incoming block proposals -------------------------");
    TEST_IGNORE_ALL_BLOCK_PROPOSALS.set(vec![]);

    info!("------------------------- Re-propose block proposal with bad burn view consensus hash -------------------------");
    test_observer::clear();
    let mut block = block_proposal.block.clone();
    let mut tenure_change_tx = block.executed_and_skipped_txs()[0].clone();
    let mut tenure_change_payload = tenure_change_tx.try_as_tenure_change().unwrap().clone();
    tenure_change_payload.burn_view_consensus_hash = ConsensusHash([7u8; 20]);
    tenure_change_tx.payload = TransactionPayload::TenureChange(tenure_change_payload);

    block.executed_and_skipped_txs_mut()[0] = tenure_change_tx;

    let tx_merkle_root = {
        let txid_vecs: Vec<_> = block
            .txs()
            .map(|tx| tx.txid().as_bytes().to_vec())
            .collect();
        MerkleTree::<Sha512Trunc256Sum>::new(&txid_vecs).root()
    };
    block.header.tx_merkle_root = tx_merkle_root;

    block.header.sign_miner(&miner_privk).unwrap();

    let proposed_sighash = block.header.signer_signature_hash();
    signer_test.propose_block(block.clone(), Duration::from_secs(30));
    let proposed_block =
        wait_for_block_proposal(30, info_before.stacks_tip_height + 1, &miner_pubk)
            .expect("Miner did not propose a tenure change block");

    assert_eq!(
        proposed_block.block.header.signer_signature_hash(),
        proposed_sighash
    );
    info!("------------------------- Confirm Signers Reject block N due to invalid burn view causing DBError::NotFound -------------------------");
    let rejections = wait_for_block_rejections_from_signers(30, &proposed_sighash, &all_signers)
        .expect("Failed to find block rejections from all signers for the reproposed block");
    rejections.iter().for_each(|rejection| {
        assert_eq!(
            rejection.reason_code,
            RejectCode::ValidationFailed(ValidateRejectCode::NotFoundError)
        );
        assert_eq!(rejection.reason, "Chainstate Error: Not found");
    });

    info!("------------------------- Confirm signers reprocess the block after reproposed even though Rejected previously with NotFoundError -------------------------");
    // This used to return "RejectedInPriorRound" but now that we allow the NotFoundError to be reprocessed it should reply with the same error again
    test_observer::clear();
    signer_test.propose_block(block, Duration::from_secs(30));
    let rejections = wait_for_block_rejections_from_signers(30, &proposed_sighash, &all_signers)
```
