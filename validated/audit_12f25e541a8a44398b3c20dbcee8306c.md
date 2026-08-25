### Title
DoS via `all_or_nothing` batch execution: a single deliberately-failing transaction cancels every co-scheduled transaction from unrelated users - ([File: svm/src/transaction_processor.rs])

### Summary
An external scheduler (packed via the shared-memory `PackToWorkerMessage`/`ExecutionFlags::all_or_nothing` protocol) can batch up to `MAX_TRANSACTIONS_PER_MESSAGE` (64) transactions from independent, unrelated users into a single work unit sent to a banking-stage worker. If the `all_or_nothing` flag is set and any single transaction in that batch fails during execution, the runtime forcibly overwrites the results of every other transaction in the batch — including ones that already executed successfully — with `TransactionError::CommitCancelled`, and none of them are committed. This mirrors the reported bug class: one cheaply-triggered failure (an "unwithdrawable"/poisoned element in a shared batch) blocks fund/state settlement for every other, unrelated participant sharing that batch.

### Finding Description
`TransactionProcessingConfig::all_or_nothing` is documented and implemented to abort the *entire* batch as soon as one transaction inside it fails: [1](#0-0) 

```
// If this is an all or nothing batch and we failed to process this transaction then we
// must abort all prior/remaining transactions.
if config.all_or_nothing && processing_result.is_err() {
    // Abort prior transactions.
    for res in processing_results.iter_mut() {
        *res = Err(TransactionError::CommitCancelled);
    }
    ...
```

This is wired directly from the external-scheduler protocol: a `PackToWorkerMessage.flags` bit (`execution_flags::ALL_OR_NOTHING`) set by the *external pack process* is translated 1:1 into `ExecutionFlags::all_or_nothing`, which then flows into `TransactionProcessingConfig::all_or_nothing` for the whole batch of up to 64 transactions: [2](#0-1) [3](#0-2) 

The batch itself is an arbitrary list of transactions assembled by the external pack process from the TPU-ingest stream (`tpu_to_pack` → scheduler → `PackToWorkerMessage.batch`), i.e., it is not restricted to transactions authored/controlled by a single party: [4](#0-3) 

Because `ExecutionFlags` and `all_or_nothing` are just fields read from an untrusted (or at least, scheduler-influenced) message with no per-transaction ownership/relationship check, an attacker only needs to get one cheap, deterministically-failing transaction (e.g., a transfer that always hits `InsufficientFunds`, a bad nonce, or any `InstructionError`) placed into the same 64-transaction batch as other unrelated legitimate transactions. Once that happens, `load_and_execute_sanitized_transactions` overwrites every other transaction's result — success or failure — with `CommitCancelled`, and the commit path (`commit_transactions_result`) never persists any of the batch's state changes or fees.

### Impact Explanation
This is a state-mutation/availability bug reachable from ordinary user transaction ingestion combined with a specific banking-stage execution mode (external-scheduler `all_or_nothing` batches). A single cheap, deliberately-failing transaction submitted by any user can cause an entire co-scheduled batch of up to 64 other users' transactions to be discarded with `CommitCancelled` for that leader slot/attempt, none of them landing on-chain, no fees collected, nonces not advanced. Repeated at scale (attacker submits many cheap failing transactions timed to land in as many batches as possible), this degrades effective throughput and selectively censors/delays arbitrary victim transactions without the attacker needing any relationship to the victims or their accounts — directly analogous to the report's "one blacklisted/failing participant blocks withdrawal for everyone sharing the same atomic operation."

### Likelihood Explanation
Exploitability depends on: (1) the leader running the external-scheduler path with `all_or_nothing` batches enabled, and (2) the external scheduler packing unrelated transactions together into the same `PackToWorkerMessage`. The `all_or_nothing` flag's own documentation states its purpose is for cases "without `drop_on_failure`" where "processed but failing transactions" would otherwise still be committed — suggesting it is intended for genuinely atomic multi-transaction submissions (e.g., a single actor's own bundle) rather than arbitrary mixed-user batching. Whether the actual external scheduler implementation guarantees batch cohesion (e.g., only bundling transactions from the same submitter/bundle) could not be fully verified from the indexed code — the `greedy_scheduler.rs` batching logic assembles batches purely by account-lock/CU-budget greediness with no such invariant, but the specific external-pack-process policy that sets the `ALL_OR_NOTHING` flag lives outside this repository/binding surface. This uncertainty should be resolved before treating this as a confirmed, generally-exploitable issue.

### Recommendation
- Ensure `ExecutionFlags::all_or_nothing` batches can only ever contain transactions that are guaranteed to originate from a single trusted submitter/bundle (e.g., verify a shared bundle identifier or signer relationship before honoring the flag), rather than trusting an opaque flag from the pack-to-worker message applied to an arbitrary transaction batch.
- Alternatively, bound the blast radius: only allow `all_or_nothing` to cancel batches where all transactions share account-lock overlap with the failing transaction, or require the scheduler to prove batch cohesion (e.g., a MAC/signature over the batch composition) before the flag is honored by the worker.
- Add telemetry/anomaly detection for `CommitCancelled` rates attributable to `all_or_nothing`, to detect griefing patterns where unrelated transactions are being cancelled en masse.

### Proof of Concept
Conceptual reproduction based on `core/src/banking_stage/consumer.rs` test infrastructure and `svm/src/transaction_processor.rs`:

1. An external scheduler assembles a `PackToWorkerMessage` batch containing:
   - Txn A: a normal transfer from victim 1 (would succeed standalone).
   - Txn B: a normal transfer from victim 2 (would succeed standalone).
   - Txn C: an attacker-crafted transaction guaranteed to fail (e.g., transfer exceeding balance → `InstructionError`/`InsufficientFunds`).
2. `message.flags` includes `execution_flags::ALL_OR_NOTHING`.
3. `ExternalWorker::execute_batch` reads the flag into `ExecutionFlags{ all_or_nothing: true }` and calls `Consumer::process_and_record_aged_transactions`, which flows into `Bank::load_and_execute_transactions` → `TransactionBatchProcessor::load_and_execute_sanitized_transactions` with `config.all_or_nothing = true`.
4. Inside the execution loop (`svm/src/transaction_processor.rs:630-655`), as soon as Txn C's `processing_result` is `Err`, all entries in `processing_results` (including A and B, which had already succeeded) are rewritten to `Err(TransactionError::CommitCancelled)`.
5. The corresponding unit test `test_run_execute_all_or_nothing_translation_failure` (`core/src/banking_stage/consume_worker.rs:1956-1992`) and `test_commit_cancelled_response_reason_uses_batch_mode` (`core/src/banking_stage/consume_worker.rs:1534-1583`) confirm the `ALL_OR_NOTHING_BATCH_FAILURE` / `CommitCancelled` propagation mechanics exist and behave exactly this way, though they exercise it with a single-submitter test batch rather than proving cross-user exploitation. [1](#0-0) [5](#0-4)

### Citations

**File:** svm/src/transaction_processor.rs (L630-655)
```rust
            // If this is an all or nothing batch and we failed to process this transaction then we
            // must abort all prior/remaining transactions.
            if config.all_or_nothing && processing_result.is_err() {
                // Abort prior transactions.
                for res in processing_results.iter_mut() {
                    *res = Err(TransactionError::CommitCancelled);
                }

                // Preserve the failure that triggered the batch to abort.
                processing_results.push(processing_result);

                // Abort remaining transactions.
                processing_results.extend(
                    (0..sanitized_txs.len() - processing_results.len())
                        .map(|_| Err(TransactionError::CommitCancelled)),
                );

                return LoadAndExecuteSanitizedTransactionsOutput {
                    error_metrics,
                    execute_timings,
                    processing_results,
                    // If we abort the batch and balance recording is enabled, no balances should be
                    // collected. If this is a leader thread, no batch will be committed.
                    balance_collector: None,
                };
            }
```

**File:** core/src/banking_stage/consume_worker.rs (L404-433)
```rust
            // SAFETY: Assumption that external scheduler does not pass messages with batch regions
            //         not pointing to valid regions in the allocator.
            let batch = unsafe {
                TransactionPtrBatch::from_sharable_transaction_batch_region(
                    &message.batch,
                    &self.allocator,
                )
            };
            let (translation_results, transactions, max_ages) =
                Self::translate_transaction_batch(&batch, bank);

            // Enforce all or nothing on translation_results.
            let execution_flags = ExecutionFlags {
                drop_on_failure: message.flags & execution_flags::DROP_ON_FAILURE != 0,
                all_or_nothing: message.flags & execution_flags::ALL_OR_NOTHING != 0,
            };
            if execution_flags.all_or_nothing && translation_results.len() != transactions.len() {
                self.send_execution_response(
                    message,
                    Self::all_or_nothing_translate_iterator(&translation_results, bank.slot()),
                )?;

                return Ok(false);
            }
            let output = self.consumer.process_and_record_aged_transactions(
                bank,
                &transactions,
                &max_ages,
                &execution_flags,
            );
```

**File:** core/src/banking_stage/consume_worker.rs (L1956-1992)
```rust
        #[test]
        fn test_run_execute_all_or_nothing_translation_failure() {
            let mut test_frame = setup_external_test_frame();
            test_frame.enable_execution();

            let batch = test_frame.allocate_batch(&[
                wincode::serialize(&transfer(
                    &test_frame.mint_keypair,
                    &Pubkey::new_unique(),
                    1,
                    test_frame.genesis_config.hash(),
                ))
                .unwrap(),
                vec![0xff],
            ]);
            test_frame.send_message(PackToWorkerMessage {
                flags: pack_message_flags::EXECUTE | execution_flags::ALL_OR_NOTHING,
                max_working_slot: test_frame.bank.slot(),
                batch: batch.region,
            });
            test_frame.iterate().unwrap();
            let response = test_frame.recv_response();
            assert_eq!(response.processed_code, processed_codes::PROCESSED);
            let responses = test_frame.execution_responses(&response.responses);
            assert_eq!(responses.len(), 2);
            assert_eq!(
                responses[0].not_included_reason,
                not_included_reasons::ALL_OR_NOTHING_BATCH_FAILURE
            );
            assert_eq!(
                responses[1].not_included_reason,
                not_included_reasons::SANITIZE_FAILURE
            );

            test_frame.free_batch(batch);
        }

```

**File:** scheduler-bindings/src/lib.rs (L29-48)
```rust
//!               │        │                │
//!               │        │                │
//!           ┌───▼───┐ ┌──▼─────┐ ...  ┌───▼───┐
//!           │worker1│ │worker2 │      │workerN│
//!           └───────┘ └────────┘      └───────┘
//!
//! - [`TpuToPackMessage`] are sent from `tpu_to_pack` queue to the
//!   external scheduler process. This passes in tpu transactions to be scheduled,
//!   and optionally vote transactions.
//! - [`ProgressMessage`] are sent from `progress_tracker` queue to the
//!   external scheduler process. This passes information about leader status
//!   and slot progress to the external scheduler process.
//! - [`PackToWorkerMessage`] are sent from the external scheduler process
//!   to worker threads within agave. This passes a batch of transactions
//!   to be processed by the worker threads. This processing can also involve
//!   resolving the transactions' addresses, or similar operations beyond
//!   execution.
//! - [`WorkerToPackMessage`] are sent from worker threads within agave
//!   back to the external scheduler process. This passes back the results
//!   of processing the transactions.
```

**File:** scheduler-bindings/src/lib.rs (L211-243)
```rust
/// Maximum number of transactions allowed in a [`PackToWorkerMessage`].
/// If the number of transactions exceeds this value, agave will
/// not process the message.
//
// The reason for this constraint is because rts-alloc currently only
// supports up to 4096 byte allocations. We must ensure that the
// `TransactionResponseRegion` is able to contain responses for all
// transactions sent. This is a conservative bound.
pub const MAX_TRANSACTIONS_PER_MESSAGE: usize = 64;

/// Message: [Pack -> Worker]
/// External pack processe passes transactions to worker threads within agave.
///
/// These messages do not transfer ownership of the transactions.
/// The external pack process is still responsible for freeing the memory.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(C)]
pub struct PackToWorkerMessage {
    /// Flags on how to handle this message.
    /// See [`pack_message_flags`] for details.
    pub flags: u16,
    /// Maximum working bank slot that this message will be processed
    /// for. For execution, this will check the leader bank if it exists.
    /// If the working bank is ahead of the slot, the return message will
    /// be set with [`NOT_PROCESSED`].
    pub max_working_slot: u64,
    /// Offset and number of transactions in the batch.
    /// See [`SharableTransactionBatchRegion`] for details.
    /// Agave will return this batch in the response message, it is
    /// the responsibility of the external pack process to free the memory
    /// ONLY after receiving the response message.
    pub batch: SharableTransactionBatchRegion,
}
```
