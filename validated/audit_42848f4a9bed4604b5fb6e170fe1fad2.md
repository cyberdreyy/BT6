[1](#0-0) [2](#0-1)

### Citations

**File:** stacks-node/src/tests/signer/v0/missing_burn_block_proposal.rs (L36-67)
```rust
#[tag(bitcoind)]
#[test]
#[ignore]
/// Test that when a block proposal contains a TenureChange referencing an
/// unknown burn view consensus hash or one with `pox_valid = 0`, all
/// signers reject it with `ValidationFailed(NotFoundError)` and will
/// reprocess (not short-circuit) the proposal if it is reproposed.
///
/// Test Setup:
/// The test spins up 5 Stacks signers, one miner Nakamoto node, and a
/// corresponding bitcoind instance. The node is advanced to the Epoch 3.0
/// boundary to allow block signing.
///
/// Test Execution:
/// 1. The miner mines a burn block to start a new tenure.
/// 2. The resulting block proposal is intercepted before signers process it.
/// 3. The TenureChange transaction is modified to reference a bogus
///    burn_view_consensus_hash (one that does not exist in the sortition DB).
/// 4. The transaction Merkle root and miner signature are recomputed.
/// 5. The modified block is proposed to the signers.
/// 6. All signers reject the block during validation with
///    `Chainstate Error: Not found`.
/// 7. The same modified block is reproposed.
/// 8. Signers revalidate the proposal and reject it again with the same
///    `NotFoundError` (rather than returning `RejectedInPriorRound`).
///
/// Test Assertion:
/// - All signers reject the modified block with
///   `ValidationFailed(NotFoundError)`.
/// - Upon reproposal, the block is fully revalidated and rejected again
///   with the same error.
/// - The rejection is treated as re-evaluable rather than terminal.
```

**File:** stacks-node/src/tests/signer/v0/missing_burn_block_proposal.rs (L153-165)
```rust
    info!("------------------------- Confirm signers reprocess the block after reproposed even though Rejected previously with NotFoundError -------------------------");
    // This used to return "RejectedInPriorRound" but now that we allow the NotFoundError to be reprocessed it should reply with the same error again
    test_observer::clear();
    signer_test.propose_block(block, Duration::from_secs(30));
    let rejections = wait_for_block_rejections_from_signers(30, &proposed_sighash, &all_signers)
        .expect("Failed to find block rejections from all signers for the reproposed block");
    rejections.iter().for_each(|rejection| {
        assert_eq!(
            rejection.reason_code,
            RejectCode::ValidationFailed(ValidateRejectCode::NotFoundError)
        );
        assert_eq!(rejection.reason, "Chainstate Error: Not found");
    });
```
