### Title
Unprivileged client can panic the validator process via unchecked `.unwrap()` on unknown blockhash in `send_transaction_with_context` - (File: banks-server/src/banks_server.rs)

### Summary
`BanksServer::send_transaction_with_context` accepts an attacker-supplied `VersionedTransaction` and calls `get_blockhash_last_valid_block_height(blockhash).unwrap()` on the value returned from `root_bank()`, with no validation that the blockhash exists in the bank's blockhash queue. Because this happens while a `bank_forks.read()` guard is alive on the stack, the panic poisons the shared `bank_forks` `RwLock`, causing all subsequent `.read()/.write().unwrap()` calls elsewhere in the process (including the `BanksServer::run` transaction-processing thread and every other RPC handler in this impl) to panic as well, resulting in a validator/test-validator-wide crash from a single call.

### Finding Description
In `send_transaction_with_context`:
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
``` [1](#0-0) 

`get_blockhash_last_valid_block_height` returns `Option<u64>` and returns `None` whenever the supplied blockhash is not present in the bank's recent blockhash queue (e.g., a random/garbage 32-byte value, or a legitimately expired blockhash that has already been purged). Because `transaction` is fully attacker-controlled and deserialized directly from the wire with no upstream sanitization/validation of `recent_blockhash` before this call, a single call with a garbage blockhash makes `.unwrap()` panic on `None`.

Critically, the temporary `RwLockReadGuard` produced by `self.bank_forks.read().unwrap()` is part of the same chained expression and remains alive on the stack for the duration of the statement, including at the moment the trailing `.unwrap()` panics. `std::sync::RwLock` poisons on panic while a guard is held, so this panic poisons the `bank_forks` lock. Every other place in the process that does `self.bank_forks.read().unwrap()` or `.write().unwrap()` — including `BanksServer::bank`, `BanksServer::slot`, and the dedicated `run` thread that processes queued transactions — will now also panic on their next access, cascading the single-request panic into a broader crash of the server/process rather than only failing the one async task.

Existing guards do not stop this: there is no length/format/commitment validation of `recent_blockhash` and no `Result`-based error path back to the client for this specific check (unlike `process_transaction_with_commitment_and_context`, which has the same unchecked `.unwrap()` at a similar spot, but at least does prior `SanitizedTransaction::try_create`/`verify()` before reaching it — none of which validate blockhash presence either).

### Impact Explanation
A single unprivileged client, with one `send_transaction_with_context` call carrying a transaction with an arbitrary/garbage `recent_blockhash`, triggers a `.unwrap()` panic on `None`. Because the panic occurs while the `bank_forks` `RwLock` read guard is alive, the lock becomes poisoned, and all other threads/tasks in the server that rely on `bank_forks.read()/.write().unwrap()` (the transaction-processing thread `run`, and every other RPC method in the `Banks` impl) will subsequently panic too. This is a genuine denial-of-service of the banks-server process from a single unprivileged request, matching the "no-panic invariant" violation and DoS category called out in the audit scope.

### Likelihood Explanation
Trivial and fully reachable with a single call: the attacker needs no special privileges beyond being able to connect and issue one `send_transaction_with_context` RPC with a crafted `VersionedTransaction` whose `recent_blockhash` is not currently tracked by the bank (e.g., all-zero hash, random 32 bytes, or an old already-evicted blockhash). No fork/leader/gossip control, no leaked keys, and no more than one call is required.

### Recommendation
Replace the `.unwrap()` on `get_blockhash_last_valid_block_height` with proper error handling: return an error/`None` result to the client (or a rejected `TransactionResult`) instead of panicking when the blockhash is absent from the queue. Additionally, avoid holding the `bank_forks` read guard across the point where the panic could occur (or wrap the fallible computation so the guard is dropped before any `.unwrap()` that can fail), and apply the same fix to the identical pattern in `process_transaction_with_commitment_and_context`.

### Proof of Concept
```rust
// banks-server/tests/panic_poc.rs
use {
    solana_banks_interface::Banks,
    solana_hash::Hash,
    solana_message::{Message, VersionedMessage},
    solana_transaction::versioned::VersionedTransaction,
    std::panic,
};

#[tokio::test]
async fn send_transaction_with_garbage_blockhash_panics() {
    // Build a BanksServer wired to a fresh BankForks (see existing banks-server test setup helpers).
    let server = /* construct via BanksServer::new_loopback(...) as in existing tests */;

    // Craft a transaction whose recent_blockhash is not in the bank's blockhash queue.
    let mut message = Message::default();
    message.recent_blockhash = Hash::new_unique(); // garbage/unknown blockhash
    let tx = VersionedTransaction {
        signatures: vec![Default::default()],
        message: VersionedMessage::Legacy(message),
    };

    let result = panic::catch_unwind(panic::AssertUnwindSafe(|| {
        futures::executor::block_on(server.clone().send_transaction_with_context(tarpc::context::current(), tx))
    }));

    assert!(result.is_err(), "expected panic on unknown recent_blockhash, none occurred");

    // Follow-up assertion: bank_forks lock is now poisoned for all other operations.
    let poisoned = server.bank_forks.read();
    assert!(poisoned.is_err(), "bank_forks RwLock should be poisoned after the panic");
}
```
Expected result: the call panics inside `send_transaction_with_context` at the `.unwrap()` on `get_blockhash_last_valid_block_height`, and a subsequent `bank_forks.read()` returns `Err` (poisoned), demonstrating the single-request DoS cascading beyond the initiating call.

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
