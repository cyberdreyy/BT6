### Title
Unregistered blockhash in `send_transaction_with_context` causes `.unwrap()` panic on `None` - (File: banks-server/src/banks_server.rs)

### Summary
`BanksServer::send_transaction_with_context` calls `get_blockhash_last_valid_block_height(blockhash)` on the root bank and immediately unwraps the `Option` result without validating that the blockhash is registered in the working/root bank's blockhash queue. An unprivileged client can submit a `VersionedTransaction` whose `recent_blockhash` field is any syntactically valid but never-issued `Hash` to trigger a panic in this async handler.

### Finding Description
The handler at [1](#0-0)  reads the client-supplied `recent_blockhash` directly from the transaction message and passes it unchecked to `bank_forks.read().unwrap().root_bank().get_blockhash_last_valid_block_height(blockhash).unwrap()`. There is no prior validation (e.g., `is_blockhash_valid`, sanitization, or signature verification) performed before this call — unlike `process_transaction_with_commitment_and_context`, which at least runs `SanitizedTransaction::try_create` and `.verify()` before touching the blockhash (though it has the identical unchecked `.unwrap()` pattern at [2](#0-1) ). `get_blockhash_last_valid_block_height` returns `Option<u64>`, returning `None` whenever the given hash is not present in the bank's blockhash queue (i.e., a hash that was never produced by the runtime as a real blockhash). Since the attacker fully controls the `VersionedTransaction` bytes sent over the wire, they can set `recent_blockhash` to any random 32-byte value. There is no signature verification prior to this line in `send_transaction_with_context`, so no valid keypair or signature is even required to reach the panicking code — only `transaction.signatures.first()` is read afterward (line 232), which occurs after the panic point.

### Impact Explanation
`send_transaction_with_context` is a `#[tarpc::server]` handler; a panic inside it propagates within the async task executing that RPC call. Depending on the tarpc/tokio panic-handling configuration, this can unwind and terminate the task that owns the BanksServer channel, or — since `run` (the transaction-processing thread) and the RPC dispatch task share process state — it can bring down the serving loop for that connection/process, denying subsequent balance, transaction-status, and account queries to all other unprivileged clients relying on the same Banks RPC surface (matches the stated scope: "process abort denies all subsequent balance/status/account queries").

### Likelihood Explanation
Trivial to trigger: an unprivileged client only needs to connect a `BanksClient`, construct a `VersionedTransaction` with an arbitrary (non-registered) `Hash` as `recent_blockhash`, and call `send_transaction_with_context` once. No stake, no valid signature, no special account state, and no more than one RPC call is required, fully within the attacker model described.

### Recommendation
Replace the `.unwrap()` on `get_blockhash_last_valid_block_height` in `send_transaction_with_context` (and the analogous call in `process_transaction_with_commitment_and_context`) with proper error handling — e.g., return a `TransactionResult::Err(TransactionError::BlockhashNotFound)` (or an equivalent `BanksTransactionResultWithMetadata`/error response) when the blockhash is not found, instead of panicking.

### Proof of Concept
```rust
// banks-server/src/banks_server.rs (test module) or an integration test in banks-server/tests/
#[tokio::test]
async fn test_send_transaction_with_unregistered_blockhash_does_not_panic() {
    use {
        solana_hash::Hash,
        solana_message::{Message, VersionedMessage},
        solana_signature::Signature,
        solana_transaction::versioned::VersionedTransaction,
        // ... plus test genesis/bank_forks/block_commitment_cache setup as in other banks-server tests
    };

    let (bank_forks, block_commitment_cache) = /* standard test setup, e.g. create_genesis_config-based BankForks */;
    let client_transport = start_local_server(
        bank_forks,
        block_commitment_cache,
        Duration::from_millis(1),
    ).await;
    let banks_client = BanksClient::new(Default::default(), client_transport).spawn();

    // Craft a transaction with a random, never-issued blockhash
    let bogus_blockhash = Hash::new_unique();
    let message = Message::new_with_blockhash(&[], None, &bogus_blockhash);
    let versioned_tx = VersionedTransaction {
        signatures: vec![Signature::default()],
        message: VersionedMessage::Legacy(message),
    };

    // Expect a graceful error, not a panic/task abort
    let result = banks_client
        .send_transaction_with_context(Context::current(), versioned_tx)
        .await;

    assert!(result.is_ok() || matches!(result, Err(BanksClientError::TransactionError(_))));
    // Confirm the server is still alive for subsequent calls
    let slot = banks_client.get_slot_with_context(Context::current(), CommitmentLevel::Processed).await;
    assert!(slot.is_ok());
}
```
Expected current behavior: the server task panics inside `.unwrap()` at `banks-server/src/banks_server.rs:230-231`, and the subsequent `get_slot_with_context` call fails because the serving task/connection is gone. After the fix, both calls should complete without panicking, with `send_transaction_with_context` surfacing a `BlockhashNotFound`-style error instead.

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

**File:** banks-server/src/banks_server.rs (L343-346)
```rust
        let last_valid_block_height = self
            .bank(commitment)
            .get_blockhash_last_valid_block_height(&blockhash)
            .unwrap();
```
