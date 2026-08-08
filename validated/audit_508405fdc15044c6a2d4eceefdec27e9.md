### Title
Unhandled `.unwrap()` on `get_blockhash_last_valid_block_height` in `BanksServer::send_transaction_with_context` panics on unrecognized recent_blockhash - ([File: banks-server/src/banks_server.rs])

### Summary
`BanksServer::send_transaction_with_context` calls `get_blockhash_last_valid_block_height(blockhash).unwrap()` on the attacker-supplied `Message.recent_blockhash` without checking whether the hash exists in the bank's blockhash queue. A single `VersionedTransaction` with any blockhash not present in `bank_forks.root_bank().blockhash_queue` causes this `Option::None` to be unwrapped, panicking the async task handling the request.

### Finding Description
The trait method is implemented as: [1](#0-0) 

Unlike `process_transaction_with_commitment_and_context` (which also calls `get_blockhash_last_valid_block_height(&blockhash).unwrap()` at line 345, sharing the same defect) `send_transaction_with_context` performs *no* sanitization/verification of the transaction (no `SanitizedTransaction::try_create`, no `verify()`) before looking up the blockhash. It only computes `message_hash` and reads `transaction.message.recent_blockhash()` directly from client-controlled input, then immediately does:
```rust
let last_valid_block_height = self
    .bank_forks
    .read()
    .unwrap()
    .root_bank()
    .get_blockhash_last_valid_block_height(blockhash)
    .unwrap();
```
`get_blockhash_last_valid_block_height` returns `Option<u64>`, yielding `None` whenever `blockhash` is not registered in the root bank's blockhash queue (e.g., an expired or randomly-generated 32-byte hash). This `.unwrap()` panics unconditionally on that input. Because `send_transaction_with_context` is invoked directly from the tarpc-generated server dispatch for every inbound request (via `banks_server.serve()` in `start_local_server`/`start_tcp_server`), a single malformed transaction reaches the unwrap with no prior guard, unlike other RPC surfaces (e.g., `rpc/src/rpc.rs`) that check blockhash validity before consuming it.

### Impact Explanation
This is a crash of the async task servicing the client connection (tarpc `BaseChannel::execute`); depending on the panic-handling configuration of the process (default `panic=unwind` vs `panic=abort`), this either terminates just the connection's task or aborts the whole process. This matches the "no single request can panic" invariant violated by a single unprivileged client, corresponding to a DoS category in Agave's bounty program.

### Likelihood Explanation
Trivial and fully reproducible: any client that can reach `BanksServer` (via `start_tcp_server`'s TCP listener or the loopback channel from `start_local_server`) can send exactly one `VersionedTransaction` whose `Message.recent_blockhash` is a random hash not present in the root bank's queue. No authentication, staking, or special timing is required — one call suffices deterministically.

Caveat: `BanksServer` is the backend for `solana-banks-client`/`solana-program-test`, used primarily by test tooling rather than the mainline validator's JSON-RPC/PubSub stack (`rpc/src/rpc.rs`, which has equivalent lookups but behind additional guards). If `start_tcp_server` is exposed as a live network service (as its implementation supports), this is directly reachable by an unprivileged remote client with a single request.

### Recommendation
Replace the `.unwrap()` calls at `banks-server/src/banks_server.rs:230` (and the analogous one at line 345 in `process_transaction_with_commitment_and_context`) with proper error handling — return a `TransactionError`/`Result::Err` (e.g., `TransactionError::BlockhashNotFound`) when `get_blockhash_last_valid_block_height` returns `None`, instead of panicking.

### Proof of Concept
```rust
// banks-server/src/banks_server.rs (test module)
#[tokio::test]
async fn test_send_transaction_with_unknown_blockhash_does_not_panic() {
    let genesis = create_genesis_config(10);
    let bank = Bank::new_for_tests(&genesis.genesis_config);
    let bank_forks = BankForks::new_rw_arc(bank);
    let block_commitment_cache = Arc::new(RwLock::new(BlockCommitmentCache::default()));

    let server = BanksServer::new_loopback(
        bank_forks,
        block_commitment_cache,
        Duration::from_millis(1),
    );

    let bogus_blockhash = Hash::new_unique(); // not in blockhash_queue
    let message = Message::new_with_blockhash(&[], None, &bogus_blockhash);
    let tx = VersionedTransaction::from(Transaction::new_unsigned(message));

    // Expect: returns without panicking (ideally an Err), not an unwrap-panic.
    let result = std::panic::AssertUnwindSafe(
        server.send_transaction_with_context(tarpc::context::current(), tx)
    )
    .catch_unwind()
    .await;

    assert!(result.is_ok(), "send_transaction_with_context panicked on unknown blockhash");
}
```
Expected current behavior: the task panics inside `.unwrap()` on `get_blockhash_last_valid_block_height`, demonstrated by `result.is_err()` (panic caught) prior to the fix; after applying the recommended fix, the call should return an `Err(TransactionError::BlockhashNotFound)`-style result instead.

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
