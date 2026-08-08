### Title
Silent swallowing of Bigtable errors in `getSignatureStatuses` causes wrong (empty) transaction status to be returned - (File: rpc/src/rpc.rs)

### Summary
`JsonRpcRequestProcessor::get_signature_statuses` queries long-term (Bigtable) storage as a fallback when a transaction is not found in the bank or in the local blockstore. The result of this fallback query is collapsed with `.map(Some).unwrap_or(None)`, which discards any error returned by Bigtable — not just a "signature not found" error — and reports `None` (i.e., "unknown/not found") to the RPC caller. This mirrors the reported ERC20 pattern of not checking whether an external call actually succeeded before treating it as a no-op/success, letting failures pass through silently and causing the caller to receive incorrect information.

### Finding Description
In `get_signature_statuses`, when `searchTransactionHistory` is requested and the signature is absent from the bank and blockstore, the code falls back to Bigtable: [1](#0-0) 

```rust
} else if let Some(bigtable_ledger_storage) = &self.bigtable_ledger_storage {
    bigtable_ledger_storage
        .get_signature_status(&signature)
        .await
        .map(Some)
        .unwrap_or(None)
} else {
    None
}
```

`LedgerStorage::get_signature_status` returns `Result<TransactionStatus>`, whose `Err` variant can be either `Error::SignatureNotFound` (a legitimate "not found" outcome) or any other underlying storage/transport error, e.g. `bigtable::Error` wrapping RPC/network failures: [2](#0-1) 

Unlike this call site, other call sites in the same file correctly distinguish `SignatureNotFound` from other errors and surface a proper internal error to the client, e.g. `get_signatures_for_address` explicitly matches `Err(StorageError::SignatureNotFound(_))` vs. `Err(err) => { warn!(...); return Err(RpcCustomError::LongTermStorageUnreachable.into()) }`: [3](#0-2) 

By contrast, `get_signature_statuses` treats *every* Bigtable error identically to "not found," discarding the distinction entirely via `unwrap_or(None)`. A transient Bigtable outage, quota/timeout error, or any decode error therefore causes the RPC to report that a transaction's status is unknown, even though the transaction may in fact be confirmed/finalized in long-term storage.

### Impact Explanation
This causes `getSignatureStatuses` (an unprivileged, commonly-used JSON-RPC method invoked by wallets, exchanges, and block explorers to confirm finalized transactions) to return incorrect/misleading data for a single low-rate call: a `null` status for a transaction that actually exists, purely because of a transient storage error rather than because the transaction is genuinely absent. Downstream consumers (e.g., exchanges polling for finality before crediting funds) may misinterpret this as "transaction dropped/never landed," leading to wrong operational decisions based on wrong-data returned by the validator's RPC. This satisfies the "wrong data returned" acceptance bar for a query-only, single-request analog.

### Likelihood Explanation
Any node operator running with `--enable-rpc-bigtable-ledger-storage` (or similar) that experiences any transient connectivity/error condition to Bigtable will trigger this on a normal client-issued `getSignatureStatuses` call with `searchTransactionHistory: true` for a signature not present in the local blockstore — a single unprivileged RPC call is sufficient to observe the incorrect result each time such an error occurs.

### Recommendation
Match on the `Result` from `bigtable_ledger_storage.get_signature_status(&signature).await` the same way `get_signatures_for_address` does: treat `Err(StorageError::SignatureNotFound(_))` as `None`, but propagate other errors as an RPC error (e.g., `RpcCustomError::LongTermStorageUnreachable`) instead of silently mapping them to `None` via `unwrap_or`.

### Proof of Concept
1. Run a validator RPC node with Bigtable ledger storage enabled and `enable_rpc_transaction_history` on.
2. Simulate/force a transient Bigtable error for a specific signature (e.g., network blip, throttling) that is not present in the bank or in local blockstore history.
3. Call `getSignatureStatuses` with `{"searchTransactionHistory": true}` for that signature.
4. Observe the code path at [4](#0-3)  converts the Bigtable error into `None`, so the RPC response reports the transaction status as unknown/absent rather than surfacing an error, even though the transaction may actually be finalized and simply temporarily unreachable.

### Citations

**File:** rpc/src/rpc.rs (L1714-1722)
```rust
                } else if let Some(bigtable_ledger_storage) = &self.bigtable_ledger_storage {
                    bigtable_ledger_storage
                        .get_signature_status(&signature)
                        .await
                        .map(Some)
                        .unwrap_or(None)
                } else {
                    None
                }
```

**File:** rpc/src/rpc.rs (L1921-1933)
```rust
                    match bigtable_ledger_storage
                        .get_signature_status(&bigtable_before.unwrap())
                        .await
                    {
                        Err(StorageError::SignatureNotFound(_)) => {
                            bigtable_before = None;
                        }
                        Err(err) => {
                            warn!("Failed to query Bigtable: {err:?}");
                            return Err(RpcCustomError::LongTermStorageUnreachable.into());
                        }
                        Ok(_) => {}
                    }
```

**File:** storage-bigtable/src/lib.rs (L791-804)
```rust
    pub async fn get_signature_status(&self, signature: &Signature) -> Result<TransactionStatus> {
        trace!("LedgerStorage::get_signature_status request received: {signature:?}");
        self.stats.increment_num_tx_table_reads();
        let mut bigtable = self.connection.client();

        let transaction_info = bigtable
            .get_bincode_cell::<TransactionInfo>(TX_TABLE_NAME, signature.to_string())
            .await
            .map_err(|err| match err {
                bigtable::Error::RowNotFound => Error::SignatureNotFound(*signature),
                _ => err.into(),
            })?;
        Ok(transaction_info.into())
    }
```
