### Title
`getLargestAccounts` cache is keyed only by filter, not by commitment level, causing stale/wrong-fork data to be returned - (File: `rpc/src/rpc.rs`, `rpc/src/rpc_cache.rs`)

### Summary
`JsonRpcRequestProcessor::get_largest_accounts` caches its (expensive) scan result in `LargestAccountsCache`, but the cache key is only the `RpcLargestAccountsFilter` (Circulating/NonCirculating/None) and does not include the commitment level of the request that produced the cached result. As a result, a `getLargestAccounts` call made at one commitment level (e.g. `processed`) can populate a cache entry that is subsequently served, unmodified, to a later call requesting a different commitment level (e.g. `finalized`), for up to the cache TTL. This mirrors the External Report's root cause: a shared piece of state (`GlobalImpliedCollateralService`/here, the largest-accounts cache) is read by downstream logic before/without being validated against the specific execution context (bank/commitment) that the caller actually requested, resulting in stale data being used for a decision that should reflect current, context-specific state.

### Finding Description
`get_largest_accounts` looks up the bank for the requested `commitment` first, but immediately afterward it checks the cache keyed only by `filter`: [1](#0-0) 

The cache implementation itself stores and looks up entries purely by `Option<RpcLargestAccountsFilter>`, with no commitment/slot discriminator baked into the key — only a TTL (`duration`) governs freshness: [2](#0-1) 

So the sequence is:
1. Client A calls `getLargestAccounts` with `commitment: processed`. The processed bank's largest-account balances are computed and stored in the cache under key `filter`.
2. Before the cache entry expires (`LARGEST_ACCOUNTS_CACHE_DURATION`, configured in `rpc/src/rpc_service.rs`), Client B calls `getLargestAccounts` with `commitment: finalized` (or `confirmed`) and the same `filter`.
3. `get_cached_largest_accounts` returns the entry from step 1 — computed against the *processed* (possibly minority-fork, and possibly later-rolled-back) bank — but the response's `context.slot` and value are returned to a caller who explicitly asked for `finalized` data.

This is architecturally the same defect class as the Malt bug: a cross-cutting piece of shared state (`GlobalImpliedCollateralService`'s implied collateral ratio there; the largest-accounts cache here) is consulted without first verifying/refreshing it against the specific context of the current operation (the currently requested commitment/bank), so a caller can receive an answer computed under a different, and possibly since-invalidated, state snapshot.

### Impact Explanation
An unprivileged RPC client can receive `getLargestAccounts` results and an accompanying `context.slot` that do not correspond to the commitment level it explicitly requested. Concretely, a `finalized`-commitment request can return balances computed from a `processed` bank on a fork that was later abandoned/rolled back, i.e., wrong-fork/wrong-slot data returned for a query — which is one of the concrete "valid" outcomes called out in the validation rules (wrong-slot/fork data returned from a query). Downstream tooling/exchanges that rely on `finalized` largest-account data for auditing or reconciliation could be misled by balances that never became final.

### Likelihood Explanation
This is trivially reachable by any unauthenticated RPC caller: two ordinary `getLargestAccounts` calls with different `commitment` parameters (and the same optional `filter`) issued within the cache TTL window are sufficient — no special account state, timing race beyond normal RPC latency, or privileged role is required. The `filter`-only cache key is a straightforward, unconditional code-level defect, not a rare race window.

### Recommendation
Include the resolved bank's commitment level (or at minimum the resolved bank's slot/fork identity) as part of the cache key in `LargestAccountsCache`, e.g. key on `(commitment, filter)` instead of `filter` alone, so that a cached computation for one commitment level can never be served to a request for a different commitment level.

### Proof of Concept
1. Start a validator/test-validator with two forks such that a `processed`-commitment bank differs materially from the `finalized` bank (or simply race two calls before finalization confirms).
2. Call `getLargestAccounts` with `{"commitment":"processed"}` — this populates the cache for `filter = None`.
3. Immediately call `getLargestAccounts` with `{"commitment":"finalized"}` (same/default filter) within the TTL window — per `get_cached_largest_accounts` in `rpc/src/rpc.rs` and `LargestAccountsCache::get_largest_accounts` in `rpc/src/rpc_cache.rs`, the second call returns the first call's cached `(slot, accounts)` pair unmodified, even though it never computed anything against a finalized bank. [3](#0-2) [4](#0-3)

### Citations

**File:** rpc/src/rpc.rs (L1049-1055)
```rust
    fn get_cached_largest_accounts(
        &self,
        filter: &Option<RpcLargestAccountsFilter>,
    ) -> Option<(u64, Vec<RpcAccountBalance>)> {
        let largest_accounts_cache = self.largest_accounts_cache.read().unwrap();
        largest_accounts_cache.get_largest_accounts(filter)
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
