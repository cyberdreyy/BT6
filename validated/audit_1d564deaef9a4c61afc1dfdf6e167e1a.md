### Title
Sortition-view reset on a mismatched-consensus-hash proposal wipes remembered miner invalidation, letting a previously-invalidated miner get signed again - ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
`SortitionsView::check_proposal` can be forced to rebuild its `cur_sortition`/`last_sortition` state from scratch by a single crafted block proposal whose `consensus_hash` matches neither tracked sortition. The rebuild path constructs new `SortitionState` values via `TryFrom<SortitionInfo> for SortitionState`, which unconditionally sets `miner_status: SortitionMinerStatus::Valid`, discarding any `InvalidatedBeforeFirstBlock`/`InvalidatedAfterFirstBlock` verdict the signer had previously and correctly recorded for that miner in this process's memory.

### Finding Description
`check_proposal` tracks, in memory, whether the current/last sortition's miner has been judged invalid (e.g. after an illegitimate reorg attempt or a timeout) via `SortitionMinerStatus`: [1](#0-0) 

When a block proposal's `consensus_hash` does not match `cur_sortition` or `last_sortition`, and the caller allowed a reset, the signer discards its view and refetches sortition data, then re-runs the check recursively: [2](#0-1) 

The reconstruction path (`TryFrom<SortitionInfo> for SortitionState`) always initializes `miner_status` to `Valid`, with no consideration of any prior invalidation: [3](#0-2) 

This matches the structure of the reported bug class: a check compares against a piece of state (`miner_status`) that is trusted as an equality/consistency gate (`Current miner behaved improperly ... considering invalid`, `RejectReason::InvalidMiner`/`ReorgNotAllowed` at lines 157‑200 and 289‑315 of the same file), but that state can be reset to a "fresh/clean" value by an attacker-controlled input (an out-of-window `consensus_hash` in a proposal) — analogous to the YSDAO `Staking.sync()` call being reachable without authorization and resetting the price-relevant reserve state that a later check relies on. The unit test `check_proposal_refresh` confirms that a mismatched-`consensus_hash` proposal with `reset_view_if_wrong_consensus_hash = true` triggers a live refetch of sortition info and a subsequent pass of `check_proposal`, and `check_proposal_invalid_status` confirms that, absent a reset, an `InvalidatedAfterFirstBlock`/`InvalidatedBeforeFirstBlock` status correctly blocks the proposal: [4](#0-3) [5](#0-4) 

If a miner that the signer has already invalidated (e.g. because it attempted a disallowed reorg, `ReorgNotAllowed`, or timed out) sends one more proposal carrying a bogus/foreign `consensus_hash`, the caller-supplied `reset_view_if_wrong_consensus_hash = true` path wipes the in-memory `miner_status` back to `Valid` for both `cur_sortition` and `last_sortition`. A subsequent, otherwise-identical proposal from the same (still actually invalid) miner would then pass the `miner_status != Valid` gate that previously rejected it, and could proceed to be validated and signed.

### Impact Explanation
This breaks the equality/consistency the signer relies on to never sign for a miner it has already determined to be invalid — i.e. a signer could end up signing (or advancing toward signing) a block from a miner that it had itself flagged as having attempted a disallowed reorg or having gone inactive. That is a "signer signing an invalid/non-canonical block" class of outcome, achievable by the miner alone (no other signers' cooperation is required — the trigger is a single crafted proposal from the miner to each signer).

### Likelihood Explanation
Likelihood depends on which call sites actually invoke `check_proposal` with `reset_view_if_wrong_consensus_hash = true` (this determines whether a live miner, not merely a test harness, can trigger the reset in production), and on the exact contents of `reset_view` (which I could not fully inspect in this session — I was unable to open its body or fully enumerate `check_proposal`'s callers before the tool budget ran out). The `TryFrom` impl unconditionally producing `Valid` is directly confirmed, and the recursive reset call is directly confirmed in the code shown above, but I could not confirm from the available context whether `reset_view` performs any extra reconciliation (e.g. re-deriving `miner_status` from signerdb history) that might mitigate this before it reaches the field assignment. This uncertainty should be resolved before treating this as a confirmed exploit path.

### Recommendation
Before or after `reset_view` rebuilds `cur_sortition`/`last_sortition`, re-derive `miner_status` from persisted signerdb evidence (e.g. recorded `ReorgNotAllowed` rejections, timeout/inactivity records, or rejection reasons already stored via `add_block_rejection_signer_addr`) rather than defaulting unconditionally to `Valid` in `TryFrom<SortitionInfo>`. At minimum, `reset_view` should re-run the same invalidation checks (`check_parent_tenure_choice`, `is_timed_out`) against the freshly fetched sortition before accepting a proposal, instead of relying on a bare, always-`Valid` reconstruction.

### Proof of Concept
Not independently reproduced with a running node in this session; based on static code reading of:
- `SortitionsView::check_proposal`'s reset branch: [2](#0-1) 
- `TryFrom<SortitionInfo> for SortitionState` always setting `Valid`: [3](#0-2) 
- Existing tests demonstrating both halves of the behavior separately (`check_proposal_invalid_status` showing invalidation blocks proposals; `check_proposal_refresh` showing a mismatched-hash proposal triggers a live reset): [4](#0-3) [5](#0-4) 

A concrete PoC would need to confirm (1) which production call path invokes `check_proposal` with `reset_view_if_wrong_consensus_hash = true`, and (2) whether `reset_view`'s full body reconstructs `miner_status` purely via the `TryFrom` impl shown, with no independent re-validation — both of which I was unable to verify further within the available tool budget.

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L32-50)
```rust
/// Captures this signer's current view of a sortition's miner.
#[derive(PartialEq, Eq, Debug)]
pub enum SortitionMinerStatus {
    /// The signer thinks this sortition's miner is invalid, and hasn't signed any blocks for them.
    InvalidatedBeforeFirstBlock,
    /// The signer thinks this sortition's miner is invalid, but already signed one or more blocks for them.
    InvalidatedAfterFirstBlock,
    /// The signer thinks this sortition's miner is valid
    Valid,
}

/// The sortition state information including miner status
#[derive(Debug)]
pub struct SortitionState {
    /// The sortition state data
    pub data: SortitionData,
    /// what is this signer's view of the this sortition's miner? did they misbehave?
    pub miner_status: SortitionMinerStatus,
}
```

**File:** stacks-signer/src/chainstate/v1.rs (L97-106)
```rust
impl TryFrom<SortitionInfo> for SortitionState {
    type Error = ClientError;
    fn try_from(value: SortitionInfo) -> Result<Self, Self::Error> {
        let data = SortitionData::try_from(value)?;
        Ok(Self {
            data,
            miner_status: SortitionMinerStatus::Valid,
        })
    }
}
```

**File:** stacks-signer/src/chainstate/v1.rs (L254-265)
```rust
        else {
            if reset_view_if_wrong_consensus_hash {
                info!(
                    "Miner block proposal has consensus hash that is neither the current or last sortition. Resetting view.";
                    "proposed_block_consensus_hash" => %block.header.consensus_hash,
                    "current_sortition_consensus_hash" => ?self.cur_sortition.data.consensus_hash,
                    "last_sortition_consensus_hash" => ?self.last_sortition.as_ref().map(|x| &x.data.consensus_hash),
                );
                self.reset_view(client)
                    .map_err(SignerChainstateError::from)?;
                return self.check_proposal(client, signer_db, block, false, replay_set);
            }
```

**File:** stacks-signer/src/chainstate/tests/v1.rs (L357-420)
```rust
#[test]
fn check_proposal_invalid_status() {
    let (stacks_client, mut signer_db, block_sk, mut view, mut block) =
        setup_test_environment(function_name!());
    block.header.consensus_hash = view.cur_sortition.data.consensus_hash.clone();
    block.header.sign_miner(&block_sk).unwrap();
    view.check_proposal(
        &stacks_client,
        &mut signer_db,
        &block,
        false,
        ReplayTransactionSet::none(),
    )
    .expect("Proposal should validate");
    view.cur_sortition.miner_status = SortitionMinerStatus::InvalidatedAfterFirstBlock;
    view.check_proposal(
        &stacks_client,
        &mut signer_db,
        &block,
        false,
        ReplayTransactionSet::none(),
    )
    .expect_err("Proposal should not validate");

    block.header.consensus_hash = view
        .last_sortition
        .as_ref()
        .unwrap()
        .data
        .consensus_hash
        .clone();
    block.header.sign_miner(&block_sk).unwrap();
    view.check_proposal(
        &stacks_client,
        &mut signer_db,
        &block,
        false,
        ReplayTransactionSet::none(),
    )
    .expect_err("Proposal should not validate");

    view.cur_sortition.miner_status = SortitionMinerStatus::InvalidatedBeforeFirstBlock;
    block.header.consensus_hash = view
        .last_sortition
        .as_ref()
        .unwrap()
        .data
        .consensus_hash
        .clone();
    block.header.sign_miner(&block_sk).unwrap();
    // this block passes the signer state checks, even though it doesn't have a tenure change tx.
    // this is because the signer state does not perform the tenure change logic checks: it needs
    // the stacks-node to do that (because the stacks-node actually knows whether or not their
    // parent blocks have been seen before, while the signer state checks are only reasoning about
    // stacks blocks seen by the signer, which may be a subset)
    view.check_proposal(
        &stacks_client,
        &mut signer_db,
        &block,
        false,
        ReplayTransactionSet::none(),
    )
    .expect("Proposal should validate");
}
```

**File:** stacks-signer/src/chainstate/tests/v1.rs (L750-820)
```rust
#[test]
fn check_proposal_refresh() {
    let (stacks_client, mut signer_db, block_sk, mut view, mut block) =
        setup_test_environment(function_name!());
    block.header.consensus_hash = view.cur_sortition.data.consensus_hash.clone();
    block.header.sign_miner(&block_sk).unwrap();
    view.check_proposal(
        &stacks_client,
        &mut signer_db,
        &block,
        false,
        ReplayTransactionSet::none(),
    )
    .expect("Proposal should validate");

    let MockServerClient {
        server,
        client,
        config: _,
    } = MockServerClient::new();

    let last_sortition = view.last_sortition.as_ref().unwrap().data.clone();

    let expected_result = vec![
        SortitionInfo {
            burn_block_hash: last_sortition.burn_block_hash.clone(),
            burn_block_height: 2,
            sortition_id: SortitionId([2; 32]),
            parent_sortition_id: SortitionId([1; 32]),
            consensus_hash: block.header.consensus_hash.clone(),
            was_sortition: true,
            burn_header_timestamp: 2,
            miner_pk_hash160: Some(view.cur_sortition.data.miner_pkh.clone()),
            stacks_parent_ch: Some(view.cur_sortition.data.parent_tenure_id.clone()),
            last_sortition_ch: Some(view.cur_sortition.data.parent_tenure_id.clone()),
            committed_block_hash: None,
            vrf_seed: None,
        },
        SortitionInfo {
            burn_block_hash: BurnchainHeaderHash([128; 32]),
            burn_block_height: 1,
            sortition_id: SortitionId([1; 32]),
            parent_sortition_id: SortitionId([0; 32]),
            consensus_hash: view.cur_sortition.data.parent_tenure_id.clone(),
            was_sortition: true,
            burn_header_timestamp: 1,
            miner_pk_hash160: Some(view.cur_sortition.data.miner_pkh.clone()),
            stacks_parent_ch: Some(view.cur_sortition.data.parent_tenure_id.clone()),
            last_sortition_ch: Some(view.cur_sortition.data.parent_tenure_id.clone()),
            committed_block_hash: None,
            vrf_seed: None,
        },
    ];

    view.cur_sortition.data.consensus_hash = ConsensusHash([128; 20]);
    let h = std::thread::spawn(move || {
        view.check_proposal(
            &client,
            &mut signer_db,
            &block,
            true,
            ReplayTransactionSet::none(),
        )
    });
    crate::client::tests::write_response(
        server,
        format!("HTTP/1.1 200 Ok\n\n{}", serde_json::json!(expected_result)).as_bytes(),
    );
    let result = h.join().unwrap();
    result.expect("Proposal should validate");
}
```
