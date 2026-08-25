## Finding: NoOp (fee-payer-not-found / nonce-failure) transactions bypass leader cost-tracker accounting while still consuming banking-stage compute

### Summary
Agave's leader-side block production explicitly drops "no-op" transactions (SIMD-0290 fee-payer failure / SIMD-0297 nonce failure) before they are committed, and because they are never committed, their execution cost is **never added to the per-block `CostTracker`**. These transactions still pass signature verification and account loading (real CPU work) before being rejected, but — unlike the Sui bug's "gasless" transactions that skip the traffic controller — they are exempt from Agave's primary per-block DoS-relevant accounting mechanism (the cost tracker), the same structural gap described in the Sui report: work with effectively zero cost to the sender is not counted by the resource-accounting/DoS-protection layer. [1](#0-0) 

### Finding Description
During block production (`drop_noop_transactions=true`), a transaction whose fee payer does not exist (or whose nonce check fails) is loaded via `validate_transaction_nonce_and_fee_payer`/`load_transaction`, produces a `TransactionLoadResult::NoOp`, and — because block production sets `drop_on_failure`/`drop_noop_transactions` — is converted into a hard `Err` rather than being committed: [2](#0-1) 

Downstream, `get_transaction_costs` maps any `Err` commit result to `None`: [3](#0-2) 

and `try_add_processed_transaction_costs` in the banking stage simply `continue`s on `None` cost entries, never calling `cost_tracker.try_add`: [4](#0-3) 

This is confirmed by an existing unit test showing the cost tracker is left completely untouched after processing such a transaction, even though the transaction went through signature verification, account loading/lookup (which touches accounts-db to determine `AccountNotFound`), and fee/nonce validation: [5](#0-4) 

Contrast this with the `FeesOnly` path (real account-loading failures with a valid, funded fee payer), which *does* charge a fee and *is* committed, and therefore *is* reflected in the cost tracker: [6](#0-5) 

The distinguishing property of the `NoOp` class is exactly the Sui report's "gasless" characteristic: no fee is ever charged to (or collected from) the sender, so the transaction is free to the attacker, yet it still requires the leader to expend CPU (signature verification, account table lookups) to determine that it is a no-op.

### Impact Explanation
Agave's QUIC ingest layer throttles by raw stream/packet count per IP/stake (`stream_throttle.rs`, `swqos.rs`) rather than by "cost," so the primary DoS backstop at the network layer is fee-agnostic and still bounds packet volume. However, the cost tracker (`cost-model/src/cost_tracker.rs`) is the mechanism Agave relies on to bound a leader's *processing/compute* budget per block. An attacker who can get many trivially-constructed transactions (throwaway keypairs as fee payers with no funded account, or transactions engineered to fail the nonce check) into a leader's banking stage causes the leader to spend real signature-verification and account-loading effort that is invisible to the cost tracker's accounting, unlike ordinary failed transactions which are still charged (`FeesOnly`) and thus rate-limited by block cost limits.

### Likelihood Explanation
Constructing a fee-payer-not-found transaction is trivial (any unfunded keypair, valid recent blockhash) and requires no lamports from the attacker, matching the "no cost to sender" precondition from the Sui report. The relevant SIMD behavior (`relax_fee_payer_constraint`, SIMD-0290/SIMD-0297, `drop_noop_transactions`) is intentional, already-shipped leader-side logic rather than a hypothetical path, and is directly reachable from an ordinary user's or bot's transaction submission via TPU.

### Recommendation
Consider whether NoOp (dropped) transactions should be charged a nominal/estimated cost against the cost tracker (analogous to how `FeesOnly` transactions are charged) to ensure the leader's compute/cost metering reflects work actually performed, closing the same class of gap identified in the Sui gasless-tx report. At minimum, evaluate whether this gap is already sufficiently mitigated by ingest-layer (stream) throttling and stake-weighted QoS, and if not, add cost-tracker accounting (or a dedicated lightweight counter feeding into leader scheduling/backpressure) for dropped NoOp transactions.

### Proof of Concept
This is exercised directly by the existing repository test, which is effectively a proof-of-concept demonstrating the accounting gap (it shows the cost tracker is unaffected by processing a fee-payer-not-found transaction during block production): [7](#0-6) 

An external attacker would submit repeated `system_transaction::transfer` (or any) transactions signed by disposable, unfunded keypairs as the fee payer, with a valid recent blockhash, directly to the leader's TPU port; each is processed (sig-verify + account load) but never billed or counted toward block cost.

**Confidence/uncertainty:** I was not able to fully trace whether `unprocessed_transaction_storage`/scheduler-level heuristics apply any additional penalty or backoff for repeated NoOp transactions from the same sender, nor did I quantify the actual CPU cost of a single `AccountNotFound` lookup relative to the cost of QUIC-layer stream throttling that already bounds ingestion volume — these would be needed to fully assess real-world severity of this gap versus it being a low-impact, already-mitigated accounting nuance.

### Citations

**File:** svm/src/transaction_processor.rs (L503-529)
```rust
            let (processing_result, single_execution_us) = measure_us!(match load_result {
                // Unprocessable transactions always result in an error
                TransactionLoadResult::Unprocessable(e) => Err(e),

                // Validation failures that would be no-ops become errors with `drop_on_failure` or
                // `drop_noop_transactions`. These may be produced by SIMD-0290 (fee-payer failure)
                // or SIMD-0297 (nonce failure), but are dropped during block production to avoid commit.
                TransactionLoadResult::NoOp(NoOpTransaction {
                    validation_error: e,
                    ..
                }) if config.drop_on_failure || config.drop_noop_transactions => Err(e),

                // SIMD-0290 (fee-payer failure) or SIMD-0297 (nonce failure) is a non-error no-op on replay
                TransactionLoadResult::NoOp(no_op_tx) =>
                    Ok(ProcessedTransaction::NoOp(Box::new(no_op_tx))),

                // Loading failures that would be fee-only become errors with `drop_on_failure`
                TransactionLoadResult::FeesOnly(FeesOnlyTransaction { load_error: e, .. })
                    if config.drop_on_failure =>
                    Err(e),

                // Transactions that fail at account loading charge fees and roll nonces
                TransactionLoadResult::FeesOnly(fees_only_tx) => {
                    account_loader
                        .update_accounts_for_failed_tx(&fees_only_tx.rollback_accounts, self.slot);
                    Ok(ProcessedTransaction::FeesOnly(Box::new(fees_only_tx)))
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

**File:** core/src/banking_stage/consumer.rs (L544-547)
```rust
        for (index, transaction_cost) in transaction_costs.iter_mut().enumerate() {
            let Some(cost) = transaction_cost.as_ref() else {
                continue;
            };
```

**File:** core/src/banking_stage/consumer.rs (L1282-1329)
```rust
    #[test]
    fn test_bank_process_and_record_transactions_cost_tracker_noop() {
        let TestFrame {
            mint_keypair: _mint_keypair,
            bank,
            bank_forks: _bank_forks,
            record_receiver: _record_receiver,
            consumer,
        } = setup_test(None);

        let get_block_cost = || bank.read_cost_tracker().unwrap().block_cost();
        let get_tx_count = || bank.read_cost_tracker().unwrap().transaction_count();
        assert_eq!(get_block_cost(), 0);
        assert_eq!(get_tx_count(), 0);

        // TEST: a blockhash transaction with an invalid fee-payer is committed as a no-op
        // on replay (with `relax_fee_payer_constraint`), but during block production
        // `drop_noop_transactions` turns it into an error. It must not be committed and
        // must leave the cost tracker untouched.
        let transactions = sanitize_transactions(vec![system_transaction::transfer(
            &Keypair::new(),
            &Pubkey::new_unique(),
            1,
            bank.last_blockhash(),
        )]);

        let process_transactions_batch_output =
            consumer.process_and_record_transactions(&bank, &transactions);

        let ExecuteAndCommitTransactionsOutput {
            transaction_counts,
            commit_transactions_result,
            ..
        } = process_transactions_batch_output.execute_and_commit_transactions_output;

        // the no-op transaction is not committed
        assert_eq!(transaction_counts.processed_with_successful_result_count, 0);
        assert_eq!(
            commit_transactions_result.ok(),
            Some(vec![CommitTransactionDetails::NotCommitted(
                TransactionError::AccountNotFound
            )])
        );

        // and the cost tracker is unchanged after processing it
        assert_eq!(get_block_cost(), 0);
        assert_eq!(get_tx_count(), 0);
    }
```
