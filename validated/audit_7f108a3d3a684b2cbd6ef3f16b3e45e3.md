### Title
`getMultipleAccounts` RPC handler aborts the entire batch response if a single account fails to encode - (File: `rpc/src/rpc.rs`)

### Summary
The reported Solidity bug is a "one bad item blocks the whole batch" pattern: `_settle()` in `GasAccounting.sol` reverts the entire `metacall()` when a single solver can't pay, instead of isolating that solver's failure so the remaining, valid solvers can still be processed. The same structural pattern — a per-item operation whose failure aborts the whole batch via early propagation — exists in agave's `getMultipleAccounts` JSON-RPC handler.

### Finding Description
`JsonRpcRequestProcessor::get_multiple_accounts` iterates over the caller-supplied pubkey list and, for each pubkey, spawns a blocking task that calls `get_encoded_account`, immediately propagating any error with `?`: [1](#0-0) 

Unlike the "account not found" case (which is represented as `Ok(None)` and does not abort the loop), any error returned from account encoding (for example, an encoding-specific failure such as the well-known base58 length restriction, or other `encode_account`/`get_encoded_account` failure paths) short-circuits the whole loop via the `?` operator. This is the identical shape as the reported `_settle()` bug: a per-item failure (one "solver"/one account) is not isolated, and instead aborts processing of the remaining, otherwise-valid items in the same batch/list, causing the entire `getMultipleAccounts` RPC call to fail even though most of the requested accounts could have been successfully returned.

The related `get_program_accounts` path shows the same idiom — the whole `Vec<RpcKeyedAccount>` construction is collected with `.collect::<Result<Vec<_>>>()?`, so one bad encoding among many matching accounts fails the entire response: [2](#0-1) 

This contrasts with the analogous but correctly-isolated error handling pattern the report calls out as the expected behavior (failure of one item does not block the others), which agave itself uses elsewhere, e.g. transaction-batch processing where a single transaction's failure is isolated per-index rather than failing the whole batch unless `all_or_nothing` is explicitly requested: [3](#0-2) 

### Impact Explanation
An unprivileged RPC caller can craft a `getMultipleAccounts` (or `getProgramAccounts`) request containing a mix of pubkeys where one triggers an encoding error, causing the whole response to fail with an RPC error instead of returning the data for the other, valid pubkeys. This is a functional/availability degradation of a single JSON-RPC call (wrong/no data returned for a batch that should have partially succeeded), not a validator crash, consensus mutation, or unbounded-cost issue, so the severity is bounded to a single, low-cost RPC request producing an incorrect (all-or-nothing) response rather than the expected partial result.

### Likelihood Explanation
Any client sending `getMultipleAccounts`/`getProgramAccounts` with a batch that includes one encoding-incompatible account (e.g., requesting base58 encoding for an oversized account) will reliably trigger this, since the loop uses `?` without per-item error isolation.

### Recommendation
Change `get_multiple_accounts` (and similarly `get_program_accounts`'s account-encoding step) to isolate per-account encoding failures, e.g., convert individual encoding errors into `None`/error markers within the returned vector instead of propagating with `?`, mirroring how "account not found" is already represented as `Ok(None)` rather than as a hard error.

### Proof of Concept
1. Store several small, easily-encodable accounts plus one very large account under distinct pubkeys.
2. Call `getMultipleAccounts` with `encoding: "base58"` and a pubkey list containing the large account together with the small ones.
3. Observe that the RPC call fails entirely with an encoding error, even though the small accounts would have encoded successfully; compare this with the expected behavior where only the problematic account's entry should be affected while the rest of the batch succeeds.

### Citations

**File:** rpc/src/rpc.rs (L562-591)
```rust
    pub async fn get_multiple_accounts(
        &self,
        pubkeys: Vec<Pubkey>,
        config: Option<RpcAccountInfoConfig>,
    ) -> Result<RpcResponse<Vec<Option<UiAccount>>>> {
        let RpcAccountInfoConfig {
            encoding,
            data_slice,
            commitment,
            min_context_slot,
        } = config.unwrap_or_default();
        let bank = self.get_bank_with_config(RpcContextConfig {
            commitment,
            min_context_slot,
        })?;
        let encoding = encoding.unwrap_or(UiAccountEncoding::Base64);

        let mut accounts = Vec::with_capacity(pubkeys.len());
        for pubkey in pubkeys {
            let bank = Arc::clone(&bank);
            accounts.push(
                self.runtime
                    .spawn_blocking(move || {
                        get_encoded_account(&bank, &pubkey, encoding, data_slice, None)
                    })
                    .await
                    .expect("rpc: get_encoded_account panicked")?,
            );
        }
        Ok(new_response(&bank, accounts))
```

**File:** rpc/src/rpc.rs (L656-666)
```rust
        } else {
            keyed_accounts
                .into_iter()
                .map(|(pubkey, account)| {
                    Ok(RpcKeyedAccount {
                        pubkey: pubkey.to_string(),
                        account: encode_account(&account, &pubkey, encoding, data_slice_config)?,
                    })
                })
                .collect::<Result<Vec<_>>>()?
        };
```

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
