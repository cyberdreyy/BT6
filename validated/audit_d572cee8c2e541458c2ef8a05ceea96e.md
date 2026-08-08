### Title
`getLargestAccounts` full-account scan cost scales linearly with total on-chain account count with no per-request bound - ([File: accounts-db/src/accounts.rs])

### Finding Description
`JsonRpcRequestProcessor::get_largest_accounts` (rpc/src/rpc.rs:1067-1119) serves the `getLargestAccounts` JSON-RPC method [1](#0-0) . On a cache miss it calls `bank.get_largest_accounts(NUM_LARGEST_ACCOUNTS, &addresses, address_filter)` inside `spawn_blocking` [2](#0-1) . `Bank::get_largest_accounts` forwards straight to `Accounts::load_largest_accounts` [3](#0-2) , which invokes `self.accounts_db.scan_accounts(ancestors, bank_id, ..., &ScanConfig::default())` [4](#0-3) . `scan_accounts` walks the entire account index visible to the bank (every root/ancestor slot), invoking the closure once per live account; the only per-item work is a lamports check, a `HashSet` membership test, and a bounded `BinaryHeap` update of size `num` (`NUM_LARGEST_ACCOUNTS`). There is no limit on the number of accounts iterated — the iteration count equals the total number of accounts held by the bank, not a value under RPC/request control. `NUM_LARGEST_ACCOUNTS` only bounds the *result* size, not the scan cost.

The only mitigating factor is `LargestAccountsCache` (rpc/src/rpc_cache.rs), which memoizes the last result per `filter` value (only 3 possible keys: `None`, `Circulating`, `NonCirculating`) for a fixed TTL [5](#0-4) . Once the cache entry expires, the next call (even a single call, one per filter) re-triggers the full O(total_accounts) scan. Because an unprivileged client can create arbitrarily many accounts on-chain (rent-paying, but otherwise unprivileged), it can grow `total_accounts` without bound, and each subsequent `getLargestAccounts` call after cache expiry costs proportionally more CPU/IO — with no fixed per-request budget independent of on-chain state size.

### Impact Explanation
This matches the "unbounded cost for a single low-rate call" category: a single JSON-RPC call issued at a rate compliant with `CLUSTER_SLOT_TIME_TARGET / 2` (i.e., no more than once per the cache TTL/slot-time budget) forces the validator to scan the full account set, and that cost is directly and only bounded by total on-chain account count, which the attacker unprivilegedly controls by creating more accounts. This creates a resource-consumption vector on RPC/API nodes proportional to chain growth, not to any fixed configured limit.

### Likelihood Explanation
The scan runs in `spawn_blocking`, so it does not block the async reactor, limiting blast radius to blocking-thread-pool exhaustion/CPU time rather than a full node crash. The cache (rpc/src/rpc_cache.rs) reduces the achievable call frequency of full scans to roughly once per cache TTL rather than once per JSON-RPC call, but does not remove the fundamental linear scaling with total account count, and the PoC's premise (cost grows with total accounts, no fixed cap) still holds for calls spaced beyond the cache TTL, which is compatible with the stated one-call-per-`CLUSTER_SLOT_TIME_TARGET/2` constraint if the TTL is at or below that interval. This is a real, reproducible design property, not a crash/consensus bug.

### Recommendation
Add an explicit, chain-size-independent cost bound to `getLargestAccounts`: e.g., maintain an incrementally-updated largest-accounts structure inside `AccountsDb` rather than scanning on demand, or lengthen/enforce the `LargestAccountsCache` TTL to be provably ≥ the minimum achievable client call interval, and document/enforce this coupling so cache misses cannot be forced at attacker-controlled cadence.

### Proof of Concept
```rust
// accounts-db/src/accounts.rs (benchmark-style test)
#[test]
fn bench_load_largest_accounts_scales_with_total_accounts() {
    use std::time::Instant;
    for &n in &[10_000usize, 100_000, 500_000] {
        let accounts_db = AccountsDb::default_for_tests();
        let accounts = Accounts::new(Arc::new(accounts_db));
        for i in 0..n {
            let pubkey = solana_pubkey::new_rand();
            let account = AccountSharedData::new(1 + i as u64, 0, &Pubkey::default());
            accounts.store_for_tests(0, &pubkey, &account);
        }
        accounts.add_root_and_flush_write_cache(0);

        let ancestors = Ancestors::from(vec![0]);
        let start = Instant::now();
        let _ = accounts
            .load_largest_accounts(&ancestors, 0, 20, &HashSet::new(), AccountAddressFilter::Exclude)
            .unwrap();
        println!("n={n} elapsed={:?}", start.elapsed());
        // Assert: elapsed time grows roughly linearly with n,
        // confirming scan cost is proportional to total accounts,
        // not to `num` (20) or any fixed request-side budget.
    }
}
```
Expected result: wall-clock time for a single `getLargestAccounts`-equivalent call grows roughly linearly with `n` (total accounts), demonstrating the absence of a fixed per-call cost cap independent of on-chain account count.

### Citations

**File:** rpc/src/rpc.rs (L1096-1108)
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
```

**File:** rpc/src/rpc.rs (L3373-3378)
```rust
        #[rpc(meta, name = "getLargestAccounts")]
        fn get_largest_accounts(
            &self,
            meta: Self::Metadata,
            config: Option<RpcLargestAccountsConfig>,
        ) -> BoxFuture<Result<RpcResponse<Vec<RpcAccountBalance>>>>;
```

**File:** runtime/src/bank.rs (L5201-5214)
```rust
    pub fn get_largest_accounts(
        &self,
        num: usize,
        filter_by_address: &HashSet<Pubkey>,
        filter: AccountAddressFilter,
    ) -> ScanResult<Vec<(Pubkey, u64)>> {
        self.rc.accounts.load_largest_accounts(
            &self.ancestors,
            self.bank_id,
            num,
            filter_by_address,
            filter,
        )
    }
```

**File:** accounts-db/src/accounts.rs (L255-296)
```rust
    pub fn load_largest_accounts(
        &self,
        ancestors: &Ancestors,
        bank_id: BankId,
        num: usize,
        filter_by_address: &HashSet<Pubkey>,
        filter: AccountAddressFilter,
    ) -> ScanResult<Vec<(Pubkey, u64)>> {
        if num == 0 {
            return Ok(vec![]);
        }
        let mut account_balances = BinaryHeap::new();
        self.accounts_db.scan_accounts(
            ancestors,
            bank_id,
            |option| {
                if let Some((pubkey, account, _slot)) = option {
                    if account.lamports() == 0 {
                        return;
                    }
                    let contains_address = filter_by_address.contains(pubkey);
                    let collect = match filter {
                        AccountAddressFilter::Exclude => !contains_address,
                        AccountAddressFilter::Include => contains_address,
                    };
                    if !collect {
                        return;
                    }
                    if account_balances.len() == num {
                        let Reverse(entry) = account_balances
                            .peek()
                            .expect("BinaryHeap::peek should succeed when len > 0");
                        if *entry >= (account.lamports(), *pubkey) {
                            return;
                        }
                        account_balances.pop();
                    }
                    account_balances.push(Reverse((account.lamports(), *pubkey)));
                }
            },
            &ScanConfig::default(),
        )?;
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
