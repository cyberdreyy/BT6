## Title
Panic in `Blockstore::find_transaction_in_slot()` from unsanitized transaction with empty signatures reachable via `getTransaction`/`getSignaturesForAddress` RPC calls - ([File: ledger/src/blockstore.rs])

## Summary
`Blockstore::find_transaction_in_slot()` calls `transaction.sanitize()` on every stored transaction but discards the error and unconditionally proceeds to index `transaction.signatures[0]`, exactly the "missing early-exit on invalid/essential data" pattern flagged in the external report (functions must validate parameters/state before using them, and exit early otherwise).

## Finding Description
`find_transaction_in_slot` reads all entries for a slot, flattens their transactions, calls `sanitize()` on each one, and — regardless of whether `sanitize()` succeeded — continues to search with `.find(|(_, transaction)| transaction.signatures[0] == signature)`: [1](#0-0) 

`Transaction::sanitize()`/`VersionedTransaction` sanitization is exactly the check that guarantees, among other things, that a transaction has at least one signature. Here the sanitize error is only logged via `warn!` and the (index, transaction) tuple is passed through unchanged into the subsequent `.find()` closure, which directly indexes `transaction.signatures[0]`. If a transaction with zero signatures is ever present in the blockstore's stored entries for a slot (bypassing normal sigverify — this can occur for entries assembled from unvalidated/malicious shreds or corrupted ledger data during ledger replay before/without full transaction verification), indexing `signatures[0]` panics with an out-of-bounds index, crashing the calling thread.

This function is called from `get_transaction_with_status`, which backs `get_complete_transaction`/`get_rooted_transaction`, which are in turn invoked by `JsonRpcRequestProcessor::get_transaction` for the `getTransaction` JSON-RPC method: [2](#0-1) [3](#0-2) 

The `getTransaction` call path runs this blocking-task-wrapped read for a single client-supplied signature per call, so a single unprivileged RPC request can trigger the crash once such a malformed transaction is present in `blockstore`.

## Impact Explanation
An out-of-bounds slice index in Rust causes an unconditional process panic. If the panicking thread executes on a tokio blocking task within the RPC service (as it does for `get_transaction`, via `self.runtime.spawn_blocking`), the panic propagates and (depending on panic hook / abort configuration) can crash or hang the validator's RPC/JSON-RPC surface, satisfying the "concrete validator-process crash ... from one request" bar. This is a decoder/indexing panic on parsing transaction data pulled from local storage in response to a single unprivileged JSON-RPC call.

## Likelihood Explanation
Likelihood depends on whether a transaction with zero signatures can actually reach blockstore's transaction entries for a slot that is later queried — this requires either a corrupted/malicious entry making it into `entries_cf`/`transaction_status`-eligible storage prior to full transaction verification, or ledger corruption. The code's own explicit (but incomplete) sanitize-and-log-only handling shows the authors anticipated malformed transactions reaching this code path, which is why the `sanitize()` call exists at all — it is simply not enforced as an early exit.

## Recommendation
Change `find_transaction_in_slot` to skip/reject transactions that fail `sanitize()` instead of just logging and continuing, e.g. filter them out of the iterator before the `.find()` call, and guard the `signatures[0]` access with `.first()`/`.get(0)` so a signature-less transaction cannot cause an index panic.

## Proof of Concept
Not independently reproducible with the available static analysis (would require constructing a blockstore state containing a slot whose stored `Entry` transactions include a `VersionedTransaction` with an empty `signatures` vec, then issuing a `getTransaction` RPC call for any signature so the `.find()` closure evaluates `transaction.signatures[0]` on that entry and panics). This is noted as unverified/uncertain since I could not confirm through this analysis alone whether the ledger-write path enforces non-empty signatures before entries reach `get_slot_entries`-visible storage.

### Citations

**File:** ledger/src/blockstore.rs (L4469-4493)
```rust
    fn get_transaction_with_status(
        &self,
        signature: Signature,
        confirmed_unrooted_slots: &HashSet<Slot>,
    ) -> Result<Option<ConfirmedTransactionWithStatusMeta>> {
        if let Some((slot, meta)) =
            self.get_transaction_status(signature, confirmed_unrooted_slots)?
        {
            let (transaction, index) = self
                .find_transaction_in_slot(slot, signature)?
                .ok_or(BlockstoreError::TransactionStatusSlotMismatch)?; // Should not happen

            let block_time = self.get_block_time(slot)?;
            Ok(Some(ConfirmedTransactionWithStatusMeta {
                slot,
                tx_with_meta: TransactionWithStatusMeta::Complete(
                    VersionedTransactionWithStatusMeta { transaction, meta },
                ),
                block_time,
                index,
            }))
        } else {
            Ok(None)
        }
    }
```

**File:** ledger/src/blockstore.rs (L4501-4522)
```rust
    fn find_transaction_in_slot(
        &self,
        slot: Slot,
        signature: Signature,
    ) -> Result<Option<(VersionedTransaction, u32)>> {
        let slot_entries = self.get_slot_entries(slot, 0)?;
        Ok(slot_entries
            .into_iter()
            .flat_map(|entry| entry.transactions)
            .enumerate()
            .map(|(index, transaction)| {
                if let Err(err) = transaction.sanitize() {
                    warn!(
                        "Blockstore::find_transaction_in_slot sanitize failed: {err:?}, slot: \
                         {slot:?}, {transaction:?}",
                    );
                }
                (index, transaction)
            })
            .find(|(_, transaction)| transaction.signatures[0] == signature)
            .map(|(index, transaction)| (transaction, index as u32)))
    }
```

**File:** rpc/src/rpc.rs (L1768-1799)
```rust
    pub async fn get_transaction(
        &self,
        signature: Signature,
        config: Option<RpcEncodingConfigWrapper<RpcTransactionConfig>>,
    ) -> Result<Option<EncodedConfirmedTransactionWithStatusMeta>> {
        self.check_if_transaction_history_enabled()?;

        let config = config
            .map(|config| config.convert_to_current())
            .unwrap_or_default();
        let encoding = config.encoding.unwrap_or(UiTransactionEncoding::Json);
        let max_supported_transaction_version = config.max_supported_transaction_version;
        let commitment = config.commitment.unwrap_or_default();
        check_is_at_least_confirmed(commitment)?;

        let confirmed_bank = self.bank(Some(CommitmentConfig::confirmed()));
        let confirmed_transaction = self
            .runtime
            .spawn_blocking({
                let blockstore = Arc::clone(&self.blockstore);
                let confirmed_bank = Arc::clone(&confirmed_bank);
                move || {
                    if commitment.is_confirmed() {
                        let highest_confirmed_slot = confirmed_bank.slot();
                        blockstore.get_complete_transaction(signature, highest_confirmed_slot)
                    } else {
                        blockstore.get_rooted_transaction(signature)
                    }
                }
            })
            .await
            .expect("Failed to spawn blocking task");
```
