### Title
`getLargestAccounts` cache ignores commitment level, returning stale/wrong-commitment bank data - ([File: rpc/src/rpc.rs, rpc/src/rpc_cache.rs])

### Summary
`LargestAccountsCache` (`rpc/src/rpc_cache.rs`) is keyed solely by the `RpcLargestAccountsFilter` and invalidated only by wall-clock duration, never by bank slot or commitment level. Because `get_largest_accounts` in `rpc/src/rpc.rs` looks up the cache using only `config.filter` while separately resolving the bank via `config.commitment`, a client can receive a previously cached result computed for a different commitment level/bank snapshot than the one it just requested.

### Finding Description
In `rpc/src/rpc.rs`, `get_largest_accounts` resolves the target bank from the caller-supplied commitment first: [1](#0-0) 
then checks the cache using only the filter parameter, completely independent of which bank/commitment was just resolved: [2](#0-1) 

The cache itself, `LargestAccountsCache` in `rpc/src/rpc_cache.rs`, stores entries keyed by `Option<RpcLargestAccountsFilter>` only, and its hit/miss decision is purely time-based: [3](#0-2) 

There is no `commitment` field in `LargestAccountsCacheValue`, and no comparison against the freshly-resolved `bank.slot()` before returning a cache hit — only `cached_time.elapsed() < duration` is checked. On a cache hit, `get_largest_accounts` returns `RpcResponseContext::new(slot)` using the *cached* slot, bypassing the bank/commitment that was just resolved from the current request: [4](#0-3) 

Exploit flow (single unprivileged client, respecting rate limit):
1. Call `getLargestAccounts({filter: "circulating", commitment: "processed"})`. This populates the cache entry for key `Some(Circulating)` with the processed bank's slot/accounts via `set_cached_largest_accounts`: [5](#0-4) 
2. Root advances (or a fork reorg happens), i.e. the true rooted state changes.
3. Within the cache `duration` window, call `getLargestAccounts({filter: "circulating", commitment: "finalized"})` (or any other commitment). Because the cache lookup ignores commitment entirely, the second call returns the exact result computed for the *first* request's bank/commitment, not the newly requested finalized/rooted bank.

This violates the stated invariant "returned data belongs to the requested key, slot, fork, and commitment level," since the response silently reflects an older, possibly unrooted or forked-away bank snapshot instead of the bank matching the just-specified commitment.

### Impact Explanation
This is a commitment/fork-correctness violation: a client explicitly requesting `finalized` (or any commitment different from a prior call) can be served data computed against a different, potentially rolled-back bank state, with no indication that the served slot corresponds to a stale/mismatched commitment level. This falls under wrong-slot/fork/commitment data returned by a single low-rate RPC call — an accepted category per the audit's `Validate` criteria. The impact is scoped to `getLargestAccounts` responses (SOL balances/lamports of top accounts), not consensus-state corruption.

### Likelihood Explanation
Fully attacker-controlled and reproducible with a single client: only two `getLargestAccounts` calls with the same `filter` but different `commitment` values, spaced closer together than the cache's `duration` (which is on the order of tens of seconds, far larger than `CLUSTER_SLOT_TIME_TARGET / 2`), are needed. No special privileges, on-chain state manipulation, or multiple clients are required — this satisfies the rate-limit constraint trivially since the cache window is much longer than the minimum allowed call interval.

### Recommendation
Include the resolved commitment (or bank slot at fetch time) as part of the `LargestAccountsCache` key, or validate that the cache-hit `slot` is still consistent with the bank resolved for the current request's commitment before returning a cached entry. At minimum, cache lookups should be tied to `(filter, commitment)` rather than `filter` alone, and/or cache entries should be invalidated on root advance rather than purely by wall-clock duration.

### Proof of Concept
```rust
// rpc/src/rpc_cache.rs (extend existing tests)
#[test]
fn test_cache_ignores_commitment_mismatch() {
    let mut cache = LargestAccountsCache::new(30); // default-like duration

    let filter = Some(RpcLargestAccountsFilter::Circulating);
    let processed_slot = 100;
    let processed_accounts = vec![RpcAccountBalance {
        address: "Aaaa...".to_string(),
        lamports: 1_000,
    }];

    // First call: commitment = processed, bank at slot 100
    cache.set_largest_accounts(&filter, processed_slot, &processed_accounts);

    // Root advances; a subsequent call requests commitment = finalized,
    // which resolves to a different bank (slot 150) with different accounts.
    let finalized_slot = 150;
    let finalized_accounts = vec![RpcAccountBalance {
        address: "Bbbb...".to_string(),
        lamports: 2_000,
    }];

    // Cache lookup only depends on `filter`, not commitment/slot, so within
    // the duration window it still returns the stale processed-bank result:
    let cached = cache.get_largest_accounts(&filter);
    assert_eq!(cached, Some((processed_slot, processed_accounts)));

    // Assert this is WRONG: the client asked for finalized/slot 150 data,
    // but got back slot 100 data with no indication of commitment mismatch.
    assert_ne!(cached.unwrap().0, finalized_slot);
}
```
Integration-level PoC: call `getLargestAccounts` RPC twice via `RpcHandler` (as in `test_get_largest_accounts` in `rpc/src/rpc.rs`) with the same filter but `commitment: "processed"` then `commitment: "finalized"` after advancing the bank/root in between, and assert that the second response's `context.slot` and account balances differ from what a fresh (uncached) finalized-bank query would produce — demonstrating the stale cross-commitment leak.

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

**File:** rpc/src/rpc.rs (L1057-1065)
```rust
    fn set_cached_largest_accounts(
        &self,
        filter: &Option<RpcLargestAccountsFilter>,
        slot: u64,
        accounts: &[RpcAccountBalance],
    ) {
        let mut largest_accounts_cache = self.largest_accounts_cache.write().unwrap();
        largest_accounts_cache.set_largest_accounts(filter, slot, accounts)
    }
```

**File:** rpc/src/rpc.rs (L1067-1073)
```rust
    async fn get_largest_accounts(
        &self,
        config: Option<RpcLargestAccountsConfig>,
    ) -> RpcCustomResult<RpcResponse<Vec<RpcAccountBalance>>> {
        let config = config.unwrap_or_default();
        let bank = self.bank(config.commitment);

```

**File:** rpc/src/rpc.rs (L1074-1079)
```rust
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
