Confirmed: `check_transactions` / `check_transaction_without_status_cache` in `runtime/src/bank/check_transactions.rs` only validate blockhash age, nonce validity, and status-cache duplication — they do not re-validate fee-payer affordability. This is used both at admission time (`receive_and_buffer.rs`) for age checks and at `incremental_recheck` in `scheduler_controller.rs` for periodic re-validation of already-buffered transactions.

#### Title
Stale fee-payer admission check allows txpool stuffing with unaffordable transactions that survive periodic recheck - (File: core/src/banking_stage/transaction_scheduler/scheduler_controller.rs, core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs)

#### Summary
Agave's banking-stage buffers transactions after a one-time affordability check performed at receive time (`Consumer::check_fee_payer_unlocked`, using the full fee including priority fee) [1](#0-0) . This check correctly incorporates the priority fee via `solana_fee::calculate_fee` [2](#0-1) , so unlike the Monad H-03 report, the base-fee-vs-full-bid mismatch itself does not exist at insertion. However, once a transaction is buffered, the only periodic re-validation performed on queued transactions is `incremental_recheck`, which calls `bank.check_transactions(...)` [3](#0-2) . That function and its underlying `check_transactions_with_processed_slots`/`check_transaction_without_status_cache` implementation in `runtime/src/bank/check_transactions.rs` only validate blockhash/nonce age and status-cache duplication — fee-payer balance is never rechecked here [4](#0-3) . A transaction that was affordable at receive time but whose fee-payer balance is later drained (e.g., by other transactions from the same address, or intentionally by an attacker submitting many transactions from a set of low-balance accounts) is not evicted by this sweep and remains queued at its original priority, occupying container capacity until it is actually pulled for execution.

#### Finding Description
The container's continuous "sweep" (`incremental_recheck`) is the closest analog to a repeated affordability gate, but it is scoped only to blockhash validity/duplication, not fee-payer solvency [5](#0-4) . A high-priority but insufficiently-funded transaction admitted once at receive time (when the account had just enough balance) will not be pruned even if its account balance subsequently drops below the fee requirement, because `check_transactions` performs no such check. It will only be discovered unaffordable when a `ConsumeWorker` actually attempts to schedule/execute it, at which point `validate_transaction_fee_payer`/`validate_fee_payer` in the SVM will reject it as `TransactionError::InsufficientFundsForFee` and return `TransactionValidationResult::Unprocessable` [6](#0-5) . Because this failure occurs deep in the execution pipeline rather than at admission or during periodic recheck, high-priority unaffordable transactions can occupy the front of the priority queue and be repeatedly pulled into consume-work batches, wasting scheduling/execution attempts, ahead of lower-priority-but-affordable transactions, until they are eventually dropped by whatever downstream disposition logic applies to `Unprocessable`/failed transactions.

#### Impact Explanation
This is a much narrower/softer analog than the Monad bug: Agave's insert-time check already uses the correct full-fee affordability formula (unlike Monad's base-fee-only gate), so there is no straightforward way to admit transactions that are unaffordable "by design" at zero cost the way the Monad attacker does. The gap here is limited to accounts whose balance changes *after* admission but before execution/sweep-eviction (e.g., balance-draining via a preceding transaction from the same fee payer, or through legitimate transfers out of the account). This could allow a moderately effective priority-queue-head occupation / minor block-building degradation, but it requires actively causing balance changes post-admission rather than a free, permanent, mass-fundable exploit as in Monad. I could not fully trace what happens after a batch returns `Unprocessable`/`InsufficientFundsForFee` from the consume worker (i.e., whether it's marked non-retryable and removed from the container, or requeued), due to running out of investigation budget — this materially affects the actual severity/exploitability.

#### Likelihood Explanation
Low-to-moderate. An attacker would need active, ongoing control of the fee payer's balance dynamics (e.g., interleaving spend transactions) to keep queued transactions perpetually "just became unaffordable," since a one-time balance drop would eventually get caught the next time that transaction is picked for execution and (presumably) removed. This is fundamentally different from the Monad case, where the false-affordable state is permanent and free to produce en masse.

#### Recommendation
Extend `incremental_recheck` (or a similar periodic sweep) in `scheduler_controller.rs` to also validate fee-payer affordability (e.g., via `Consumer::check_fee_payer_unlocked`) against the current working bank, not just blockhash/nonce/status-cache validity, and evict transactions that fail this check from the container rather than leaving them queued at stale priority.

#### Proof of Concept
Not independently verified end-to-end due to tool budget exhaustion; the gap is demonstrated structurally by comparing the fields checked in `check_transactions`/`check_transaction_without_status_cache` (age, nonce, status-cache) against the fee-payer balance check performed only at `receive_and_buffer.rs` insertion time and at final SVM execution (`validate_transaction_fee_payer`), with no fee-payer check present in the periodic `incremental_recheck` path in between.

### Citations

**File:** core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs (L332-340)
```rust
            // Check the transaction's fee-payer validates.
            if let Err(_err) = Consumer::check_fee_payer_unlocked(
                working_bank,
                state.transaction(),
                &mut error_counters,
            ) {
                receiving_stats.num_dropped_on_fee_payer += 1;
                continue;
            };
```

**File:** core/src/banking_stage/consumer.rs (L710-722)
```rust
    pub fn check_fee_payer_unlocked(
        bank: &Bank,
        transaction: &impl TransactionWithMeta,
        error_counters: &mut TransactionErrorMetrics,
    ) -> Result<(), TransactionError> {
        let fee_payer = transaction.fee_payer();
        let transaction_configuration = transaction.transaction_configuration(&bank.feature_set)?;
        let fee = solana_fee::calculate_fee(
            transaction,
            bank.fee_structure().lamports_per_signature,
            transaction_configuration.priority_fee_lamports,
            bank.fee_features(),
        );
```

**File:** core/src/banking_stage/transaction_scheduler/scheduler_controller.rs (L368-419)
```rust
    /// Incrementally recheck queued transactions for validity. A cursor walks the
    /// priority queue from highest to lowest priority. When the cursor reaches the end it
    /// wraps back to the top, continuously sweeping the queue.
    fn incremental_recheck(&mut self) {
        let bank = self.sharable_banks.working();

        // Walk the cursor to collect up to one chunk of valid IDs.
        self.recheck_chunk.clear();
        let mut last_seen = None;
        for id in self.container.recheck_iter(self.recheck_cursor.as_ref()) {
            last_seen = Some(*id);

            self.recheck_chunk.push(*id);
            if self.recheck_chunk.len() >= CHECK_CHUNK {
                break;
            }
        }

        // Update cursor: if we hit the chunk limit, continue from last seen;
        // otherwise we exhausted the range, so wrap back to start.
        self.recheck_cursor = if self.recheck_chunk.len() >= CHECK_CHUNK {
            last_seen
        } else {
            None
        };

        // Bail if no work to do (should only happen if container is empty).
        if self.recheck_chunk.is_empty() {
            return;
        }

        // Build our recheck batch & feed it through bank.
        let txs = {
            // NB: Always allocate a the same size chunk to help jemalloc predict us.
            let mut txs = Vec::with_capacity(CHECK_CHUNK);
            txs.extend(self.recheck_chunk.iter().map(|pid| {
                self.container
                    .get_transaction(pid.id)
                    .expect("transaction must exist")
            }));

            txs
        };
        let lock_results = [const { Ok(()) }; CHECK_CHUNK];
        let mut error_counters = TransactionErrorMetrics::default();
        let results = bank.check_transactions::<R::Transaction>(
            &txs,
            &lock_results[..txs.len()],
            bank.max_processing_age(),
            true,
            &mut error_counters,
        );
```

**File:** runtime/src/bank/check_transactions.rs (L53-70)
```rust
    pub fn check_transactions<Tx: TransactionWithMeta>(
        &self,
        sanitized_txs: &[impl core::borrow::Borrow<Tx>],
        lock_results: &[TransactionResult<()>],
        max_age: usize,
        strict_nonce_size_check: bool,
        error_counters: &mut TransactionErrorMetrics,
    ) -> Vec<TransactionCheckResult> {
        self.check_transactions_with_processed_slots(
            sanitized_txs,
            lock_results,
            max_age,
            false,
            strict_nonce_size_check,
            error_counters,
        )
        .0
    }
```

**File:** svm/src/transaction_processor.rs (L781-813)
```rust
    fn validate_transaction_fee_payer<CB: TransactionProcessingCallback>(
        account_loader: &mut AccountLoader<CB>,
        message: &impl SVMMessage,
        nonce_info: Option<NonceInfo>,
        compute_budget_and_limits: SVMTransactionExecutionAndFeeBudgetLimits,
        rent: &Rent,
        relax_post_exec_min_balance_check: bool,
        error_counters: &mut TransactionErrorMetrics,
    ) -> TransactionResult<ValidatedTransactionDetails> {
        let fee_payer_address = message.fee_payer();

        // We *must* use load_transaction_account() here because *this* is when the fee-payer
        // is loaded for the transaction. Transaction loading skips the first account and
        // loads (and thus inspects) all others normally.
        let Some(mut loaded_fee_payer) =
            account_loader.load_transaction_account(fee_payer_address, true)
        else {
            error_counters.account_not_found += 1;
            return Err(TransactionError::AccountNotFound);
        };

        let fee_payer_loaded_rent_epoch = loaded_fee_payer.account.rent_epoch();
        update_rent_exempt_status_for_account(rent, &mut loaded_fee_payer.account);

        let fee_payer_index = 0;
        validate_fee_payer(
            &mut loaded_fee_payer.account,
            fee_payer_index,
            error_counters,
            rent,
            compute_budget_and_limits.fee_details.total_fee(),
            relax_post_exec_min_balance_check,
        )?;
```
