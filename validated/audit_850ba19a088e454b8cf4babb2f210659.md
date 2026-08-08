### Title
`getLargestAccounts` returns stale cached results without validating the requested commitment/slot - ([File: rpc/src/rpc.rs])

### Summary
The external report's bug class is "the pool computes a per-user output from a state snapshot that is not refreshed before use, so a caller can receive results for a state that has since changed." The reachable analog in agave is `JsonRpcRequestProcessor::get_largest_accounts`, which serves a cached response instead of recomputing it against the state implied by the caller's requested commitment/slot.

### Finding Description
`get_largest_accounts` first resolves `bank = self.bank(config.commitment)` for the requested commitment, but then checks a cache keyed **only** by `config.filter` (`Option<RpcLargestAccountsFilter>`) via `get_cached_largest_accounts`: [1](#0-0) 

If a cache hit occurs, the handler returns the cached `slot`/`accounts` directly, completely bypassing the bank/commitment that was just resolved: [2](#0-1) 

The cache itself is keyed by `filter` only, with no commitment or slot dimension: [3](#0-2) 

Only on a cache miss is the value freshly computed from the resolved `bank` and then stored, again keyed only by filter, along with whatever `bank.slot()` happened to produce that value: [4](#0-3) 

This mirrors the `Crate.sol` bug: a value (`sharePrice`/withdrawable amount there, largest-accounts list here) is served from a snapshot taken for one context and reused for a different request context without recomputation, because the code path that should "rebalance" (recompute against the newly resolved bank) is skipped whenever the cache is populated.

### Impact Explanation
A client requesting `getLargestAccounts` with `commitment: "finalized"` can receive a response whose `context.slot` and account list were computed for an earlier `commitment: "processed"` (or a different, older) request that happens to share the same filter. Because the cache key ignores commitment/min_context_slot entirely, one caller's low-commitment request populates a cache entry (default TTL is used for all callers), and other callers — including callers explicitly requiring finalized/rooted data — get the stale data returned as if it corresponded to their own request. This is a "wrong-slot/fork/account data returned" outcome for an unprivileged JSON-RPC query, since the `RpcResponseContext::new(slot)` reported to the client does not correspond to the bank resolved from their actual requested commitment.

### Likelihood Explanation
The condition is trivially reachable: any two `getLargestAccounts` calls with the same `filter` value but different `commitment`/`minContextSlot` parameters within the cache TTL window will hit this path. No special permissions, timing races beyond normal request cadence, or multiple clients are required beyond issuing two ordinary RPC calls.

### Recommendation
Include the resolved commitment (or the bank's effective slot/context requirements, e.g., `min_context_slot`) in the `LargestAccountsCache` key, or validate that the cached `slot` still satisfies the currently requested `CommitmentConfig`/`min_context_slot` before returning the cached value; otherwise recompute from the freshly resolved `bank`.

### Proof of Concept
1. Call `getLargestAccounts` with `{"filter": "circulating", "commitment": "processed"}`. This populates the cache for `filter = circulating` with the processed-bank's slot/accounts [5](#0-4) .
2. Immediately call `getLargestAccounts` with `{"filter": "circulating", "commitment": "finalized"}` before the cache TTL (`largest_accounts_cache` duration) expires.
3. Because `get_cached_largest_accounts` only checks `filter`, the second call returns the same cached `slot`/`accounts` from step 1 without consulting the finalized bank at all, even though the caller explicitly asked for finalized-commitment data [2](#0-1) .

### Citations

**File:** rpc/src/rpc.rs (L1067-1078)
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

**File:** rpc/src/rpc_cache.rs (L9-42)
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

impl LargestAccountsCache {
    pub(crate) fn new(duration: u64) -> Self {
        Self {
            duration,
            cache: HashMap::new(),
        }
    }

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
```
