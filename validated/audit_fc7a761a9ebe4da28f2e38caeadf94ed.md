### Title
`LargestAccountsCache` keyed only by `filter` allows a `processed`-commitment result to be served as a `finalized` response - ([File: rpc/src/rpc_cache.rs])

### Summary
`LargestAccountsCache` stores cached `getLargestAccounts` results keyed solely on `Option<RpcLargestAccountsFilter>`, with no `CommitmentConfig` component in the key. `JsonRpcRequestProcessor::get_largest_accounts` fetches a commitment-specific bank but then serves stale cached data (and a stale `RpcResponseContext` slot) that may have been populated by a request made at a *different, weaker* commitment level, so a caller requesting `finalized` can receive balances/slot data that were only ever computed against a `processed` (potentially unrooted) bank.

### Finding Description
The cache struct is: [1](#0-0) 

Both accessors take only `filter` as the lookup/insert key, with no commitment parameter anywhere in the type or method signatures: [2](#0-1) 

In `rpc.rs`, `get_largest_accounts` first resolves a commitment-specific bank, but then checks the cache **before** using that bank to decide whether to answer, and if there's a hit it returns the cached slot/accounts directly, bypassing everything derived from the just-fetched bank: [3](#0-2) 

If there is a cache miss, the accounts are computed from whatever bank was resolved for the *current* request's commitment, and the cache is populated keyed only by `filter`, with the slot coming from `bank.slot()` of that (possibly `processed`/unrooted) bank: [4](#0-3) 

Exploit flow:
1. Client A issues `getLargestAccounts` with `commitment=processed`, `filter=Circulating`. `self.bank(commitment)` returns the processed (potentially unrooted) bank at slot `X`. The result is computed and cached via `set_cached_largest_accounts(&Some(Circulating), X, accounts)` — this call site has no commitment information.
2. Within `duration` seconds (the cache TTL), the same or a different client issues `getLargestAccounts` with `commitment=finalized`, `filter=Circulating`. `get_cached_largest_accounts(&Some(Circulating))` matches the same key and returns `(X, accounts)` from the `processed` bank — even though a `finalized` bank at an older, truly rooted slot exists and was fetched by `self.bank(config.commitment)` right before the cache check (and then discarded).
3. The RPC response is built as `RpcResponse { context: RpcResponseContext::new(slot) /* = X */, value: accounts }`, labeled implicitly as answering the `finalized`-commitment request, when the underlying data was never derived from a rooted/finalized bank.

The existing commitment-resolution logic (`self.bank(config.commitment)`) is the guard that is supposed to enforce commitment semantics, but it is rendered ineffective for cache hits because the cache key omits commitment entirely.

### Impact Explanation
This falls under the "wrong-slot/fork data returned to client" category from the scope description: a `finalized`-labeled `getLargestAccounts` response can be populated from account balances computed against an unrooted/`processed` bank state. A wallet, exchange, or any RPC consumer relying on `finalized` commitment for `getLargestAccounts` to guarantee irreversible state can be misled about circulating/non-circulating largest account balances that were never confirmed as finalized, potentially informing decisions (e.g., displaying inflated/incorrect top-balance data) on data that could later be rolled back if the source fork was abandoned.

### Likelihood Explanation
This is trivially reproducible by a single unprivileged client: it requires only two sequential `getLargestAccounts` calls with the same `filter` but different `commitment` values, spaced less than `duration` seconds apart (the cache TTL configured for `LargestAccountsCache::new`). No special privileges, keys, or multiple accounts are needed — just normal JSON-RPC access, well within the one-call-per-`CLUSTER_SLOT_TIME_TARGET/2` constraint.

### Recommendation
Include the resolved `CommitmentConfig` (or equivalently, the resolved bank's slot/commitment level) as part of the cache key in `LargestAccountsCache`, e.g. change the key type to `(Option<RpcLargestAccountsFilter>, CommitmentConfig)`, and update `get_cached_largest_accounts`/`set_cached_largest_accounts` call sites in `rpc.rs` to pass the commitment used to resolve `bank`. Alternatively, only cache/reuse entries whose cached slot is `<= ` the currently resolved bank's slot for the requested commitment, and always reconstruct `RpcResponseContext` from the actually-resolved bank rather than only from the cached slot.

### Proof of Concept
```rust
// rpc/src/rpc_cache.rs (extended test)
use solana_commitment_config::CommitmentConfig; // conceptual: key needs commitment support once fixed

#[test]
fn test_cache_key_ignores_commitment_allows_cross_commitment_reuse() {
    let mut cache = LargestAccountsCache::new(30); // 30s TTL, default-like
    let filter = Some(RpcLargestAccountsFilter::Circulating);

    // Simulate response computed from a `processed` (unrooted) bank at slot 1000
    let processed_accounts = vec![RpcAccountBalance {
        address: "Fake111111111111111111111111111111111111".to_string(),
        lamports: 999_999,
    }];
    cache.set_largest_accounts(&filter, 1000, &processed_accounts);

    // A subsequent lookup, intended to serve a `finalized` request with a
    // resolved finalized bank at an older, truly rooted slot (e.g. 900),
    // still hits the same cache entry because the key has no commitment
    // component.
    let (returned_slot, returned_accounts) = cache.get_largest_accounts(&filter).unwrap();

    // BUG: the finalized-labeled request receives slot 1000 / processed_accounts,
    // which were never derived from a rooted bank.
    assert_eq!(returned_slot, 1000);
    assert_eq!(returned_accounts, processed_accounts);
    // Expected (post-fix) behavior: this lookup should miss because it was
    // populated under a different commitment level, forcing recomputation
    // against the actual finalized bank.
}
```

Integration-level PoC (conceptual, to run against `JsonRpcRequestProcessor`): call `get_largest_accounts(Some(RpcLargestAccountsConfig{commitment: Some(CommitmentConfig::processed()), filter: Some(Circulating), ..}))`, then immediately call `get_largest_accounts` again with `commitment: Some(CommitmentConfig::finalized())` and the same filter; assert that the second response's `context.slot` equals a bank slot that is actually rooted (`bank_forks.root()`) rather than the slot returned by the first (processed) call, which the current implementation fails.

### Citations

**File:** rpc/src/rpc_cache.rs (L9-20)
```rust
#[derive(Debug, Clone)]
pub struct LargestAccountsCache {
    duration: u64,
    cache: HashMap<Option<RpcLargestAccountsFilter>, LargestAccountsCacheValue>,
}

#[derive(Debug, Clone)]
struct LargestAccountsCacheValue {
    accounts: Vec<RpcAccountBalance>,
    slot: u64,
    cached_time: SystemTime,
}
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

**File:** rpc/src/rpc.rs (L1067-1079)
```rust
    async fn get_largest_accounts(
        &self,
        config: Option<RpcLargestAccountsConfig>,
    ) -> RpcCustomResult<RpcResponse<Vec<RpcAccountBalance>>> {
        let config = config.unwrap_or_default();
        let bank = self.bank(config.commitment);

        if let Some((slot, accounts)) = self.get_cached_largest_accounts(&config.filter) {
            Ok(RpcResponse {
                context: RpcResponseContext::new(slot),
                value: accounts,
            })
        } else {
```

**File:** rpc/src/rpc.rs (L1096-1118)
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
        }
```
