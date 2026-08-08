### Title
`get_largest_accounts` cache keyed only by filter, not commitment, causes cross-commitment stale/unrooted data leakage - ([File: rpc/src/rpc_cache.rs])

### Summary
`LargestAccountsCache` stores results keyed solely by `Option<RpcLargestAccountsFilter>`, with no dimension for the requested `CommitmentConfig` or the bank slot/rootedness. A client can call `getLargestAccounts` with `commitment=processed` against an unrooted bank, then immediately call it again with `commitment=finalized`; the second call hits the shared cache and returns the data computed from the earlier unrooted/processed bank instead of re-deriving it from the finalized (rooted) bank.

### Finding Description
In `JsonRpcRequestProcessor::get_largest_accounts` [1](#0-0) , the flow is:
1. `let bank = self.bank(config.commitment);` — resolves a bank corresponding to the requested commitment (processed vs. finalized banks can be different slots/forks).
2. `self.get_cached_largest_accounts(&config.filter)` looks up the cache keyed **only** by `config.filter` [2](#0-1) , completely ignoring `config.commitment` and the resolved bank's slot. If a cache entry exists (not yet expired), it is returned immediately without ever consulting `bank` at all.
3. Otherwise, results are computed from the `bank` (whichever commitment resolved it) and stored via `set_cached_largest_accounts(&config.filter, bank.slot(), &accounts)` [3](#0-2) , again keyed only by `filter`.

The underlying `LargestAccountsCache` struct confirms this: the `cache: HashMap<Option<RpcLargestAccountsFilter>, LargestAccountsCacheValue>` has no commitment or slot key component [4](#0-3) , and `get_largest_accounts`/`set_largest_accounts` only index by `filter` [5](#0-4) .

Exploit flow:
- Call `getLargestAccounts(commitment=processed)` — this populates the cache from a bank at slot `S_unrooted`, which may later be dropped from the fork (never rooted/finalized).
- Immediately call `getLargestAccounts(commitment=finalized)` — the RPC layer still resolves `bank = self.bank(finalized)` to the correct rooted bank, but before doing any work, `get_cached_largest_accounts` returns the entry populated by the *processed* call, whose `accounts`/`slot` were derived from `S_unrooted`. The response carries `RpcResponseContext::new(slot)` using the stale unrooted slot, and the account balances reflect a bank state that may never become part of the canonical (rooted) chain.

This violates the stated invariant that finalized answers should only be derived from rooted-bank data; a single unprivileged client, issuing two ordinary API calls in sequence, can obtain incorrect data for a `finalized`-commitment request.

### Impact Explanation
This is a "wrong-slot/fork data returned" class of bug: a `finalized`-commitment RPC response can present financial/state data (largest account balances and the slot they belong to) that stems from an unrooted, possibly-forked-away bank rather than the canonical finalized state. Because `getLargestAccounts` is commonly used for auditing/economic snapshots, this can mislead any client relying on `finalized` commitment guarantees, and is triggerable by a single unprivileged client with two sequential calls at normal call rates — well within the allowed one call per `CLUSTER_SLOT_TIME_TARGET / 2`.

### Likelihood Explanation
Feasible and fully reachable via public JSON-RPC with no special privileges: any client can call `getLargestAccounts` twice back-to-back with differing `commitment` values. It requires that the validator is actively processing forks/rollback (a normal occurrence at the tip of the chain, not a contrived precondition), and that the cache TTL (`duration` in `LargestAccountsCache::new`) has not yet expired between the two calls — which is trivially satisfiable since the default cache window is on the order of seconds and the two calls can be issued back-to-back. This makes the bug reliably reproducible.

### Recommendation
Key the cache by `(CommitmentConfig, Option<RpcLargestAccountsFilter>)` (or at minimum include the bank's rootedness/slot) instead of `Option<RpcLargestAccountsFilter>` alone, so that a cache entry populated under one commitment level can never be returned for a request at a different commitment level. Alternatively, disable the cache for anything other than a single, well-defined commitment level (e.g., only cache `finalized` results, and always compute fresh for `processed`/`confirmed`), and validate that `value.slot` corresponds to a currently-rooted slot before serving it for `finalized` requests.

### Proof of Concept
```rust
// rpc/src/rpc_cache.rs (new test) — demonstrates key collision across commitment levels
#[test]
fn test_cache_ignores_commitment_dimension() {
    let mut cache = LargestAccountsCache::new(60); // 60s TTL, plenty of time between calls
    let filter = None;

    // Simulate a "processed" call populating the cache from an unrooted bank at slot 100
    let unrooted_accounts = vec![RpcAccountBalance {
        address: "11111111111111111111111111111111".to_string(),
        lamports: 999_999,
    }];
    cache.set_largest_accounts(&filter, 100 /* unrooted slot */, &unrooted_accounts);

    // Simulate an immediately-following "finalized" call.
    // Real finalized bank would be at a different (rooted) slot, e.g. 90,
    // with different account data, but the cache has no commitment/slot key
    // to distinguish the two requests.
    let cached = cache.get_largest_accounts(&filter);

    // BUG: the finalized request incorrectly receives data tagged with the
    // unrooted slot 100 from the prior processed-commitment call.
    assert_eq!(cached, Some((100, unrooted_accounts)));
    // Expected (fixed) behavior: cache miss for finalized commitment, forcing
    // recomputation from the actual finalized/rooted bank.
}
```

An integration-level PoC would extend this by driving a `BankForks` with a fork where slot 100 is later pruned/never rooted, calling `JsonRpcRequestProcessor::get_largest_accounts` with `commitment=processed` (bank at slot 100), then with `commitment=finalized` (bank at rooted slot 90), and asserting that the second response's `context.slot` and `value` differ from the first and reflect only rooted-bank data — which currently fails because the second call hits the shared cache entry from the first.

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

**File:** rpc/src/rpc.rs (L1067-1119)
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
    }
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
