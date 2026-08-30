[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** runtime/runtime/src/pipelining.rs (L58-62)
```rust
    block_accounts: BTreeSet<AccountId>,

    /// List of global contract identifiers that must not be prepared in this chunk.
    /// This solves the same issue as `block_accounts` but for global contract deployments.
    block_global_contracts: HashSet<GlobalContractIdentifier>,
```

**File:** runtime/runtime/src/pipelining.rs (L91-100)
```rust
struct PrepareTask {
    status: Mutex<PrepareTaskStatus>,
    condvar: Condvar,
    created: Instant,
    /// Hash of the contract identifier captured at submit time.
    ///
    /// Defense in depth: if the receiver's code hash unexpectedly changes between
    /// preparation and execution, the stale prepared artifact is discarded.
    expected_hash: CryptoHash,
}
```

**File:** runtime/runtime/src/pipelining.rs (L165-168)
```rust
            ReceiptEnum::GlobalContractDistribution(global_contract_data) => {
                self.block_global_contracts.insert(global_contract_data.id().clone());
                return false;
            }
```

**File:** runtime/runtime/src/pipelining.rs (L348-352)
```rust
        let key = PrepareTaskKey { receipt_id: receipt.get_hash(), action_index };
        // Double-check contract hash matches as defense-in-depth.
        let Some(task) = self.map.get(&key).filter(|t| t.expected_hash == identifier.hash()) else {
            let start = Instant::now();
            let gas_counter = self.gas_counter(view_config.as_ref(), function_call.gas);
```
