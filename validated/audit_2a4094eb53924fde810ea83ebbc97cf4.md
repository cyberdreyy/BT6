A concrete analog exists: an allocation is made and then discarded on an error path without being freed, mirroring the SUNRPC `gss_import_v2_context` bug class (allocate-then-leak-on-error).

### Title
Memory leak in shared-memory allocator on external-scheduler check-batch error path - (`core/src/banking_stage/consume_worker.rs`)

### Summary
In the external-scheduler worker path, `ExternalWorker::check_batch` allocates a shared-memory response region up front and then calls `check_resolve_pubkeys`, which itself allocates per-transaction pubkey regions from the same shared `rts_alloc::Allocator`. If a later allocation inside the same batch fails, `check_resolve_pubkeys` returns `Err(ExternalConsumeWorkerError::AllocationFailure)`, which is propagated with `?` all the way out of `check_batch`. None of the previously made allocations (the top-level `responses_ptr` region, nor any `pubkeys_allocation` regions from earlier loop iterations) are freed on this path, permanently leaking shared-memory-allocator capacity — directly analogous to the kernel bug where `ctx->mech_used.data` from `kmemdup` was never freed on the error/return path of `gss_import_v2_context`.

### Finding Description
`check_batch` first allocates a response region: [1](#0-0) 

It then calls `check_resolve_pubkeys`, propagating any error with `?`: [2](#0-1) 

Inside `check_resolve_pubkeys`, for every transaction in the batch whose account keys exceed the static account keys (i.e., it uses an address lookup table), a new allocation is taken from the same shared allocator: [3](#0-2) 

If this `allocate()` call returns `None` for any transaction in the batch (allocator exhausted/fragmented), the function returns `Err(ExternalConsumeWorkerError::AllocationFailure)` immediately via `.ok_or(...)?`. Every `pubkeys_allocation` successfully made in prior loop iterations for earlier transactions in the same batch is dropped without ever being freed — the `NonNull` pointer is simply discarded when the stack unwinds. The same is true for the `responses_ptr`/`responses` region allocated earlier in `check_batch` (line 497-501): once the `?` at line 534 fires, that allocation is also never freed.

The codebase is explicitly aware that early-return paths must not leak shared allocator memory in the common case — see the adjacent `return_not_included_with_reason`, which documents (and accepts) skipping the free only because it's treated as an unrecoverable error: [4](#0-3) 

However, `check_batch`'s error path (triggered by transient/frequent allocator exhaustion under normal load, not just fatal conditions) has no equivalent handling, and unlike `return_not_included_with_reason`'s scenario, `AllocationFailure` here is a recoverable, repeatable condition that can occur many times during ordinary operation as batches with address-lookup-table transactions are processed.

### Impact Explanation
The shared allocator (`rts_alloc::Allocator`) backing the external-scheduler IPC channel is a fixed-size arena (e.g., sized via `allocator_size` at session setup). Each leaked allocation permanently reduces the arena's usable capacity for the lifetime of the worker process, since the memory is never returned to the free list. Under sustained transaction traffic containing address-lookup-table transactions, repeated triggering of this leak path progressively exhausts the shared arena, eventually causing `AllocationFailure` for all subsequent check/execute requests on that worker — degrading or halting the leader's transaction checking/consuming pipeline for that worker thread over time (an availability-only impact, matching the CVSS profile of the analog: C:N/I:N/A:H).

### Likelihood Explanation
The leak is deterministic whenever a batch contains multiple address-lookup-table transactions and any one allocation attempt after the first legitimately fails (arena has finite headroom and address-lookup-table pubkey buffers scale with account-key counts). Any transaction sender can construct transactions using address lookup tables, and an attacker sending high volumes of such transactions can accelerate arena exhaustion, making repeated triggering of the leak path practical over time rather than requiring a rare or privileged condition.

### Recommendation
On the error path in `check_resolve_pubkeys`, track and free any `pubkeys_allocation`s made in prior loop iterations before returning `Err`. Similarly, in `check_batch`, free the `responses_ptr`/`responses` allocation if `check_resolve_pubkeys` (or any subsequent step before the final `try_write`) returns an error, mirroring the cleanup discipline already used elsewhere (e.g., `drop_transaction`'s explicit `free` calls in `scheduling-utils/src/bridge/bindings.rs`). Consider wrapping these allocations in an RAII guard tied to the shared allocator so early returns automatically free them, eliminating this class of leak entirely.

### Proof of Concept
1. Configure/attach an external scheduler session with a bounded `allocator_size` (as in the test harness `setup_external_test_frame_disable_features`).
2. Submit a batch (`PackToWorkerMessage`) with `check_flags::LOAD_ADDRESS_LOOKUP_TABLES` set, containing many transactions that reference address lookup tables (large `account_keys.len() - num_static_account_keys`), sized so cumulative pubkey-buffer allocations approach the allocator's capacity.
3. Repeat step 2 across multiple check-batch calls; each time an allocation in `check_resolve_pubkeys` fails partway through a batch (line 715-718), the earlier successful allocations in that batch (and the batch's `responses_ptr` from `check_batch`, line 497-501) are leaked rather than freed.
4. Observe that the shared allocator's available capacity monotonically shrinks with each triggered failure, eventually causing persistent `AllocationFailure` for all future check/execute requests on that worker. [5](#0-4) [6](#0-5)

### Citations

**File:** core/src/banking_stage/consume_worker.rs (L467-557)
```rust
        fn check_batch(
            &mut self,
            message: &PackToWorkerMessage,
        ) -> Result<(), ExternalConsumeWorkerError> {
            let BankPair {
                root_bank,
                working_bank,
            } = self.sharable_banks.load();
            // Prefer the leader bank over the highest working fork when leader
            let working_bank = active_leader_state(&self.shared_leader_state)
                .and_then(|leader_state| leader_state.working_bank().cloned())
                .unwrap_or(working_bank);

            if working_bank.slot() > message.max_working_slot {
                return self.return_unprocessed_message(
                    message,
                    processed_codes::MAX_WORKING_SLOT_EXCEEDED,
                );
            }

            // SAFETY: Assumption that external scheduler does not pass messages with batch regions
            //         not pointing to valid regions in the allocator.
            let batch = unsafe {
                TransactionPtrBatch::from_sharable_transaction_batch_region(
                    &message.batch,
                    &self.allocator,
                )
            };

            // Allocate space for all responses.
            let (responses_ptr, responses) = allocate_check_response_region(
                &self.allocator,
                usize::from(message.batch.num_transactions),
            )
            .ok_or(ExternalConsumeWorkerError::AllocationFailure)?;

            // SAFETY: responses_ptr is sufficiently sized and aligned.
            let (parsing_results, parsed_transactions, response_slice) = unsafe {
                Self::parse_transactions_and_populate_initial_check_responses(
                    message,
                    &batch,
                    responses_ptr,
                )
            };

            // Check fee-payer if requested.
            if message.flags & check_flags::LOAD_FEE_PAYER_BALANCE != 0 {
                Self::check_load_fee_payer_balance(
                    &parsing_results,
                    &parsed_transactions,
                    response_slice,
                    &working_bank,
                );
            }

            // Do resolving next since we (currently) need resolved transactions for status checks.
            let (parsing_and_resolve_results, txs, max_ages) =
                Self::translate_transaction_batch(&batch, &root_bank);

            if message.flags & check_flags::LOAD_ADDRESS_LOOKUP_TABLES != 0 {
                self.check_resolve_pubkeys(
                    &parsing_results,
                    &parsing_and_resolve_results,
                    &txs,
                    &max_ages,
                    response_slice,
                    root_bank.slot(),
                )?;
            }

            if message.flags & check_flags::STATUS_CHECKS != 0 {
                Self::check_status_checks(
                    &parsing_and_resolve_results,
                    &txs,
                    response_slice,
                    &working_bank,
                );
            }

            let response = WorkerToPackMessage {
                batch: message.batch,
                processed_code: agave_scheduler_bindings::processed_codes::PROCESSED,
                responses,
            };

            self.sender
                .try_write(response)
                .map_err(|_| ExternalConsumeWorkerError::SenderDisconnected)?;

            Ok(())
        }
```

**File:** core/src/banking_stage/consume_worker.rs (L659-663)
```rust
            // Should de-allocate the memory, but this is a non-recoverable
            // error and so it's not needed.
            self.sender
                .try_write(response_message)
                .map_err(|_| ExternalConsumeWorkerError::SenderDisconnected)?;
```

**File:** core/src/banking_stage/consume_worker.rs (L668-752)
```rust
        fn check_resolve_pubkeys(
            &self,
            parsing_results: &[Result<(), TransactionViewError>],
            parsing_and_resolve_results: &[Result<(), PacketHandlingError>],
            txs: &[Tx],
            max_ages: &[MaxAge],
            responses: &mut [CheckResponse],
            resolution_slot: Slot,
        ) -> Result<(), ExternalConsumeWorkerError> {
            assert_eq!(parsing_results.len(), parsing_and_resolve_results.len());
            assert_eq!(parsing_results.len(), responses.len());

            let mut resolved_transaction_iter = txs.iter();
            let mut max_age_iter = max_ages.iter();
            for (transaction_index, (parsing_result, parsing_and_resolve_results)) in
                parsing_results
                    .iter()
                    .zip(parsing_and_resolve_results.iter())
                    .enumerate()
            {
                if parsing_result.is_err() {
                    continue;
                }

                let response = &mut responses[transaction_index];
                response.resolve_flags |= resolve_flags::PERFORMED;
                if parsing_and_resolve_results.is_err() {
                    response.resolve_flags |= resolve_flags::FAILED;
                    continue;
                }

                let transaction = resolved_transaction_iter.next().expect(
                    "resolved_transaction_iter iterator must contain element for each sent parsed \
                     transaction",
                );
                let max_age = max_age_iter.next().expect(
                    "max_age_iter iterator must contain element for each sent parsed transaction",
                );

                // Address table lookups are sanitized to contain at least one account, so there
                // are loaded keys exactly when account keys outnumber static account keys.
                let account_keys = transaction.account_keys();
                let num_static_account_keys = transaction.static_account_keys().len();
                let (sharable_keys, alt_invalidation_slot) = if account_keys.len()
                    > num_static_account_keys
                {
                    let num_pubkeys = account_keys.len().wrapping_sub(num_static_account_keys);
                    let pubkeys_allocation = self
                        .allocator
                        .allocate(num_pubkeys.wrapping_mul(core::mem::size_of::<Pubkey>()) as u32)
                        .ok_or(ExternalConsumeWorkerError::AllocationFailure)?
                        .cast();
                    // SAFETY: non-overlapping and appropriately sized.
                    unsafe {
                        Self::copy_loaded_addresses(
                            account_keys.iter().skip(num_static_account_keys),
                            pubkeys_allocation,
                        )
                    };
                    // SAFETY: pubkeys_allocation was allocated by allocator
                    let offset = unsafe { self.allocator.offset(pubkeys_allocation.cast()) };
                    (
                        SharablePubkeys {
                            offset,
                            num_pubkeys: num_pubkeys as u32,
                        },
                        max_age.alt_invalidation_slot,
                    )
                } else {
                    (
                        SharablePubkeys {
                            offset: 0,
                            num_pubkeys: 0,
                        },
                        u64::MAX,
                    )
                };

                response.resolution_slot = resolution_slot;
                response.resolved_pubkeys = sharable_keys;
                response.min_alt_deactivation_slot = alt_invalidation_slot;
            }

            Ok(())
        }
```
