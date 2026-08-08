### Title
Unvalidated `recent_blockhash` causes `.unwrap()` panic in `BanksServer::send_transaction_with_context` - ([File: banks-server/src/banks_server.rs])

### Summary
`BanksServer::send_transaction_with_context` looks up `last_valid_block_height` for the client-supplied `recent_blockhash` without first sanitizing/verifying the transaction, and directly `.unwrap()`s the `Option` returned by `get_blockhash_last_valid_block_height`. A `VersionedTransaction` whose `recent_blockhash` is not present in the root bank's blockhash queue makes this call return `None`, and the `.unwrap()` panics the async task handling the RPC.

### Finding Description
In `banks-server/src/banks_server.rs`, the `send_transaction_with_context` handler is: [1](#0-0) 

Unlike `process_transaction_with_commitment_and_context`, which first calls `SanitizedTransaction::try_create` and `.verify()` before ever touching the blockhash queue lookup (and even there the same `.unwrap()` pattern exists at lines 343-346), `send_transaction_with_context` performs **no validation at all** on the incoming `VersionedTransaction` before calling:
```rust
self.bank_forks.read().unwrap().root_bank()
    .get_blockhash_last_valid_block_height(blockhash)
    .unwrap()
```
`get_blockhash_last_valid_block_height` returns `Option<u64>`, yielding `None` whenever the supplied hash is not currently tracked in the bank's blockhash queue (e.g., an unrelated random `Hash`, an already-expired blockhash, or simply `Hash::default()`). Because the attacker fully controls the `VersionedTransaction` sent over the tarpc channel (loopback or TCP, per `start_local_server` / `start_tcp_server`), they can trivially set `message.recent_blockhash` to any value never issued by the bank. This directly triggers the panic in the async handler `send_transaction_with_context`, invoked from `Banks::send_transaction_with_context` via a single tarpc call — no prior authentication, staking, or special access is required beyond having a connection to the banks-server (loopback channel used by BanksClient/program-test, or the TCP listener from `start_tcp_server`).

The panic occurs on the tokio task executing the `tarpc` channel (`chan.execute(server.serve())` in `start_tcp_server`, or the spawned server task in `start_local_server`). This is a single-call, unauthenticated crash of the request-handling task, matching the described invariant violation ("no single RPC request can panic the process/task").

### Impact Explanation
A single crafted `VersionedTransaction` with a bogus/non-existent `recent_blockhash` panics the task servicing that banks-server connection. Depending on the build's panic strategy and whether the panic is caught by tokio's task boundary, this can abort the connection-handling task or, in a `panic = "abort"` build, terminate the whole process. This is a scoped single-call Denial-of-Service against the banks-server component, matching the "process/task panic from one crafted RPC call" bounty category.

### Likelihood Explanation
Fully deterministic and trivially reproducible: any client with a connection to the banks-server (used by `program-test`/`solana-test-validator`'s Banks RPC surface, and reachable over TCP via `start_tcp_server`) can send exactly one `send_transaction_with_context` call with an arbitrary/random `Hash` as `recent_blockhash`. No signatures, staking, or special privileges are needed since no verification precedes the blockhash lookup.

### Recommendation
In `send_transaction_with_context`, replace the `.unwrap()` with a graceful error path: return early (e.g., an error/`None` response, or drop with a logged warning) when `get_blockhash_last_valid_block_height` returns `None`, mirroring how `process_transaction_with_commitment_and_context` should also be hardened at its equivalent `.unwrap()` (lines 343-346). Ideally, sanitize/validate the transaction (as done in `process_transaction_with_commitment_and_context`) before performing the blockhash-queue lookup, and change the RPC method's return type to propagate a "blockhash not found" error to the client instead of relying on infallible unwraps.

### Proof of Concept
```rust
// banks-server/tests/panic_bogus_blockhash.rs
use {
    solana_banks_interface::Banks,
    solana_hash::Hash,
    solana_message::{Message, VersionedMessage},
    solana_transaction::versioned::VersionedTransaction,
    // ... set up BanksServer via start_local_server as in existing banks-server tests
};

#[tokio::test]
async fn bogus_blockhash_does_not_panic_server() {
    // Build BankForks/BlockCommitmentCache and spin up start_local_server(...) as in
    // existing banks-server integration tests to obtain a Banks client.

    let bogus_blockhash = Hash::new_unique(); // never issued by the bank
    let mut message = Message::default();
    message.recent_blockhash = bogus_blockhash;
    let tx = VersionedTransaction {
        signatures: vec![Default::default()],
        message: VersionedMessage::Legacy(message),
    };

    // Expectation: call should return an error/None, not panic the server task.
    let result = client.send_transaction_with_context(tarpc::context::current(), tx).await;
    assert!(result.is_ok(), "server task panicked instead of returning gracefully");
}
```
Expected current behavior: the server-side task panics inside `.get_blockhash_last_valid_block_height(blockhash).unwrap()`, either aborting the connection task or the process (depending on panic strategy), instead of returning a controlled error to the client.

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
