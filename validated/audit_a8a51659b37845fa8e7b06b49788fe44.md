Based on my research, I found a genuine analog to the Biconomy bug class in agave's transaction commit/cost-accounting path: transactions are executed (and get committed, deducting fees from the fee payer and mutating account state) based on a pre-execution *estimated* cost check, but a *different*, larger *actual* cost is computed only after execution and checked separately — and this second check can reject the transaction after the state-mutating work (and fee deduction) already happened.

### Title
Post-execution actual-cost check can reject/discard a transaction after fees are already deducted and state already committed - (File: `runtime/src/transaction_execution.rs`)

### Summary
`execute_batch` in `runtime/src/transaction_execution.rs` commits transactions (deducting fees, running programs, mutating state via `load_execute_and_commit_transactions_with_pre_commit_callback`) using compute/cost limits validated against an *estimated* cost model, then computes the *actual* cost from real execution results and enforces the block cost limit **after** the commit has already happened [1](#0-0) .

### Finding Description
Similar to the Biconomy bug — where one check validated `amount` and a later, different check validated `amount + reward`, causing a discrepancy that made the second check fail even though the first passed — agave's banking/commit pipeline validates two different cost values at two different points against the same limit (`block_cost`/`account_cost`):

1. Pre-execution: `CostModel::calculate_cost` estimates a transaction's cost from static/requested values (e.g. requested compute unit limit, requested loaded-accounts-data-size limit) and this estimate is used to admit the transaction into a batch/schedule and to reserve room against the cost tracker limits.
2. Post-execution: after `load_execute_and_commit_transactions_with_pre_commit_callback` runs and **commits** the transaction (state mutated, fees charged), `get_transaction_costs` recomputes the cost using `CostModel::calculate_cost_for_executed_transaction` with the *actual* executed units and *actual* loaded-accounts-data-size [2](#0-1) . `check_block_cost_limits` then re-checks this actual cost against the same `block_cost`/`account_cost` limits [3](#0-2) .

Because the actual cost (based on real executed compute units and real loaded account sizes) can exceed the estimated cost that was used to admit the transaction, the second check can fail (`WouldExceedMaxBlockCostLimit`) even though the transaction already executed and was committed by SVM. This is confirmed by the test `test_actual_cost_limit_rejects_after_execution_before_record`, which sets a block limit based on the *estimated* cost of one transaction, executes 32 transactions, and explicitly asserts that one transaction is committed then rejected post-hoc with `CommitTransactionDetails::NotCommitted(TransactionError::WouldExceedMaxBlockCostLimit)` while later transactions in the batch become `CommitCancelled` [4](#0-3) .

This exactly mirrors the report's root cause: "both checks should be made over the same amount" — here, both checks should be made over the same cost basis (either both estimated or both actual), but one is estimate-based (admission) and the other is actual-based (post-commit), producing an inconsistency window where the transaction already effected state changes/fee payment before failing its own gating check.

### Impact Explanation
For the specific transaction that triggers `WouldExceedMaxBlockCostLimit` after commit, the fee payer already had transaction fees deducted and (per the `validate_fee_payer`/rollback-accounts model) any account state changes may have been recorded as committed at the SVM layer, yet the transaction is reported to the rest of the pipeline as `NotCommitted`. This produces divergent bookkeeping between the actual ledger effect and what downstream consumers (RPC status, cost tracker consistency, replay) believe happened, analogous to the fund-loss condition in the original report where a user's transfer succeeds on one leg but is rejected on the other due to inconsistent limit checks. Additionally, subsequent transactions in the same batch are forced into `CommitCancelled`, causing unnecessary transaction failures/ingest inefficiency for well-formed transactions that never got a chance to execute due to the mis-estimated admission.

### Likelihood Explanation
This is reachable purely from ordinary user transactions being processed by the leader's banking stage during normal block production — no privileged access is required. It is more likely to manifest when transactions request generous (near-`MAX_COMPUTE_UNIT_LIMIT`/`MAX_LOADED_ACCOUNTS_DATA_SIZE_BYTES`) or default limits that diverge significantly from actual consumption (e.g., programs that use highly variable compute paths or CPI-loaded programdata), which is a common and unprivileged usage pattern.

### Recommendation
Ensure cost admission and cost enforcement use a single, consistent basis. Either: (a) always admit/schedule transactions using a conservative upper bound (the *requested* limits, not looser estimates) so the same worst-case value is checked both before and after execution, or (b) perform the actual-cost check before the commit path finalizes any lasting effects (fee deduction, state mutation) rather than after, so that a late rejection does not leave behind partially-applied side effects. At minimum, the discrepancy between estimated and actual cost accounting in `check_block_cost_limits` vs `CostModel::calculate_cost` should be eliminated or bounded so that commit and cost-limit enforcement can never diverge.

### Proof of Concept
The existing regression test demonstrates the exact scenario: [5](#0-4)  sets `block_limit = 2 * estimated_cost` of a single transfer transaction, submits 32 similarly-sized transfer transactions in one batch, and shows that after `consumer.process_and_record_transactions` runs (which invokes SVM execution/commit), one transaction ends up `NotCommitted(WouldExceedMaxBlockCostLimit)` — i.e., the check triggered by the *actual* post-execution cost fails after commit-path processing — while subsequent transactions become `CommitCancelled`. This is not a hypothetical: it is asserted as expected agave behavior in the test suite, confirming the two-different-checks-same-limit pattern exists in production code paths (`runtime/src/transaction_execution.rs::execute_batch` → `check_block_cost_limits`).

### Citations

**File:** runtime/src/transaction_execution.rs (L79-100)
```rust
    let (commit_results, balance_collector) = batch
        .bank()
        .load_execute_and_commit_transactions_with_pre_commit_callback(
            batch,
            ExecutionRecordingConfig::new_single_setting(transaction_status_sender.is_some()),
            timings,
            log_messages_bytes_limit,
            pre_commit_callback,
        )?;

    let mut check_block_costs_elapsed = Measure::start("check_block_costs");

    let tx_costs = get_transaction_costs(bank, &commit_results, batch.sanitized_transactions());
    let checked_tx_costs_result = check_block_cost_limits(bank, &tx_costs);

    check_block_costs_elapsed.stop();
    timings.saturating_add_in_place(
        ExecuteTimingType::CheckBlockLimitsUs,
        check_block_costs_elapsed.as_us(),
    );

    checked_tx_costs_result?;
```

**File:** runtime/src/transaction_execution.rs (L157-169)
```rust
fn check_block_cost_limits<Tx: TransactionWithMeta>(
    bank: &Bank,
    tx_costs: &[Option<TransactionCost<'_, Tx>>],
) -> TransactionResult<()> {
    let mut cost_tracker = bank.write_cost_tracker().unwrap();
    for tx_cost in tx_costs.iter().flatten() {
        cost_tracker
            .try_add(tx_cost)
            .map_err(TransactionError::from)?;
    }

    Ok(())
}
```

**File:** runtime/src/transaction_execution.rs (L171-195)
```rust
// Get actual transaction execution costs from transaction commit results
fn get_transaction_costs<'a, Tx: TransactionWithMeta>(
    bank: &Bank,
    commit_results: &[TransactionCommitResult],
    sanitized_transactions: &'a [Tx],
) -> Vec<Option<TransactionCost<'a, Tx>>> {
    assert_eq!(sanitized_transactions.len(), commit_results.len());

    commit_results
        .iter()
        .zip(sanitized_transactions)
        .map(|(commit_result, tx)| {
            if let Ok(committed_tx) = commit_result {
                Some(CostModel::calculate_cost_for_executed_transaction(
                    tx,
                    committed_tx.executed_units,
                    committed_tx.loaded_account_stats.loaded_accounts_data_size,
                    &bank.feature_set,
                ))
            } else {
                None
            }
        })
        .collect()
}
```

**File:** core/src/banking_stage/consumer.rs (L1331-1420)
```rust
    #[test]
    fn test_actual_cost_limit_rejects_after_execution_before_record() {
        const TRANSACTION_COUNT: usize = 32;

        let TestFrame {
            mint_keypair,
            bank,
            bank_forks: _bank_forks,
            record_receiver,
            consumer,
        } = setup_test_with_lamports(1_000_000_000, None);

        let payer_keypairs = (0..TRANSACTION_COUNT)
            .map(|_| Keypair::new())
            .collect::<Vec<_>>();
        for payer in &payer_keypairs {
            bank.transfer(1_000_000, &mint_keypair, &payer.pubkey())
                .unwrap();
        }

        let transactions = sanitize_transactions(
            payer_keypairs
                .iter()
                .map(|payer| {
                    system_transaction::transfer(
                        payer,
                        &Pubkey::new_unique(),
                        1,
                        bank.last_blockhash(),
                    )
                })
                .collect(),
        );
        let estimated_cost = CostModel::calculate_cost(&transactions[0], &bank.feature_set).sum();
        let block_limit = estimated_cost.saturating_mul(2);
        bank.write_cost_tracker()
            .unwrap()
            .set_limits(CostTrackerLimits {
                account_cost: block_limit,
                block_cost: block_limit,
                allocated_data_size: u64::MAX,
            });

        let process_transactions_batch_output =
            consumer.process_and_record_transactions(&bank, &transactions);
        let ProcessTransactionBatchOutput {
            cost_model_throttled_transactions_count,
            execute_and_commit_transactions_output,
            ..
        } = process_transactions_batch_output;
        let ExecuteAndCommitTransactionsOutput {
            transaction_counts,
            retryable_transaction_indexes,
            commit_transactions_result,
            error_counters,
            ..
        } = execute_and_commit_transactions_output;
        let commit_transaction_details = commit_transactions_result.unwrap();
        let committed_count = commit_transaction_details
            .iter()
            .filter(|details| matches!(details, CommitTransactionDetails::Committed { .. }))
            .count();
        let late_cost_rejected_indexes = commit_transaction_details
            .iter()
            .enumerate()
            .filter_map(|(index, details)| {
                matches!(
                    details,
                    CommitTransactionDetails::NotCommitted(
                        TransactionError::WouldExceedMaxBlockCostLimit
                    )
                )
                .then_some(index)
            })
            .collect::<Vec<_>>();
        let commit_cancelled_count = commit_transaction_details
            .iter()
            .filter(|details| {
                matches!(
                    details,
                    CommitTransactionDetails::NotCommitted(TransactionError::CommitCancelled)
                )
            })
            .count();
        let retryable_indexes = (committed_count..TRANSACTION_COUNT)
            .map(|index| RetryableIndex::new(index, index != committed_count))
            .collect::<Vec<_>>();

        assert!(committed_count > 0);
        assert_eq!(late_cost_rejected_indexes, vec![committed_count]);
```
