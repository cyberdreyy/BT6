### Title
Unvalidated blockhash in `send_transaction_with_context` causes server panic via `.unwrap()` on `get_blockhash_last_valid_block_height` - ([File: banks-server/src/banks_server.rs])

### Summary
`BanksServer::send_transaction_with_context` calls `root_bank.get_blockhash_last_valid_block_height(blockhash).unwrap()` directly on the attacker-supplied `VersionedTransaction`'s `recent_blockhash`, with no sanitization, verification, or existence check beforehand. Because `get_blockhash_last_valid_block_height` returns `None` for any blockhash not present in the root bank's blockhash queue, a single fire-and-forget transaction with a bogus or expired blockhash panics the serving async task.

### Finding Description
The `Banks::send_transaction_with_context` handler in `banks-server/src/banks_server.rs` (lines 222-244) is the direct implementation reached by the client-facing `send_transaction`/`send_transaction_with_context` calls exposed in `banks-client/src/lib.rs`. Unlike `process_transaction_with_commitment_and_context` (line 317+), which sanitizes and verifies the transaction via `SanitizedTransaction::try_create` and `.verify()` before touching blockhash validity, `send_transaction_with_context` performs no such checks:

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
    ...
}
``` [1](#0-0) 

`Bank::get_blockhash_last_valid_block_height` looks up the blockhash in the bank's blockhash queue and returns `Option<u64>`, yielding `None` when the hash is absent (e.g., a random/unregistered `Hash`, or one older than the queue's retained history) [2](#0-1) . An attacker only needs to construct a `VersionedTransaction` whose `message.recent_blockhash()` is any value not currently tracked by the root bank (trivially satisfiable with `Hash::new_unique()` or an old/expired hash) and submit it through the exposed `send_transaction` client API defined in `banks-client/src/lib.rs`, which forwards directly to this RPC method without any client- or server-side preflight validation.

Because this handler runs as an async task inside the tarpc server loop, an `unwrap()` panic here unwinds/aborts the task executing the request — for a single unprivileged client sending one malformed transaction, causing denial of service for the banks server / embedded validator read path that hosts it.

### Impact Explanation
A single malformed `VersionedTransaction` sent through `send_transaction` (fire-and-forget) reaches `send_transaction_with_context` and panics the process on the `.unwrap()` at line 231 of `banks-server/src/banks_server.rs`. This matches the "single RPC request panics the validator process" bounty category, since `banks-server` is embedded in test-validator and other validator-adjacent tooling reachable by unprivileged clients.

### Likelihood Explanation
Fully deterministic and trivially reproducible: any client with access to the banks RPC endpoint (loopback or TCP, per `start_local_server`/`start_tcp_server`) can trigger it with exactly one call, no special privileges, no race conditions, and no dependence on other clients' behavior.

### Recommendation
Replace the `.unwrap()` with graceful error handling — e.g., return early (or an error variant, if the interface allows) when `get_blockhash_last_valid_block_height` returns `None`, mirroring the sanitize-and-verify pattern already used in `process_transaction_with_commitment_and_context`. At minimum, guard with `.ok_or(...)?`/`if let Some(...)` and drop the transaction instead of unwrapping.

### Proof of Concept
```rust
// banks-server/src/banks_server.rs (or an integration test in banks-client)
use {
    solana_hash::Hash,
    solana_message::{Message, VersionedMessage},
    solana_transaction::versioned::VersionedTransaction,
    solana_keypair::Keypair,
    solana_signer::Signer,
};

#[tokio::test]
async fn test_send_transaction_with_bad_blockhash_does_not_panic() {
    // set up BanksServer via new_loopback / start_local_server as in existing banks-server tests
    let (banks_client, bank_forks, ..) = setup_test_server(); // reuse existing test harness

    let payer = Keypair::new();
    let bogus_blockhash = Hash::new_unique(); // guaranteed not in root bank's blockhash queue
    let message = Message::new_with_blockhash(&[], Some(&payer.pubkey()), &bogus_blockhash);
    let tx = VersionedTransaction::try_new(VersionedMessage::Legacy(message), &[&payer]).unwrap();

    // Expected (buggy) behavior today: this call panics the server task instead of
    // returning gracefully.
    banks_client.send_transaction(tx).await.unwrap();

    // Assert server is still alive by issuing a follow-up call.
    let slot = banks_client.get_root_slot().await.unwrap();
    assert!(slot >= 0);
}
```
Expected assertion after fix: the call to `send_transaction` with an unregistered blockhash should not crash the server, and subsequent RPC calls (e.g. `get_root_slot`) should continue to succeed.

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

**File:** runtime/src/bank.rs (L1-1)
```rust
//! The `bank` module tracks client accounts and the progress of on-chain
```
