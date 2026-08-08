### Title
`JsonRpcRequestProcessor::bank` silently substitutes `root_bank()` for a purged/missing slot, returning data mislabeled with the wrong slot for `processed`/`finalized` commitment queries - ([File: rpc/src/rpc.rs])

### Summary
`JsonRpcRequestProcessor::bank` resolves a target bank by first computing a `slot` from `BlockCommitmentCache::slot_with_commitment` and then looking it up in `BankForks`. If that slot has already been purged from `BankForks` (a normal event during root advancement/fork pruning) before the cache is refreshed, the code silently falls back to `r_bank_forks.root_bank()` instead of returning an error, so the response is served from a different (older) slot than the one implied by the commitment level, while only logging a `warn!` server-side.

### Finding Description
For `CommitmentLevel::Processed` and `CommitmentLevel::Finalized`, `bank()` computes `slot` from `self.block_commitment_cache.read().unwrap().slot_with_commitment(commitment.commitment)`, then does: [1](#0-0) 

```rust
let r_bank_forks = self.bank_forks.read().unwrap();
r_bank_forks.get(slot).unwrap_or_else(|| {
    // ... known bug per solana-labs/solana#11078 ...
    warn!("Bank with {:?} not found at slot: {:?}", commitment.commitment, slot);
    r_bank_forks.root_bank()
})
```

The comment in the code itself acknowledges this is a known bug: "it may occur after an old bank has been purged from BankForks and a new BlockCommitmentCache has not yet arrived," referencing solana-labs/solana#11078. There is no propagation of an error to the RPC caller and no retry — the caller silently receives `root_bank()`, whose slot can differ arbitrarily from the slot implied by the commitment the client requested. This flows into every RPC method that calls `self.bank(commitment)` or `get_bank_with_config`, e.g. `get_account_info`/`get_balance` via `get_bank_with_config`, so an `RpcResponseContext.slot` and returned account data can be attributed to the wrong slot without the client having any indication that the fallback occurred (only a `warn!` in the validator's own log, invisible to the RPC client).

Note that for `CommitmentConfig::confirmed()` this specific fallback is not reachable — that branch returns `self.optimistically_confirmed_bank.read().unwrap().bank.clone()` directly, bypassing `BankForks::get`/root fallback entirely. The race is only reachable via `processed` or `finalized` commitment.

### Impact Explanation
This matches the "wrong-slot/fork/account data returned" impact category: a single unprivileged `getAccountInfo`/`getBalance` (or any RPC call funneled through `JsonRpcRequestProcessor::bank`/`get_bank_with_config`) using `processed` (the JSON-RPC default commitment) or `finalized` commitment can receive account/balance data attributed to `root_bank()`'s slot while the response's `context.slot` and semantics imply data from the (purged) slot the commitment cache pointed to. This is a data-correctness violation that misleads any client or downstream consumer trusting `RpcResponseContext.slot`.

### Likelihood Explanation
The trigger condition is a normal validator lifecycle event — root advancement/fork pruning removing an old bank from `BankForks` — occurring in the narrow window before `BlockCommitmentCache` is refreshed to match. This is not something the attacker can force deterministically with a single call, but it requires no elevated privilege, no additional clients, and no more than the allowed call rate: a single `processed`/`finalized`-commitment call issued at/near a root advance is sufficient to potentially observe it. The bug is also already flagged in the code comment (linking solana-labs/solana#11078) as a known-possible occurrence, indicating it is a real, reachable condition rather than only a theoretical race.

### Recommendation
Do not silently substitute `root_bank()` when the target slot is missing from `BankForks`. Instead, return an explicit RPC error (e.g. a variant analogous to `RpcCustomError::BlockCleanedUp` or a dedicated "commitment slot unavailable" error) so the client is not misled into treating stale/older-slot data as matching the requested commitment. Alternatively, as suggested in the existing code comment, have `BlockCommitmentCache` hold an `Arc<Bank>` (or otherwise keep the referenced bank alive) rather than a bare `Slot`, eliminating the possibility that the bank is purged from `BankForks` before the cache is updated.

### Proof of Concept
Integration test sketch in `rpc/src/rpc.rs` tests module:
1. Construct a `JsonRpcRequestProcessor` with a `BankForks` containing banks for slots `[root, root+1, ..., X]`.
2. Advance `BlockCommitmentCache` so `slot_with_commitment(Processed)` (or `Finalized`) returns slot `X`.
3. Remove slot `X` from `BankForks` (simulating normal pruning, e.g. via `BankForks::set_root`/`prune_non_rooted` removing a fork tip) without yet refreshing `BlockCommitmentCache`.
4. Call `processor.get_account_info(pubkey, Some(RpcContextConfig { commitment: Some(CommitmentConfig::processed()), ..Default::default() }))` (or the underlying `bank(Some(CommitmentConfig::processed()))`).
5. Assert failure of the invariant: `result.context.slot == X` is expected by the client's commitment request, but the implementation returns `bank_forks.root_bank().slot()` instead, demonstrating `context.slot != X` while no RPC error was surfaced — confirming silently wrong-slot data attribution.

### Citations

**File:** rpc/src/rpc.rs (L381-399)
```rust
        let r_bank_forks = self.bank_forks.read().unwrap();
        r_bank_forks.get(slot).unwrap_or_else(|| {
            // We log a warning instead of returning an error, because all known error cases
            // are due to known bugs that should be fixed instead.
            //
            // The slot may not be found as a result of a known bug in snapshot creation, where
            // the bank at the given slot was not included in the snapshot.
            // Also, it may occur after an old bank has been purged from BankForks and a new
            // BlockCommitmentCache has not yet arrived. To make this case impossible,
            // BlockCommitmentCache should hold an `Arc<Bank>` everywhere it currently holds
            // a slot.
            //
            // For more information, see https://github.com/solana-labs/solana/issues/11078
            warn!(
                "Bank with {:?} not found at slot: {:?}",
                commitment.commitment, slot
            );
            r_bank_forks.root_bank()
        })
```
