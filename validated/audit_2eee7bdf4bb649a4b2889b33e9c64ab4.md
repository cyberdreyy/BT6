### Title
Stale address-lookup-table resolution in `resanitize_transaction_minimally`/banking-stage pipeline causes leader/replay bank-hash divergence - (File: `core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs`, `runtime/src/bank.rs`)

### Summary
Address Lookup Table (ALT) resolution for a v0 transaction is performed once, at ingestion time, by `load_addresses_for_view`/`Bank::load_addresses_from_ref`, and the resulting `LoadedAddresses` are baked immutably into the `RuntimeTransaction<ResolvedTransactionView<..>>` used for scheduling and execution. When the transaction is later actually committed (potentially many slots afterward), `Bank::resanitize_transaction_minimally` only re-derives addresses to check that resolution *still succeeds*, then discards the result and continues executing with the original, possibly stale, resolved keys. An attacker who fully controls the lifecycle of the referenced ALT account (close it, then recreate it at the same pubkey with different addresses) can make the leader execute/commit a transaction against different account keys than what any other validator replaying the same block bytes would independently resolve at that point in the canonical transaction order, producing a bank hash mismatch.

### Finding Description
`translate_to_runtime_view` (called from `TransactionViewReceiveAndBuffer::try_handle_packet` and from `consume_worker.rs::translate_transaction`) resolves ALT addresses exactly once via: [1](#0-0) 

using the bank state visible **at ingestion time** (`root_bank`/`working_bank` at buffering, or the check-worker's bank). The resulting `LoadedAddresses` are baked immutably into the `ResolvedTransactionView`'s `account_keys()`: [2](#0-1) 

The `MaxAge`/`alt_invalidation_slot` computed at that point is only a *lower bound estimate* on when the ALT might deactivate (see `calculate_max_age` docs): [3](#0-2) 

When the transaction is finally committed (potentially several slots later, via `process_and_record_aged_transactions`), `Bank::resanitize_transaction_minimally` is invoked: [4](#0-3) 

but its ALT re-check is gated by `self.slot() > alt_invalidation_slot` and, critically, **discards** the freshly-resolved addresses instead of using them for execution: [5](#0-4) 

The actual execution path (`execute_and_commit_transactions_locked` → `bank.load_and_execute_transactions`) operates on `batch.sanitized_transactions()`, which is the *same* pre-resolved `RuntimeTransaction` object created at ingestion — never re-resolved: [6](#0-5) 

By contrast, the canonical/replay path resolves ALTs fresh from the raw wire bytes (which only carry ALT indices, not baked pubkeys) via `Bank::load_addresses_from_ref`/`AddressLoader for &Bank`, using the replaying bank's own ancestors at the exact point in the deterministic transaction sequence: [7](#0-6) 

If an attacker (owner/authority of their own ALT account) closes the ALT and recreates it at the same pubkey with different addresses *after* a transaction referencing it has been ingested and resolved by the leader's banking stage but *before* that transaction is actually committed by the leader, the leader will commit account-state changes using the **stale** addresses resolved at ingestion. Any other node that later replays the same block from raw entry bytes resolves the ALT lookup **fresh**, at the point in canonical order where the ALT account state may already differ (because the attacker's close/recreate can be sequenced as a prior transaction within the same block, or is simply the current on-chain state by the time of independent verification). This produces divergent account keys / state changes for the same transaction and slot — the exact scenario proposed by the audit question. Existing checks (`validate_account_locks`, `check_reserved_keys`, `resanitize_transaction_minimally`'s existence check) verify that resolution still *succeeds* structurally, but never re-align the value used for execution with what a fresh resolution at commit time would produce, unless slot > alt_invalidation_slot forces a resanitize whose result is still not consumed.

### Impact Explanation
This is a determinism/consensus-divergence bug: a leader can commit a block using ALT-resolved addresses that other validators replaying the identical block bytes will not reproduce, causing a bank hash mismatch. This falls squarely under the "divergent bank hash or execution result" bounty category — it is a live-cluster consensus safety issue reachable purely through normal transaction submission plus ownership/control of one's own ALT account (no privileged capability required).

### Likelihood Explanation
Preconditions are fully within reach of an unprivileged attacker: create an ALT with the extend/deactivate/close instructions (standard `address-lookup-table-program` operations available to any account owner), submit a v0 transaction referencing it, then close and recreate the ALT at the same pubkey with different addresses before the transaction is dequeued/committed by the leader (a window that can span multiple slots, bounded only by `alt_invalidation_slot`/blockhash validity, and unaffected at all when `slot() <= alt_invalidation_slot`, in which case no re-check whatsoever occurs). Timing precision needed is loose because the entire "hold" window in banking stage (which can be many slots for low-priority or held transactions) is available, and the check in `resanitize_transaction_minimally` explicitly does not correct for it even when triggered.

### Recommendation
Re-resolve ALT addresses immediately before execution/commit (inside `execute_and_commit_transactions_locked` or `resanitize_transaction_minimally`) and use the freshly resolved `LoadedAddresses` to rebuild the transaction's account key list for locking/execution, rather than discarding the result of the re-resolution check. Alternatively, reject/retry any transaction whose freshly re-resolved addresses differ from the ones cached at ingestion, rather than only checking for resolution success/failure.

### Proof of Concept
```rust
// Sketch: resolve the same tx bytes against two bank snapshots of the same ALT pubkey
// with different contents, and show load_addresses_for_view yields different LoadedAddresses.
let bank_before = /* bank where ALT at `alt_key` contains [x] */;
let bank_after  = /* bank where ALT at `alt_key` was closed and recreated with [y] */;

let (view, _) = translate_to_runtime_view(tx_bytes.clone(), &bank_before, limit, &cfg).unwrap();
let addrs_before = view.account_keys().to_vec();

let (view2, _) = translate_to_runtime_view(tx_bytes, &bank_after, limit, &cfg).unwrap();
let addrs_after = view2.account_keys().to_vec();

assert_ne!(addrs_before, addrs_after); // demonstrates non-determinism across resolution times

// Then: bank.resanitize_transaction_minimally(&tx_with_addrs_before, epoch, alt_invalidation_slot)
// against `bank_after` returns Ok(()) as long as indices still resolve (into [y]),
// yet execution proceeds using addrs_before ([x]) — the stale set — not addrs_after.
```

### Citations

**File:** core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs (L439-447)
```rust
    let (loaded_addresses, deactivation_slot) = load_addresses_for_view(&view, bank)?;

    let Ok(view) = RuntimeTransaction::<ResolvedTransactionView<_>>::try_new(
        view,
        loaded_addresses,
        bank.get_reserved_account_keys(),
    ) else {
        return Err(PacketHandlingError::Sanitization);
    };
```

**File:** core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs (L474-499)
```rust
/// Given the epoch, the minimum deactivation slot, and the current slot,
/// return the `MaxAge` that should be used for the transaction. This is used
/// to determine the maximum slot that a transaction will be considered valid
/// for, without re-resolving addresses or resanitizing.
///
/// This function considers the deactivation period of Address Table
/// accounts. If the deactivation period runs past the end of the epoch,
/// then the transaction is considered valid until the end of the epoch.
/// Otherwise, the transaction is considered valid until the deactivation
/// period.
///
/// Since the deactivation period technically uses blocks rather than
/// slots, the value used here is the lower-bound on the deactivation
/// period, i.e. the transaction's address lookups are valid until
/// AT LEAST this slot.
fn calculate_max_age(
    sanitized_epoch: Epoch,
    deactivation_slot: Slot,
    current_slot: Slot,
) -> MaxAge {
    let alt_min_expire_slot = estimate_last_valid_slot(deactivation_slot.min(current_slot));
    MaxAge {
        sanitized_epoch,
        alt_invalidation_slot: alt_min_expire_slot,
    }
}
```

**File:** runtime-transaction/src/runtime_transaction/transaction_view.rs (L123-143)
```rust
impl<D: TransactionData> RuntimeTransaction<ResolvedTransactionView<D>> {
    /// Create a new `RuntimeTransaction<ResolvedTransactionView>` from a
    /// `RuntimeTransaction<SanitizedTransactionView>` that already has
    /// static metadata loaded.
    pub fn try_new(
        statically_loaded_runtime_tx: RuntimeTransaction<SanitizedTransactionView<D>>,
        loaded_addresses: Option<LoadedAddresses>,
        reserved_account_keys: &HashSet<Pubkey>,
    ) -> Result<Self> {
        let RuntimeTransaction { transaction, meta } = statically_loaded_runtime_tx;
        // transaction-view does not distinguish between different types of errors here.
        // return generic sanitize failure error here.
        // these transactions should be immediately dropped, and we generally
        // will not care about the specific error at this point.
        let transaction =
            ResolvedTransactionView::try_new(transaction, loaded_addresses, reserved_account_keys)
                .map_err(|_| TransactionError::SanitizeFailure)?;
        let tx = Self { transaction, meta };
        Ok(tx)
    }
}
```

**File:** core/src/banking_stage/consumer.rs (L179-197)
```rust
    pub fn process_and_record_aged_transactions(
        &self,
        bank: &Bank,
        txs: &[impl TransactionWithMeta],
        max_ages: &[MaxAge],
        flags: &ExecutionFlags,
    ) -> ProcessTransactionBatchOutput {
        // Need to filter out transactions since they were sanitized earlier.
        // This means that the transaction may cross and epoch boundary (not allowed),
        //  or account lookup tables may have been closed.
        let pre_results = txs.iter().zip(max_ages).map(|(tx, max_age)| {
            bank.resanitize_transaction_minimally(
                tx,
                max_age.sanitized_epoch,
                max_age.alt_invalidation_slot,
            )
        });
        self.process_and_record_transactions_with_pre_results(bank, txs, pre_results, flags)
    }
```

**File:** core/src/banking_stage/consumer.rs (L234-288)
```rust
    fn execute_and_commit_transactions_locked(
        &self,
        bank: &Bank,
        batch: &TransactionBatch<impl TransactionWithMeta>,
        flags: &ExecutionFlags,
    ) -> ExecuteAndCommitTransactionsOutput {
        let transaction_status_sender_enabled = self.committer.transaction_status_sender_enabled();
        let mut execute_and_commit_timings = LeaderExecuteAndCommitTimings::default();

        let mut error_counters = TransactionErrorMetrics::default();
        let mut retryable_transaction_indexes: Vec<_> = batch
            .lock_results()
            .iter()
            .enumerate()
            .filter_map(|(index, res)| match res {
                // Account lock conflicts are immediately retryable.
                Err(TransactionError::AccountInUse) => {
                    error_counters.account_in_use += 1;
                    // locking failure due to vote conflict or jito - immediately retry.
                    Some(RetryableIndex {
                        index,
                        immediately_retryable: true,
                    })
                }
                // following are non-retryable errors
                Err(TransactionError::TooManyAccountLocks) => {
                    error_counters.too_many_account_locks += 1;
                    None
                }
                Err(_) => None,
                Ok(_) => None,
            })
            .collect();

        let (load_and_execute_transactions_output, load_execute_us) =
            measure_us!(bank.load_and_execute_transactions(
                batch,
                bank.max_processing_age(),
                &mut execute_and_commit_timings.execute_timings,
                &mut error_counters,
                TransactionProcessingConfig {
                    account_overrides: None,
                    check_program_deployment_slot: bank.check_program_deployment_slot(),
                    log_messages_bytes_limit: self.log_messages_bytes_limit,
                    limit_to_load_programs: true,
                    recording_config: ExecutionRecordingConfig::new_single_setting(
                        transaction_status_sender_enabled
                    ),
                    drop_on_failure: flags.drop_on_failure,
                    all_or_nothing: flags.all_or_nothing,
                    strict_nonce_size_check: true,
                    drop_noop_transactions: true,
                }
            ));
        execute_and_commit_timings.load_execute_us = load_execute_us;
```

**File:** runtime/src/bank.rs (L3794-3806)
```rust
        if self.slot() > alt_invalidation_slot {
            // The address table lookup **may** have expired, but the
            // expiration is not guaranteed since there may have been
            // skipped slot.
            // If the addresses still resolve here, then the transaction is still
            // valid, and we can continue with processing.
            // If they do not, then the ATL has expired and the transaction
            // can be dropped.
            let (_addresses, _deactivation_slot) =
                self.load_addresses_from_ref(transaction.message_address_table_lookups())?;
        }

        Ok(())
```

**File:** runtime/src/bank/address_lookup_table.rs (L24-68)
```rust
impl AddressLoader for &Bank {
    fn load_addresses(
        self,
        address_table_lookups: &[MessageAddressTableLookup],
    ) -> Result<LoadedAddresses, AddressLoaderError> {
        self.load_addresses_from_ref(
            address_table_lookups
                .iter()
                .map(SVMMessageAddressTableLookup::from),
        )
        .map(|(loaded_addresses, _deactivation_slot)| loaded_addresses)
    }
}

impl Bank {
    /// Load addresses from an iterator of `SVMMessageAddressTableLookup`,
    /// additionally returning the minimum deactivation slot across all referenced ALTs
    pub fn load_addresses_from_ref<'a>(
        &self,
        address_table_lookups: impl Iterator<Item = SVMMessageAddressTableLookup<'a>>,
    ) -> Result<(LoadedAddresses, Slot), AddressLoaderError> {
        let slot_hashes = self
            .transaction_processor
            .sysvar_cache()
            .get_slot_hashes()
            .map_err(|_| AddressLoaderError::SlotHashesSysvarNotFound)?;

        let mut deactivation_slot = u64::MAX;
        let mut loaded_addresses = LoadedAddresses::default();
        for address_table_lookup in address_table_lookups {
            deactivation_slot = deactivation_slot.min(
                self.rc
                    .accounts
                    .load_lookup_table_addresses_into(
                        &self.ancestors,
                        address_table_lookup,
                        &slot_hashes,
                        &mut loaded_addresses,
                    )
                    .map_err(into_address_loader_error)?,
            );
        }

        Ok((loaded_addresses, deactivation_slot))
    }
```
