### Title
`getLargestAccounts` cache is keyed only by filter, ignoring commitment level, causing wrong-slot/stale data to be returned across commitment levels - (File: `rpc/src/rpc.rs`, `rpc/src/rpc_cache.rs`)

### Summary
`JsonRpcRequestProcessor::get_largest_accounts` caches results in `LargestAccountsCache`, which is keyed solely by the optional `RpcLargestAccountsFilter` (`Circulating`/`NonCirculating`/`None`) and carries a TTL (`duration`), but it never incorporates the requested `CommitmentConfig`/slot into the cache key. This mirrors the reported bug class: a cached aggregate value (`s.totalLiquidity`) is reused without accounting for state that can independently change (`hedgeSize`/hedge liquidity) between reads, so callers observe stale or logically-inconsistent state. Here the "stale, unaccounted-for state change" is the commitment/slot dimension of the request.

### Finding Description
`get_largest_accounts` first tries the cache without any regard to the caller's requested commitment: [1](#0-0) 

The underlying cache lookup/store logic only keys on `filter`: [2](#0-1) [3](#0-2) 

The bank used to actually compute results is chosen per the caller-supplied commitment via `self.bank(config.commitment)`, and different commitments (`processed`, `confirmed`, `finalized`) can correspond to different banks/slots with materially different balances/order: [4](#0-3) 

Because the cache entry stores only `(slot, accounts)` per `filter` and is looked up before considering commitment, a request made with one commitment level populates the cache, and a *different, unrelated* request using a different commitment level (but same `filter`) within the TTL window will be served the first request's `(slot, accounts)` pair unconditionally: [5](#0-4) 

This means an RPC client explicitly asking for `finalized` largest-accounts data can receive results computed from a `processed` (potentially unconfirmed/rolled-back) bank, or vice versa — the returned `context.slot` and `value` do not correspond to the bank/commitment actually requested.

### Impact Explanation
This is a wrong-slot/wrong-commitment data disclosure: an unprivileged RPC caller requesting `getLargestAccounts` with a strict commitment (e.g., `finalized`) can be served account balances and the associated `slot` from a different commitment level's bank (e.g., `processed`), which may reflect an unconfirmed or since-forked state. This can mislead any downstream consumer (auditors, exchanges, explorers) relying on `getLargestAccounts` under a specific commitment guarantee into believing finalized/confirmed data when it is not. This falls under "wrong-slot/fork/account data returned" from a query, achievable from a single unprivileged JSON-RPC caller.

### Likelihood Explanation
Medium: it requires only two calls to `getLargestAccounts` with the same `filter` but different `commitment` values within the cache TTL window (`duration` seconds, configured by the operator) — no elevated privileges, no crafted state, and no multiple clients needed. Any RPC consumer alternating commitment levels (a very common pattern for polling clients) will trip this.

### Recommendation
Extend the `LargestAccountsCache` key (and/or invalidate/skip the cache) to include the resolved bank's commitment/slot, e.g., key by `(filter, commitment)` or validate that the cached `slot` is still consistent with the currently requested commitment's bank before returning cached data, similar to how the report recommends recomputing `s.totalLiquidity` from the ground truth (`_getTotalLiquidity`) rather than trusting a value that can go stale due to an unaccounted state dimension.

### Proof of Concept
1. Configure an RPC node such that `processed` and `finalized` banks diverge in largest-account balances (e.g., an in-flight large transfer landed in the processed bank but not yet finalized).
2. Call `getLargestAccounts` with `commitment: "finalized"` and no filter — this populates `largest_accounts_cache` keyed only by `filter = None`, storing the finalized bank's slot/accounts.
3. Immediately call `getLargestAccounts` with `commitment: "processed"` and no filter, within the cache TTL (`duration` seconds).
4. The second call hits `get_cached_largest_accounts(&None)` in `rpc/src/rpc.rs` and returns the finalized-bank's cached `(slot, accounts)` instead of computing against the processed bank — the response's `context.slot` and account balances do not reflect the requested `processed` commitment (and the same happens in reverse order).

### Citations

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
