### Title
Unvalidated recent_blockhash allows unwrap panic in BanksServer::send_transaction_with_context - (File: banks-server/src/banks_server.rs)

### Summary
`BanksServer::send_transaction_with_context` derives `last_valid_block_height` from `get_blockhash_last_valid_block_height(blockhash)` and immediately calls `.unwrap()` on the result without checking whether the value is actually valid/present, mirroring the report's bug class of trusting a returned value without confirming its "status" is genuinely valid before using it in downstream logic.

### Finding Description
`send_transaction_with_context` takes an attacker/client-controlled `VersionedTransaction`, extracts `blockhash = transaction.message.recent_blockhash()`, and passes it straight into `Bank::get_blockhash_last_valid_block_height`, then unwraps the result: [1](#0-0) 

`get_blockhash_last_valid_block_height` looks up the blockhash in the bank's `BlockhashQueue` and returns `None` when the supplied hash is not a recognized/recent blockhash (e.g., an arbitrary, forged, or expired hash a client can freely place in `recent_blockhash`). Just like the `ChainlinkUtil::getPrice` bug — where a legitimately possible "invalid" return value (`startedAt == 0`) was not checked before being used in a security-relevant computation — this code fails to check for the legitimately possible `None` case of `get_blockhash_last_valid_block_height` before unwrapping it. Since `recent_blockhash` is fully attacker-controlled input on an unprivileged RPC-like path (the tarpc `Banks` service `send_transaction_with_context` handler), any caller can trigger the `None` branch by submitting a transaction with a bogus or already-expired blockhash.

### Impact Explanation
Triggering the `unwrap()` on `None` causes an immediate panic in the thread handling the request. Because `BanksServer` is a shared server process backing the `Banks` RPC interface, an unhandled panic on a request-serving path causes a concrete process crash/DoS reachable from a single malformed transaction, without requiring any special role, similar in class to a validator-process crash from one request.

### Likelihood Explanation
The only requirement is submitting a `VersionedTransaction` whose `recent_blockhash` field is not present in the target bank's blockhash queue (trivial to construct — e.g., `Hash::default()` or any random hash, or a hash that has aged out of the queue). No signature verification or privileged access is needed before this code path is reached, since this call happens before any transaction processing/validation of the blockhash's freshness.

### Recommendation
Replace the `.unwrap()` with proper `Option` handling: if `get_blockhash_last_valid_block_height` returns `None`, return an appropriate error result to the client (e.g., a `BanksTransactionResultWithMetadata`/error response) instead of unwrapping, matching how `Bank`'s regular transaction-processing path already treats an unrecognized blockhash as `TransactionError::BlockhashNotFound` rather than panicking.

### Proof of Concept
1. Start a `BanksServer` instance (as used by `solana-banks-server`/`solana-program-test`).
2. Construct any `VersionedTransaction` with `message.recent_blockhash` set to a hash not present in the bank's `BlockhashQueue` (e.g. `Hash::default()` or a random hash).
3. Call the `send_transaction_with_context` RPC method with this transaction.
4. Observe the server thread panics at `get_blockhash_last_valid_block_height(blockhash).unwrap()` in [2](#0-1) , crashing that request-handling path.

Note: I was unable to fully trace whether the panic is caught/isolated per-connection by the surrounding `tarpc` server harness (which could reduce this to a per-connection failure rather than a full-process crash); this would need to be verified in a live/test environment, since the index does not show the exact `tarpc` panic-isolation behavior for this async handler.

### Citations

**File:** banks-server/src/banks_server.rs (L222-244)
```rust
    async fn send_transaction_with_context(self, _: Context, transaction: VersionedTransaction) {
        let message_hash = transaction.message.hash();
        let blockhash = transaction.message.recent_blockhash();
        let last_valid_block_height = self
            .bank_forks
            .read()
            .unwrap()
            .root_bank()
            .get_blockhash_last_valid_block_height(blockhash)
            .unwrap();
        let signature = transaction.signatures.first().cloned().unwrap_or_default();
        let info = TransactionInfo::new(
            message_hash,
            signature,
            *blockhash,
            serialize(&transaction).unwrap(),
            last_valid_block_height,
            None,
            None,
            None,
        );
        self.transaction_sender.send(info).unwrap();
    }
```
