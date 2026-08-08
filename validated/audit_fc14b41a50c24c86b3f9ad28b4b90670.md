### Title
`getLargestAccounts` RPC handler returns stale, commitment-mismatched cached results - (File: rpc/src/rpc.rs)

### Summary
`JsonRpcRequestProcessor::get_largest_accounts()` caches its result in `LargestAccountsCache`, but the cache is keyed only by the optional `RpcLargestAccountsFilter`, not by the requested commitment level or bank slot. As a result, a cached response computed for one commitment/bank can be served back to a request made against a different (fresher or differently-forked) bank/commitment, similar to the Notional bug where a stale, non-refreshed state snapshot (`factors`) was used to compute the value returned by a view function.

### Finding Description
`get_largest_accounts` first resolves the bank for the requested commitment via `self.bank(config.commitment)` and then checks the cache using only the `filter` field: [1](#0-0) 

`LargestAccountsCache::get_largest_accounts` looks the entry up purely by `filter` and returns it if it hasn't expired, with no comparison against the bank/slot that was actually requested: [2](#0-1) 

The cache is populated with `bank.slot()` from whichever bank happened to be resolved on the *first* call for a given filter: [3](#0-2) 

Consequently, if a client first calls `getLargestAccounts` with `commitment: "processed"` (fetching the working/heaviest bank) and shortly after with `commitment: "finalized"` (which should read the rooted bank), the second call gets `self.bank(finalized)` (a different, lagging bank) but then bypasses reading anything from that bank at all — it simply returns whatever was cached from the "processed" bank, together with that earlier bank's slot in the response context. The `commitment.slot_with_commitment()`/`bank()` resolution work is discarded once a cache hit occurs. This is directly analogous to the Notional bug: a value that should be recomputed from the currently-relevant state (`factors` recomputed vs. `getPrimeCashFactors`, here: the bank resolved for the given commitment) is instead served from a stale, previously captured value, because the caching/lookup path does not account for which "context" (commitment/bank) the caller actually asked about.

### Impact Explanation
This causes `getLargestAccounts` — a JSON-RPC handler reachable by any unprivileged RPC client — to return wrong-slot data: the `context.slot` and the account/lamport values returned may belong to an older bank/fork than what the caller's commitment level requested. Downstream consumers (explorers, monitoring tools, bots polling for balance changes) can be misled about current state at a given commitment level. This matches the "wrong-slot/fork/account data returned" class explicitly accepted in the validation criteria.

### Likelihood Explanation
Any unprivileged client can trigger this deterministically by issuing two `getLargestAccounts` calls with different `commitment` values (e.g., `processed` then `finalized`) using the same `filter` within the cache TTL window; no special access or timing race is required.

### Recommendation
Include the resolved bank's slot (or the requested commitment level) as part of the `LargestAccountsCache` key, or validate that the cached entry's slot is still valid/consistent for the commitment currently being requested (e.g., verify `cached_slot <= bank.slot()` and that it is an ancestor for `processed`/`confirmed`, or matches the rooted slot for `finalized`) before returning a cache hit; otherwise recompute for the newly resolved bank.

### Proof of Concept
1. Start a validator RPC node with the default full API enabled.
2. Client A calls `getLargestAccounts` with `{"commitment": "processed"}` (or no commitment) — this populates the cache keyed by `filter = None`, storing the current working-bank slot `S1`.
3. Advance the working bank a few slots without rooting/finalizing them.
4. Client B calls `getLargestAccounts` with `{"commitment": "finalized"}` within the cache TTL — instead of resolving and scanning the rooted/finalized bank, the handler short-circuits at `get_cached_largest_accounts` and returns the cached result with `context.slot = S1` and the account balances captured from the earlier `processed` bank, even though the caller explicitly asked for `finalized` state.

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

**File:** rpc/src/rpc_cache.rs (L30-42)
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
```
