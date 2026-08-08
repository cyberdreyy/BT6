## Title
Non-atomic check-then-set race in `getLargestAccounts` cache lets a slower/stale scan overwrite a fresher result, causing wrong-slot data to be served - (File: rpc/src/rpc.rs, rpc/src/rpc_cache.rs)

## Summary
`get_largest_accounts` implements a classic TOCTOU pattern that is structurally the same defect as the reported `change_gauge_weight` bug: a "set"-style write (`set_cached_largest_accounts`) unconditionally overwrites shared state without checking whether a concurrent request has already installed a fresher value, so the final state depends on request completion order rather than logical/slot order.

## Finding Description
`get_largest_accounts` first checks the cache via `get_cached_largest_accounts`, and on a miss, performs an expensive scan (`bank.get_largest_accounts`) before unconditionally writing the result back with `set_cached_largest_accounts`: [1](#0-0) 

The cache read/compute/write sequence is not atomic as a whole — only the individual read and write steps are separately locked: [2](#0-1) 

`LargestAccountsCache::set_largest_accounts` simply inserts into the `HashMap`, replacing whatever entry currently exists for that filter, with no comparison against the `slot` (or `cached_time`) that is already stored: [3](#0-2) 

If two unprivileged RPC callers issue `getLargestAccounts` concurrently while the cache entry for a given filter is expired or empty, both will observe a cache miss, both will perform the (expensive) scan against their respective current bank, and both will call `set_cached_largest_accounts`. Whichever call finishes last wins, regardless of which one observed the more recent bank/slot. If the request that read an older bank happens to finish after the request that read a newer bank (e.g., due to scheduling delays, `spawn_blocking` queue depth, or GC pauses in `calculate_non_circulating_supply`/`bank.get_largest_accounts`), the cache is left holding stale data tagged with an older `slot`, even though a fresher result had already been computed and cached moments before. This exactly mirrors the reported bug class: a privileged/attempted authoritative "set" of shared state is clobbered by an interleaved actor's action because the write path never validates that its input is still the most current before committing it.

## Impact Explanation
Subsequent callers of `getLargestAccounts` (any unprivileged client) receive `RpcResponse` data tagged with an older `slot` and (potentially) stale account balances, i.e., wrong-slot/account data returned from a read-only JSON-RPC query — the exact impact category permitted by the validation rules for this scan ("wrong-slot/fork/account data returned"). This is a straightforward data-correctness defect in a widely used, unprivileged JSON-RPC method, not a validator-crashing bug, but it can mislead any downstream consumer (explorers, wallets, analytics) relying on `getLargestAccounts` for a consistent, monotonically-fresh snapshot.

## Likelihood Explanation
`getLargestAccounts` is a public, unprivileged JSON-RPC method reachable by any client with no special role. The race window is the (potentially large) duration of `bank.get_largest_accounts`/`calculate_non_circulating_supply`, executed via `spawn_blocking`, during which the cache entry is expired/absent. Any two concurrent calls to this method for the same filter during that window can trigger the stale-overwrite; this requires no validator/peer privileges and no malicious code — just ordinary concurrent client traffic, making it readily reachable in practice.

## Recommendation
Make the cache write conditional on freshness instead of unconditional overwrite: compare the `slot`/`cached_time` of the incoming write against what is currently stored and only replace the entry if the new value is newer (e.g., `if slot > existing.slot` or use a monotonic version/timestamp check), or hold a single lock across the whole read-compute-write sequence (or a per-filter mutex) so only one concurrent scan proceeds per filter and the rest wait on it and directly reuse its result, similar to a de-duplicated single-flight cache pattern.

## Proof of Concept
1. Ensure the `LargestAccountsCache` entry for a filter (e.g., `None`) is empty or expired.
2. Client A calls `getLargestAccounts` while the bank is at slot `S1`; the request enters the miss branch and starts `bank.get_largest_accounts` for slot `S1`, but is artificially delayed (e.g., scheduler contention).
3. Before A finishes, the bank advances to `S2`, and Client B calls `getLargestAccounts`; it also misses the cache, computes results for slot `S2`, and calls `set_cached_largest_accounts` with `slot = S2`, populating the cache with the fresh result.
4. Client A's delayed call finally completes and calls `set_cached_largest_accounts` with `slot = S1`, unconditionally overwriting B's fresher `S2` entry in the `HashMap` (see `rpc/src/rpc_cache.rs:44-58`).
5. Client C then calls `getLargestAccounts` and receives a cache hit reporting `slot = S1` with the older account balances, even though the bank has already progressed to `S2` and a fresher scan result had briefly existed in the cache. [1](#0-0) [3](#0-2)

### Citations

**File:** rpc/src/rpc.rs (L1049-1065)
```rust
    fn get_cached_largest_accounts(
        &self,
        filter: &Option<RpcLargestAccountsFilter>,
    ) -> Option<(u64, Vec<RpcAccountBalance>)> {
        let largest_accounts_cache = self.largest_accounts_cache.read().unwrap();
        largest_accounts_cache.get_largest_accounts(filter)
    }

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

**File:** rpc/src/rpc.rs (L1067-1118)
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
            let (addresses, address_filter) = if let Some(filter) = config.clone().filter {
                let non_circulating_supply = self
                    .calculate_non_circulating_supply(&bank)
                    .await
                    .map_err(|e| RpcCustomError::ScanError {
                        message: e.to_string(),
                    })?;
                let addresses = non_circulating_supply.accounts.into_iter().collect();
                let address_filter = match filter {
                    RpcLargestAccountsFilter::Circulating => AccountAddressFilter::Exclude,
                    RpcLargestAccountsFilter::NonCirculating => AccountAddressFilter::Include,
                };
                (addresses, address_filter)
            } else {
                (HashSet::new(), AccountAddressFilter::Exclude)
            };
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

**File:** rpc/src/rpc_cache.rs (L44-58)
```rust
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
