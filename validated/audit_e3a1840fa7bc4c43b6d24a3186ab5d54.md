### Title
Durable-nonce transactions are charged fees using the current (execution-time) `lamports_per_signature` instead of the rate that was locked in when the nonce was advanced — ([File: runtime/src/bank/check_transactions.rs])

### Summary
The GMX bug (`M-6`) is a "value-at-execution vs. value-at-submission" mismatch: an order signed/submitted at time T gets filled using whatever oracle price is current at T+N, rather than the price the user agreed to at T. Agave's durable-nonce mechanism has the same structural flaw for fee accounting: a nonce account snapshots `lamports_per_signature` at the moment the nonce was advanced (this is what the transaction signer implicitly commits to), but the fee actually deducted from the fee payer is computed from the bank's *current* `fee_structure.lamports_per_signature` at check/execution time, and the nonce-stored rate is discarded.

### Finding Description
When a nonce transaction's validity is checked, `check_nonce_transaction_validity` retrieves the fee rate recorded in the nonce account at the time it was advanced: [1](#0-0) 

but its caller, `check_transaction_age`, throws that value away and keeps only the nonce address: [2](#0-1) 

Meanwhile, the actual fee that will be deducted is computed earlier in `check_age_and_compute_budget_limits` using `self.fee_structure.lamports_per_signature` — the *bank's current* value at the moment the transaction is checked/processed, not the value stored in the nonce account when the transaction was authorized: [3](#0-2) 

This `fee_details` is packaged into `CheckedTransactionDetails` and flows unmodified into `validate_transaction_nonce_and_fee_payer` / `validate_transaction_fee_payer`, which deducts it from the fee payer: [4](#0-3) [5](#0-4) 

The nonce account's own stored `lamports_per_signature` (returned by `nonce_data.get_lamports_per_signature()`) is only referenced in `get_fee_for_message` for user-facing fee *estimation* (`bank.rs:3346-3365`), not in the actual charge path used during transaction checking/commit: [6](#0-5) 

So a user who signs a durable-nonce transaction is implicitly relying on the fee rate captured in the nonce account (the value "at submission time," analogous to GMX's oracle-archive price), but if that transaction sits in a keeper/queue/mempool and is later included in a slot where the bank's `fee_structure.lamports_per_signature` differs, they are charged the current rate instead — exactly the "delayed execution uses current value, not submission-time value" bug class from the GMX report.

### Impact Explanation
This is a fee/nonce accounting discrepancy reachable from an ordinary user's signed transaction (no special privileges required). If the network's fee schedule changes between when a durable-nonce transaction is authorized and when it is actually included, the fee payer is charged an amount they never explicitly authorized via the nonce-locked rate, and the value recorded in the nonce account is silently discarded rather than being honored. This causes unauthorized-amount fund deduction relative to what the transaction signer committed to (either over- or under-charging), which is state-mutation/fund-impact in the fee/nonce accounting category explicitly in scope.

### Likelihood Explanation
Historically Agave's `lamports_per_signature` was designed to be dynamic (fee-rate governor targets, `FeeCalculator` per blockhash/per-nonce), and the code still fully implements and tests this per-blockhash/per-nonce fee-rate model (`get_lamports_per_signature_for_blockhash`, nonce `fee_calculator` fields, `test_nonce_fee_calculator_updates*`). Under current mainnet configuration the network fee rate is effectively fixed, which limits real-world exploitability today, but the vulnerable code path (discarding the nonce's captured rate in favor of the bank's current rate) is unconditional and would immediately manifest as an accounting bug the moment fee-rate variability is reintroduced or under any validator/cluster configuration where `fee_structure.lamports_per_signature` is not static.

### Recommendation
When processing a durable-nonce transaction, use the `lamports_per_signature` value stored in the nonce account (returned by `check_nonce_transaction_validity`) to compute the fee charged, rather than (or in addition to consistency-checking against) the bank's current `fee_structure.lamports_per_signature`. At minimum, ensure the previously-fetched `previous_lamports_per_signature` in `check_transaction_age` is actually threaded through to fee computation instead of being discarded.

### Proof of Concept
1. Set up a bank with `fee_rate_governor`/`fee_structure.lamports_per_signature = X`.
2. Create and advance a nonce account, recording `lamports_per_signature = X` in the nonce data (as in `test_nonce_fee_calculator_updates`, `runtime/src/bank/tests.rs:4507-4569`).
3. Sign a nonce transaction using that nonce hash.
4. Before the transaction is processed, change the bank's fee structure to `Y != X` (simulating a fee-rate-governor adjustment slot boundary, or a validator configured with a different `fee_structure`).
5. Process the transaction: `check_age_and_compute_budget_limits` computes `fee_details` from the bank's *current* `self.fee_structure.lamports_per_signature = Y`, not from the nonce-stored `X`, and the fee payer is charged based on `Y` — demonstrating that the value locked in at "submission time" (nonce advance) is ignored in favor of the value at "execution time" (check time).

### Citations

**File:** runtime/src/bank/check_transactions.rs (L167-207)
```rust
        sanitized_txs
            .iter()
            .zip(lock_results)
            .map(|(tx, lock_res)| match lock_res {
                Ok(()) => {
                    let compute_budget_and_limits = tx
                        .borrow()
                        .transaction_configuration(feature_set)
                        .map(|config| {
                            let fee_details = calculate_fee_details(
                                tx.borrow(),
                                self.fee_structure.lamports_per_signature,
                                config.priority_fee_lamports,
                                fee_features,
                            );
                            if let Some(compute_budget) = self.compute_budget {
                                // This block of code is only necessary to retain legacy behavior of the code.
                                // It should be removed along with the change to favor transaction's compute budget limits
                                // over configured compute budget in Bank.
                                compute_budget.get_compute_budget_and_limits(
                                    config.loaded_accounts_data_size_limit,
                                    fee_details,
                                )
                            } else {
                                SVMTransactionExecutionAndFeeBudgetLimits {
                                    budget: SVMTransactionExecutionBudget {
                                        compute_unit_limit: u64::from(config.compute_unit_limit),
                                        heap_size: config.updated_heap_bytes,
                                        ..SVMTransactionExecutionBudget::new_with_defaults(
                                            raise_cpi_limit,
                                        )
                                    },
                                    loaded_accounts_data_size_limit: config
                                        .loaded_accounts_data_size_limit,
                                    fee_details,
                                }
                            }
                        })
                        .inspect_err(|_err| {
                            error_counters.invalid_compute_budget += 1;
                        })?;
```

**File:** runtime/src/bank/check_transactions.rs (L238-256)
```rust
    ) -> TransactionResult<Option<Pubkey>> {
        let recent_blockhash = tx.recent_blockhash();
        if hash_queue
            .get_hash_info_if_valid(recent_blockhash, max_age)
            .is_some()
        {
            Ok(None)
        } else if let Some((nonce_address, _)) = self.check_nonce_transaction_validity(
            tx,
            next_durable_nonce,
            strict_nonce_size_check,
            strict_nonce_authority_check,
        ) {
            Ok(Some(nonce_address))
        } else {
            error_counters.blockhash_not_found += 1;
            Err(TransactionError::BlockhashNotFound)
        }
    }
```

**File:** runtime/src/bank/check_transactions.rs (L258-284)
```rust
    pub(super) fn check_nonce_transaction_validity(
        &self,
        message: &impl SVMMessage,
        next_durable_nonce: &DurableNonce,
        strict_nonce_size_check: bool,
        strict_nonce_authority_check: bool,
    ) -> Option<(Pubkey, u64)> {
        let nonce_is_advanceable = message.recent_blockhash() != next_durable_nonce.as_hash();
        if !nonce_is_advanceable {
            return None;
        }

        let (nonce_address, nonce_data) =
            self.load_message_nonce_data(message, strict_nonce_size_check)?;

        if strict_nonce_authority_check
            && !message
                .get_ix_signers(NONCED_TX_MARKER_IX_INDEX as usize)
                .any(|signer| signer == &nonce_data.authority)
        {
            return None;
        }

        let previous_lamports_per_signature = nonce_data.get_lamports_per_signature();

        Some((nonce_address, previous_lamports_per_signature))
    }
```

**File:** svm/src/transaction_processor.rs (L694-731)
```rust
    fn validate_transaction_nonce_and_fee_payer<CB: TransactionProcessingCallback>(
        account_loader: &mut AccountLoader<CB>,
        message: &impl SVMMessage,
        checked_details: CheckedTransactionDetails,
        environment_blockhash: &Hash,
        next_lamports_per_signature: u64,
        rent: &Rent,
        relax_post_exec_min_balance_check: bool,
        strict_nonce_size_check: bool,
        error_counters: &mut TransactionErrorMetrics,
    ) -> TransactionValidationResult {
        let CheckedTransactionDetails {
            nonce_address,
            compute_budget_and_limits,
        } = checked_details;

        // If this is a nonce transaction, validate the nonce info.
        // This must be done for every transaction to support SIMD83 because
        // it may have changed due to use, authorization, or deallocation.
        let nonce_info = if let Some(ref nonce_address) = nonce_address {
            let next_durable_nonce = DurableNonce::from_blockhash(environment_blockhash);
            let nonce_result = Self::validate_transaction_nonce(
                account_loader,
                message,
                nonce_address,
                &next_durable_nonce,
                next_lamports_per_signature,
                strict_nonce_size_check,
                error_counters,
            );

            match nonce_result {
                Ok(nonce_info) => Some(nonce_info),
                Err(e) => return TransactionValidationResult::Unprocessable(e),
            }
        } else {
            None
        };
```

**File:** svm/src/transaction_processor.rs (L778-822)
```rust
    // Loads transaction fee payer, collects rent if necessary, then calculates
    // transaction fees, and deducts them from the fee payer balance. If the
    // account is not found or has insufficient funds, an error is returned.
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

        // Capture fee-subtracted fee payer account and next nonce account state
        // to commit if transaction execution fails.
        let rollback_accounts = RollbackAccounts::new(
            nonce_info,
            *fee_payer_address,
            loaded_fee_payer.account.clone(),
            fee_payer_loaded_rent_epoch,
        );
```

**File:** runtime/src/bank.rs (L3332-3366)
```rust
    pub fn get_lamports_per_signature(&self) -> u64 {
        self.fee_rate_governor.lamports_per_signature
    }

    /// Convert Agave's active feature set into the fee crate's narrowed feature view.
    pub fn fee_features(&self) -> FeeFeatures {
        FeeFeatures {}
    }

    pub fn get_lamports_per_signature_for_blockhash(&self, hash: &Hash) -> Option<u64> {
        let blockhash_queue = self.blockhash_queue.read().unwrap();
        blockhash_queue.get_lamports_per_signature(hash)
    }

    pub fn get_fee_for_message(&self, message: &SanitizedMessage) -> Option<u64> {
        {
            let blockhash_queue = self.blockhash_queue.read().unwrap();
            blockhash_queue.get_lamports_per_signature(message.recent_blockhash())
        }
        .or_else(|| {
            self.load_message_nonce_data(message, false)
                .map(|(_nonce_address, nonce_data)| nonce_data.get_lamports_per_signature())
        })?;

        let transaction_configuration =
            TransactionConfiguration::try_from_sanitized_message(message, &self.feature_set)
                .ok()?;
        Some(solana_fee::calculate_fee(
            message,
            self.fee_structure().lamports_per_signature,
            transaction_configuration.priority_fee_lamports,
            self.fee_features(),
        ))
    }

```
