### Title
Silently discarded protobuf-to-status conversion error causes transaction status/metadata misreporting - (File: ledger/src/blockstore.rs)

### Summary
`Blockstore::read_transaction_status` fetches a stored protobuf `TransactionStatusMeta` and converts it via `TryInto`, but discards the conversion error with `.ok()`, collapsing any decode failure into `None` (i.e., "no status found") instead of surfacing the error.

### Finding Description
`read_transaction_status` reads the raw protobuf record and converts it to the in-memory `TransactionStatusMeta` type, but ignores whether that conversion succeeded: [1](#0-0) 

The conversion is `TryFrom<generated::TransactionStatusMeta> for TransactionStatusMeta`, which can fail (`DecodeError`) in multiple places: wincode deserialization of the stored `TransactionError` bytes, and `Pubkey::try_from` on loaded writable/readonly addresses when the byte slice isn't exactly 32 bytes: [2](#0-1) 

Because `read_transaction_status` swallows this `Result` with `.ok()`, any of these failures (e.g., a corrupted/edge-case stored record, or a mismatch introduced by a future on-disk format change) is indistinguishable from "no status was ever recorded" for that signature/slot. This is the same defect class as the referenced report: a fallible operation's outcome is discarded, and the caller silently proceeds as if it succeeded/returned an innocuous default, producing incorrect downstream behavior instead of surfacing the error.

This value flows into `map_transactions_to_statuses`, which is used to build confirmed blocks: [3](#0-2) 
There, a `None` from `read_transaction_status` is turned into `BlockstoreError::MissingTransactionMetadata`, which will fail the entire `getBlock` request even though the raw record technically exists on disk — an unprivileged JSON-RPC caller's `getBlock`/`getTransaction`-style query returns an error (or, for other call sites reading a single signature, could report the transaction as absent) instead of the actual data or a decode-specific diagnostic.

### Impact Explanation
This causes wrong data to be returned for an unprivileged JSON-RPC read query (transaction/block status lookups): a stored, real transaction record can be reported as missing/absent due to a swallowed conversion error rather than actually being missing. This matches the "wrong-slot/fork/account data returned" / "decoder panic and misreporting" acceptance class from the validation rules, since it silently misrepresents on-disk state to a read-only caller.

### Likelihood Explanation
I could not find within the indexed code a concrete, currently-reachable input that forces the `TryFrom` conversion to fail on a record that was written by this same validator version (the `From<TransactionStatusMeta> for generated::TransactionStatusMeta` writer path always produces well-formed 32-byte pubkeys and valid wincode-serialized errors, so in the common case round-tripping succeeds). The realistic trigger is data written by a different/older schema version, on-disk corruption, or a future format change reusing the same column family — none of which I can fully confirm is reachable purely from an unprivileged RPC call in the current codebase. I am not fully certain this is exploitable today without additional conditions (e.g., cross-version ledger data or bit-level corruption), so likelihood should be treated as low/uncertain rather than confirmed high.

### Recommendation
Do not discard the `Result` from `meta.try_into()` in `read_transaction_status`. Propagate the `DecodeError` (e.g., map it into a `BlockstoreError` variant) instead of collapsing it to `None`, so callers can distinguish "no status recorded" from "status recorded but failed to decode," and so RPC handlers can return an explicit error rather than silently reporting a transaction as absent.

### Proof of Concept
Not independently verified with a runnable reproduction; based on static code analysis only:
1. A `TransactionStatusMeta` protobuf record exists in the `transaction_status_cf` column family for `(signature, slot)` whose `loaded_writable_addresses`/`loaded_readonly_addresses` bytes are not exactly 32 bytes long (e.g., due to a schema mismatch or corrupted write), or whose embedded `err` bytes fail wincode deserialization.
2. Call `Blockstore::read_transaction_status((signature, slot))`.
3. `get_protobuf` succeeds and returns `Some(meta)`, but `meta.try_into()` returns `Err(DecodeError)`.
4. `.ok()` discards this error, and the function returns `Ok(None)`.
5. Any caller (including `map_transactions_to_statuses`, feeding `getBlock`/similar RPC methods) treats this as "no transaction status metadata found," misreporting the true on-disk state rather than surfacing a decode error. [1](#0-0) [4](#0-3)

### Citations

**File:** ledger/src/blockstore.rs (L4227-4243)
```rust
    pub fn map_transactions_to_statuses(
        &self,
        slot: Slot,
        iterator: impl Iterator<Item = VersionedTransaction>,
    ) -> Result<Vec<VersionedTransactionWithStatusMeta>> {
        iterator
            .map(|transaction| {
                let signature = transaction.signatures[0];
                Ok(VersionedTransactionWithStatusMeta {
                    transaction,
                    meta: self
                        .read_transaction_status((signature, slot))?
                        .ok_or(BlockstoreError::MissingTransactionMetadata)?,
                })
            })
            .collect()
    }
```

**File:** ledger/src/blockstore.rs (L4245-4253)
```rust
    pub fn read_transaction_status(
        &self,
        index: (Signature, Slot),
    ) -> Result<Option<TransactionStatusMeta>> {
        Ok(self
            .transaction_status_cf
            .get_protobuf(index)?
            .and_then(|meta| meta.try_into().ok()))
    }
```

**File:** storage-proto/src/convert.rs (L573-629)
```rust
        let status = match err {
            None => Ok(()),
            Some(tx_error) => {
                let tx_error =
                    wincode::deserialize(&tx_error.err).map_err(|source| DecodeError {
                        bytes: tx_error.err,
                        source,
                    })?;
                Err(tx_error)
            }
        };
        let inner_instructions = if inner_instructions_none {
            None
        } else {
            Some(
                inner_instructions
                    .into_iter()
                    .map(|inner| inner.into())
                    .collect(),
            )
        };
        let log_messages = if log_messages_none {
            None
        } else {
            Some(log_messages)
        };
        let pre_token_balances = Some(
            pre_token_balances
                .into_iter()
                .map(|balance| balance.into())
                .collect(),
        );
        let post_token_balances = Some(
            post_token_balances
                .into_iter()
                .map(|balance| balance.into())
                .collect(),
        );
        let rewards = Some(rewards.into_iter().map(|reward| reward.into()).collect());
        let loaded_addresses = LoadedAddresses {
            writable: loaded_writable_addresses
                .into_iter()
                .map(Pubkey::try_from)
                .collect::<Result<_, _>>()
                .map_err(|bytes| DecodeError {
                    bytes,
                    source: ReadError::Custom("Invalid writable address"),
                })?,
            readonly: loaded_readonly_addresses
                .into_iter()
                .map(Pubkey::try_from)
                .collect::<Result<_, _>>()
                .map_err(|bytes| DecodeError {
                    bytes,
                    source: ReadError::Custom("Invalid readonly address"),
                })?,
        };
```
