### Title
`get_largest_accounts` cache is keyed only by filter, not by commitment level, causing commitment-level misrepresentation - ([File: rpc/src/rpc.rs])

### Summary
`JsonRpcRequestProcessor::get_largest_accounts` caches results in `LargestAccountsCache` keyed solely by `RpcLargestAccountsFilter`, with no commitment-level component in the cache key. A `processed`-commitment call populates the cache with data/slot from a non-rooted bank, and a subsequent `finalized`-commitment call with the same filter returns that same processed-bank data/slot via a cache hit, bypassing the freshly-fetched finalized bank entirely.

### Finding Description
In `rpc/src/rpc.rs`, `get_largest_accounts` first resolves a bank for the requested commitment (`let bank = self.bank(config.commitment);`), then immediately checks the cache with `self.get_cached_largest_accounts(&config.filter)` [1](#0-0) . The cache lookup key is only `Option<RpcLargestAccountsFilter>` — commitment is never part of the key, as shown by `LargestAccountsCache::get_largest_accounts`/`set_largest_accounts` in `rpc/src/rpc_cache.rs` [2](#0-1) .

Exploit flow with a single client, sequential calls:
1. Client calls `getLargestAccounts` with `commitment=processed`, `filter=None` (or any filter). `bank(processed)` resolves to a recent, non-rooted bank at slot X. Cache miss occurs, the scan runs, and `set_cached_largest_accounts(filter, bank.slot()=X, accounts)` stores the processed-bank result under key `filter` [3](#0-2) .
2. Client calls `getLargestAccounts` again with `commitment=finalized`, same `filter`. `bank(finalized)` is resolved (a genuinely rooted bank, likely at a different, later slot Y), but before that bank is ever used, `get_cached_largest_accounts(&config.filter)` returns a hit using only the `filter` key — matching the entry from step 1 — and the function returns `RpcResponseContext::new(slot=X)` with the processed-era `accounts`, completely discarding the finalized bank it just fetched [4](#0-3) .

The `RpcResponseContext` embeds only the slot number, giving no indication that this slot/data came from a `processed` commitment request rather than the `finalized` bank that was actually queried. Downstream tooling relying on "finalized" RPC responses to be rooted/consensus-safe would be misled: the returned slot X may not even be a root in `BankForks`, and the account balances reflect a bank state that could later be forked out.

### Impact Explanation
This matches the "wrong-slot/fork/account data returned" category: `getLargestAccounts` at `commitment=finalized` can return data and a slot number that were never actually derived from a finalized/rooted bank, violating the RPC's fundamental commitment-level guarantee. This is scoped as consensus-adjacent because financial/monitoring tooling that treats `finalized` responses as authoritative can be given data belonging to a possibly-orphaned processed slot, with no signal in the response distinguishing this from a legitimate finalized read.

### Likelihood Explanation
Fully reachable by a single unprivileged client issuing two ordinary sequential `getLargestAccounts` JSON-RPC calls (first at `processed`, then at `finalized`) with the same `filter` value, within the cache TTL window (`duration` passed to `LargestAccountsCache::new`). No special config, keys, or multiple clients are required, and the behavior is deterministic/repeatable every time the TTL hasn't expired.

### Recommendation
Include the effective commitment level (or the bank's `slot`/`bank_id` alongside a freshness check against `self.bank(commitment).slot()`) as part of the `LargestAccountsCache` key, e.g. key by `(Option<RpcLargestAccountsFilter>, CommitmentLevel)`, so that entries populated at one commitment level cannot satisfy lookups from a different commitment level.

### Proof of Concept
Integration test in `rpc/src/rpc.rs` test module:
```rust
#[test]
fn test_get_largest_accounts_commitment_cache_bypass() {
    let rpc = RpcHandler::start();
    // 1. Call at commitment=processed, populate cache with processed bank's slot
    let request = create_test_request(
        "getLargestAccounts",
        Some(json!([{"commitment": "processed"}])),
    );
    let processed_result: RpcResponse<Vec<RpcAccountBalance>> =
        parse_success_result(rpc.handle_request_sync(request));
    let processed_slot = processed_result.context.slot;

    // Advance/root a later bank to represent real finalized state at a higher slot
    // (e.g. via rpc.advance_bank_to_confirmed()/root_bank helpers in RpcHandler)

    // 2. Call at commitment=finalized with same filter (None)
    let request = create_test_request(
        "getLargestAccounts",
        Some(json!([{"commitment": "finalized"}])),
    );
    let finalized_result: RpcResponse<Vec<RpcAccountBalance>> =
        parse_success_result(rpc.handle_request_sync(request));

    // Assertion that should hold but fails due to the bug:
    // finalized_result.context.slot should equal the actual rooted/finalized slot,
    // not the processed_slot from step 1.
    assert_ne!(finalized_result.context.slot, processed_slot,
        "finalized response returned data/slot cached from a processed-commitment call");
}
```
Expected (current, buggy) behavior: `finalized_result.context.slot == processed_slot` and `finalized_result.value == processed_result.value`, demonstrating the cache-key bypass across commitment levels.

### Citations

**File:** rpc/src/rpc.rs (L1070-1078)
```rust
    ) -> RpcCustomResult<RpcResponse<Vec<RpcAccountBalance>>> {
        let config = config.unwrap_or_default();
        let bank = self.bank(config.commitment);

        if let Some((slot, accounts)) = self.get_cached_largest_accounts(&config.filter) {
            Ok(RpcResponse {
                context: RpcResponseContext::new(slot),
                value: accounts,
            })
```

**File:** rpc/src/rpc.rs (L1096-1117)
```rust
            let accounts = self
                .runtime
                .spawn_blocking({
                    let bank = Arc::clone(&bank);
                    move || {
                        bank.get_largest_accounts(NUM_LARGEST_ACCOUNTS, &addresses, address_filter)
                    }
                })
                .await
                .expect("Failed to spawn blocking task")
                .map_err(|e| RpcCustomError::ScanError {
                    message: e.to_string(),
                })?
                .into_iter()
                .map(|(address, lamports)| RpcAccountBalance {
                    address: address.to_string(),
                    lamports,
                })
                .collect::<Vec<RpcAccountBalance>>();

            self.set_cached_largest_accounts(&config.filter, bank.slot(), &accounts);
            Ok(new_response(&bank, accounts))
```

**File:** rpc/src/rpc_cache.rs (L30-58)
```rust
    pub(crate) fn get_largest_accounts(
        &self,
        filter: &Option<RpcLargestAccountsFilter>,
    ) -> Option<(u64, Vec<RpcAccountBalance>)> {
        self.cache.get(filter).and_then(|value| {
            if let Ok(elapsed) = value.cached_time.elapsed()
                && elapsed < Duration::from_secs(self.duration)
            {
                return Some((value.slot, value.accounts.clone()));
            }
            None
        })
    }

    pub(crate) fn set_largest_accounts(
        &mut self,
        filter: &Option<RpcLargestAccountsFilter>,
        slot: u64,
        accounts: &[RpcAccountBalance],
    ) {
        self.cache.insert(
            filter.clone(),
            LargestAccountsCacheValue {
                accounts: accounts.to_owned(),
                slot,
                cached_time: SystemTime::now(),
            },
        );
    }
```
