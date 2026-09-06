### Title
Unbounded wall-clock/memory execution during transaction-replay validation defeats the block-proposal resource guard, enabling a Besu-MSTORE-style liveness wedge - ([File: stackslib/src/net/api/postblock_proposal.rs])

### Summary
`NakamotoBlockProposal::validate()` protects normal block-proposal transaction execution with a wall-clock/memory `ResourceBudget` (`block_proposal_validation_timeout_secs`, `block_proposal_max_tx_execution_time_secs`, `block_proposal_max_tx_analysis_time_secs`, `block_proposal_max_tx_mem_bytes`), specifically as defense-in-depth against exactly the bug class in the referenced Besu report: a transaction that is cheap under the deterministic cost model but pathologically slow to execute in wall-clock time (e.g., due to an implementation inefficiency in a specific opcode/native function, analogous to Besu's `MSIZE`/`MSTORE` memory-expansion bug). However, the *transaction-replay* code path, `validate_replay()`, executes every replay-set transaction with `TransactionResourceBudgets::unlimited()` and runs entirely **before** the block-level deadline (`block_deadline`) is even computed. This path therefore has no wall-clock cap, no per-tx analysis/execution cap, and no memory cap at all.

### Finding Description
In `stackslib/src/net/api/postblock_proposal.rs`:
- `validate()` calls `self.validate_replay(...)` at [1](#0-0) , and only afterwards computes the block-level deadline and per-tx budgets used for the *main* tx loop: [2](#0-1) .
- Inside `validate_replay()`, every attempt to mine a replay transaction — both the "skip mismatched tx" probe and the actual application of the block's transaction — passes `TransactionResourceBudgets::unlimited()`: [3](#0-2)  and again in the "exhausted" check: [4](#0-3) .
- By contrast, the main-loop budgets exist precisely to guard against a Clarity evaluation bug causing "excessive memory usage or delays," per the documented intent of `TransactionResourceBudgets`/`ResourceBudget`: [5](#0-4)  and [6](#0-5) . The interpreter-level check that would normally catch a runaway operation, `check_interpreter_resource_usage`, only fires when a `ResourceLimiter` other than `NoTracking`/unlimited is installed: [7](#0-6) . Since `validate_replay` explicitly installs `TransactionResourceBudgets::unlimited()`, this check is a no-op for the entire replay path.

The transaction-replay mechanism activates automatically whenever signers detect that a prior tenure was reorged out and its transactions must be replayed (`ReplayTransactionSet`/`tx_replay_set`, established by signer state-machine agreement based on the observed fork, not on active collusion) — see the replay-set tests confirming this is a normal, automatically-triggered mechanism: [8](#0-7) , and the signer submits this replay set to the node on every subsequent proposal via `submit_block_for_validation`: [9](#0-8) .

A single miner who wins one sortition can therefore include, in their own tenure, a transaction that is legal under the deterministic Clarity cost model but which triggers a pathologically slow native operation (the Besu-analog case: an operation whose real implementation cost scales worse than its charged cost, e.g. an O(n) copy/reallocation invoked repeatedly). If that tenure is subsequently reorged out (an ordinary chain event, not requiring signer collusion), the orphaned transaction naturally becomes part of the `ReplayTransactionSet` that all signers/nodes must revalidate on every future block proposal until it is dropped or mined. Every node's `validate_replay()` call for every such future proposal will then re-execute that transaction with no wall-clock or memory bound.

### Impact Explanation
This directly breaks the intended safety/liveness property that the resource-budget mechanism guarantees for the entire block-proposal-validation surface: "a smart contract that triggers excessive memory usage or delays is not included in the chain" and is treated as problematic rather than stalling validation. Because `validate_replay()` is exempted from this guarantee and runs unconditionally ahead of the timeout, a crafted-but-legal transaction can cause the node's `/v3/block_proposal` validation thread to run for an unbounded amount of wall-clock time (or consume unbounded memory) on every signer/node attempting to validate any subsequent block proposal, for as long as the tenure's replay set remains unexhausted. Signers relying on `block_proposal_validation_timeout` will repeatedly time out with `ConnectivityIssues` rejections rather than making forward progress, and the underlying node-side validation thread keeps consuming resources regardless. This is a liveness wedge: the network cannot cleanly resolve the replay obligation and advance, matching the "High" impact category of a signer/network wedged into never validating a legitimate proposal.

### Likelihood Explanation
Reachable by a single one-slot miner: they need only include one carefully constructed transaction (a legal Clarity call/deploy exploiting a wall-clock/cost-model mismatch analogous to Besu's memory-expansion bug) in their own tenure, then have that tenure reorged — an ordinary, frequent chain event that does not require compromising or colluding with a majority of signers. The replay-set construction and its unbounded re-validation are automatic, deterministic node/signer behavior once the fork is observed, so no cooperation from other signers beyond their normal, honest operation is required.

### Recommendation
Apply the same `TransactionResourceBudgets` (wall-clock + memory) used in the main validation loop to `validate_replay()`, and compute/enforce `block_deadline` before entering `validate_replay()` so the overall per-block wall-clock cap also bounds the replay-set validation phase. Ensure a transaction that exceeds its budget during replay is treated the same way as in the main loop (flagged `ProblematicTransaction`/timeout rejection) rather than being allowed to run unbounded.

### Proof of Concept
Exact reproduction (real wall-clock timing of a specific slow Clarity native op) requires running the node/signer stack, which is outside static analysis. The logical PoC is:
1. Miner wins a sortition and includes, in their tenure, a Clarity transaction that is cheap per the deterministic cost tracker but implementation-slow (the Besu-report analog would be a Stacks native operation whose real cost is worse than its charged cost, e.g. a large sequence/copy/reallocation-heavy builtin).
2. That tenure is reorged out by a later miner (ordinary chain event).
3. Signers' state machine agreement establishes this transaction as part of `ReplayTransactionSet` (`libsigner/src/tests/signer_state.rs` shows this requires only the normal majority-of-honest-agreement on the *observed fork*, not collusion in crafting the attack tx).
4. Every subsequent block proposal is submitted to nodes with `replay_txs` populated (`stacks-signer/src/v0/signer.rs:2613-2623`), and every node's `validate()` → `validate_replay()` re-executes the slow transaction using `TransactionResourceBudgets::unlimited()` (`stackslib/src/net/api/postblock_proposal.rs:939-1051`), with no deadline in effect, for every future proposal attempt until the replay set is exhausted.

### Citations

**File:** stackslib/src/net/api/postblock_proposal.rs (L704-711)
```rust
        let replay_tx_exhausted = self.validate_replay(
            &parent_stacks_header,
            tenure_change,
            coinbase,
            tenure_cause,
            chainstate,
            &burn_dbconn,
        )?;
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L726-753)
```rust
        let mut miner_tenure_info =
            builder.load_tenure_info(chainstate, &burn_dbconn, tenure_cause)?;
        let burn_chain_height = miner_tenure_info.burn_tip_height;
        let mut tenure_tx = builder.tenure_begin(&burn_dbconn, &mut miner_tenure_info)?;

        let block_deadline = Instant::now() + Duration::from_secs(timeout_secs);
        let per_tx_max_execution_time = Duration::from_secs(max_tx_execution_time_secs);
        // Bound the analysis phase during proposal validation by the
        // dedicated per-tx analysis budget, independently of the eval budget above.
        let per_tx_max_analysis_time = Duration::from_secs(max_tx_analysis_time_secs);
        let mut receipts_total = 0u64;

        let max_tx_mem_bytes_opt = if max_tx_mem_bytes > 0 {
            Some(max_tx_mem_bytes)
        } else {
            None
        };
        let resource_budgets = TransactionResourceBudgets::new()
            .with_analysis_budget(
                ResourceBudget::new()
                    .with_max_duration(Some(per_tx_max_analysis_time))
                    .with_max_memory_use(max_tx_mem_bytes_opt),
            )
            .with_execution_budget(
                ResourceBudget::new()
                    .with_max_duration(Some(per_tx_max_execution_time))
                    .with_max_memory_use(max_tx_mem_bytes_opt),
            );
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L939-1051)
```rust
        let mut total_receipts = 0;
        for (i, tx) in self.block.txs.iter().enumerate() {
            let tx_len = tx.tx_len();

            // If a list of replay transactions is set, this transaction must be the next
            // mineable transaction from this list.
            loop {
                if matches!(
                    tx.payload,
                    TransactionPayload::TenureChange(..) | TransactionPayload::Coinbase(..)
                ) {
                    // Allow this to happen, tenure extend checks happen elsewhere.
                    break;
                }
                fault_injection_reject_replay_txs()?;
                let Some(replay_tx) = replay_txs.pop_front() else {
                    // During transaction replay, we expect that the block only
                    // contains transactions from the replay set. Thus, if we're here,
                    // the block contains a transaction that is not in the replay set,
                    // and we should reject the block.
                    warn!("Rejected block proposal. Block contains transactions beyond the replay set.";
                        "txid" => %tx.txid(),
                        "tx_index" => i,
                    );
                    return Err(BlockValidateRejectReason {
                        reason_code: ValidateRejectCode::InvalidTransactionReplay,
                        reason: "Block contains transactions beyond the replay set".into(),
                        failed_txid: Some(tx.txid()),
                    });
                };
                if replay_tx.txid() == tx.txid() {
                    break;
                }

                // The included tx doesn't match the next tx in the
                // replay set. Check to see if the tx is skipped because
                // it was unmineable.
                let tx_result = replay_builder.try_mine_tx_with_len(
                    &mut replay_tenure_tx,
                    &replay_tx,
                    replay_tx.tx_len(),
                    &BlockLimitFunction::NO_LIMIT_HIT,
                    &TransactionResourceBudgets::unlimited(),
                    &mut total_receipts,
                );
                match tx_result {
                    TransactionResult::Skipped(TransactionSkipped { error, .. })
                    | TransactionResult::ProcessingError(TransactionError { error, .. })
                    | TransactionResult::Problematic(TransactionProblematic { error, .. }) => {
                        // The tx wasn't able to be mined. Check the underlying error, to
                        // see if we should reject the block or allow the tx to be
                        // dropped from the replay set.

                        match error {
                            ChainError::CostOverflowError(..)
                            | ChainError::BlockTooBigError
                            | ChainError::BlockCostLimitError
                            | ChainError::ClarityError(ClarityError::CostError(..)) => {
                                // block limit reached; add tx back to replay set.
                                // BUT we know that the block should have ended at this point, so
                                // return an error.
                                let txid = replay_tx.txid();
                                replay_txs.push_front(replay_tx);

                                warn!("Rejecting block proposal. Next replay tx exceeds cost limits, so should have been in the next block.";
                                    "error" => %error,
                                    "txid" => %txid,
                                );

                                return Err(BlockValidateRejectReason {
                                    reason_code: ValidateRejectCode::InvalidTransactionReplay,
                                    reason: "Next replay tx exceeds cost limits, so should have been in the next block.".into(),
                                    failed_txid: None,
                                });
                            }
                            _ => {
                                info!("During replay block validation, allowing problematic tx to be dropped";
                                    "txid" => %replay_tx.txid(),
                                    "error" => %error,
                                );
                                // it's ok, drop it
                                continue;
                            }
                        }
                    }
                    TransactionResult::Success(_) => {
                        // Tx should have been included
                        warn!("Rejected block proposal. Block doesn't contain replay transaction that should have been included.";
                            "block_txid" => %tx.txid(),
                            "block_tx_index" => i,
                            "replay_txid" => %replay_tx.txid(),
                        );
                        return Err(BlockValidateRejectReason {
                            reason_code: ValidateRejectCode::InvalidTransactionReplay,
                            reason: "Transaction is not in the replay set".into(),
                            failed_txid: Some(tx.txid()),
                        });
                    }
                };
            }

            // Apply the block's transaction to our block builder, but we don't
            // actually care about the result - that happens in the main
            // validation check.
            let _tx_result = replay_builder.try_mine_tx_with_len(
                &mut replay_tenure_tx,
                tx,
                tx_len,
                &BlockLimitFunction::NO_LIMIT_HIT,
                &TransactionResourceBudgets::unlimited(),
                &mut total_receipts,
            );
        }
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L1056-1085)
```rust
        let only_unmineable_remaining = !replay_txs.is_empty()
            && replay_txs.iter().all(|tx| {
                let tx_result = replay_builder.try_mine_tx_with_len(
                    &mut replay_tenure_tx,
                    &tx,
                    tx.tx_len(),
                    &BlockLimitFunction::NO_LIMIT_HIT,
                    &TransactionResourceBudgets::unlimited(),
                    &mut total_receipts,
                );
                match tx_result {
                    TransactionResult::Skipped(TransactionSkipped { error, .. })
                    | TransactionResult::ProcessingError(TransactionError { error, .. })
                    | TransactionResult::Problematic(TransactionProblematic { error, .. }) => {
                        // If it's just a cost error, it's not unmineable.
                        !matches!(
                            error,
                            ChainError::CostOverflowError(..)
                                | ChainError::BlockTooBigError
                                | ChainError::ClarityError(ClarityError::CostError(..))
                                | ChainError::BlockCostLimitError
                        )
                    }
                    TransactionResult::Success(_) => {
                        // The tx could have been included, but wasn't. This is ok, but we
                        // haven't exhausted the replay set.
                        false
                    }
                }
            });
```

**File:** stackslib/src/chainstate/stacks/miner.rs (L736-755)
```rust
/// Defines limits on computing resources (heap allocation and wallclock time)
/// during processing of contract deploy and call transaction. These are
/// independent of cost tracking and MUST be [`ResourceBudget::unlimited`]
/// during consensus-critical processing, because that must remain deterministic.
///
/// The budgets are limited during the miner's block construction and the
/// signer node's proposal validation to ensure that a smart contract that
/// triggers excessive memory usage or delays is not included in the chain.
/// This is a defense-in-depth measure -- if these budgets are exceeded, that
/// probably means there's an underlying bug in the VM or analysis engine that
/// should be fixed.
pub struct TransactionResourceBudgets {
    /// The budget that applies during clarity evalution, used both during
    /// contract deploy and contract call transactions.
    execution_budget: ResourceBudget,

    /// The budget that applies during contract analysis, only used during
    /// contract deploy transactions.
    analysis_budget: ResourceBudget,
}
```

**File:** clarity/src/vm/resource_limiter.rs (L181-199)
```rust
/// Specifies the maximum wallclock time and the maximum heap allocation
/// that can be used by an operation. The two relevant operations are
/// contract analysis and execution, each of which have separate budgets
/// (see `TransactionResourceBudgets`).
///
/// Call [`ResourceBudget::start_tracking`] to receive a [`ResourceLimiter`] that
/// fixes the baseline (current time and memory allocation) and that can be polled
/// to ensure usage stays within limits.
///
/// Memory tracking requires that the [`TrackingAllocator`] has been installed.
///
/// This is NOT related to cost tracking. The latter is consensus-critical and therefore
/// deterministic. The purpose of the [`ResourceBudget`] is defense-in-depth: If
/// a bug in clarity evaluation or analysis causes a long runtime or a huge amount
/// of memory being used, the miner will not include it in a block, and the signer
/// will reject the block as problematic.
///
/// During consensus-critical work, the budget MUST be [`ResourceBudget::unlimited`]
/// to ensure determinism.
```

**File:** clarity/src/vm/mod.rs (L550-572)
```rust
/// Check for interpreter-level violations of the resource limits
/// (execution time limit or excessive heap allocations).
fn check_interpreter_resource_usage(
    global_context: &GlobalContext,
) -> Result<(), VmExecutionError> {
    global_context
        .execution_resource_limiter
        .check_not_exceeded()
        .map_err(|err| match err {
            ResourceLimitExceeded::MaxDurationExceeded(s) => {
                RuntimeCheckErrorKind::ExecutionResourceBudgetExceeded(format!(
                    "Evaluation took too much time: {s}"
                ))
                .into()
            }
            ResourceLimitExceeded::MaxAllocationExceeded(s) => {
                RuntimeCheckErrorKind::ExecutionResourceBudgetExceeded(format!(
                    "Evaluation used too much memory: {s}"
                ))
                .into()
            }
        })
}
```

**File:** libsigner/src/tests/signer_state.rs (L533-561)
```rust
#[test]
/// Case: One signer has [A,B,C], another has [A,B] - should find common prefix [A,B]
fn test_replay_set_common_prefix_coalescing() {
    let mut state_test = SignerStateTest::new(5);

    // Signers 0, 1: [A,B,C] (40% weight)
    state_test.update_signers(
        &[0, 1],
        vec![
            state_test.tx_a.clone(),
            state_test.tx_b.clone(),
            state_test.tx_c.clone(),
        ],
    );

    // Signers 2, 3, 4: [A,B] (60% weight - should win)
    state_test.update_signers(
        &[2, 3, 4],
        vec![state_test.tx_a.clone(), state_test.tx_b.clone()],
    );

    let transactions = state_test.get_global_replay_set();

    // Should find common prefix [A,B] since it's the longest prefix with majority support
    assert_eq!(transactions.len(), 2);
    assert_eq!(transactions[0], state_test.tx_a); // Order matters!
    assert_eq!(transactions[1], state_test.tx_b);
    assert!(!transactions.contains(&state_test.tx_c));
}
```

**File:** stacks-signer/src/v0/signer.rs (L2613-2623)
```rust
        match stacks_client.submit_block_for_validation(
            block.clone(),
            if self.validate_with_replay_tx {
                self.global_state_evaluator
                    .get_global_tx_replay_set()
                    .unwrap_or_default()
                    .clone_as_optional()
            } else {
                None
            },
        ) {
```
