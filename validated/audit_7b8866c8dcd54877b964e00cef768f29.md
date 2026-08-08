### Title
Index-scan byte-limit check in `accumulate_and_check_scan_result_size` allows one over-limit account to be materialized into the RPC scan collector before the scan aborts - (File: accounts-db/src/accounts.rs)

### Summary
`Accounts::load_by_index_key_with_filter` checks `scan_results_limit_bytes` only *after* the current account has already been included by the inner filter closure, so the account whose size pushes the running total past the limit is still cloned into the result `Vec` before the scan is aborted. A single `getProgramAccounts`/`getTokenAccountsByOwner` call against a `ProgramId`/`SplTokenOwner`/`SplTokenMint` secondary index can therefore make the collector briefly hold up to one maximal-size account (bounded by the runtime's max account data size) beyond the configured `scan_results_limit_bytes`.

### Finding Description
`load_by_index_key_with_filter` wraps the caller-supplied `filter` in a closure that is passed to `load_while_filtering`: [1](#0-0) 

The closure computes `use_account = filter(account)` first, and only *afterwards*, if `use_account` is true, calls `accumulate_and_check_scan_result_size` to update the running sum and decide whether to call `config.abort()`. Crucially, `use_account`'s value was already fixed to `true` before the size check runs, so the closure still returns `true` for the very account that causes the overflow. `load_while_filtering` (defined earlier in the same file) uses the closure's boolean return value to decide whether to push the account into `collector`, meaning the over-limit account is unconditionally materialized/cloned into the `Vec<KeyedAccountSharedData>` collector regardless of the abort signal.

`config.abort()` only stops the *next* iteration of the index scan loop in `index_scan_accounts`: [2](#0-1) 
`is_aborted()` is checked at the top of the loop before processing the *next* pubkey, so exactly one already-fetched, already-cloned account beyond the limit is retained in `collector` at the moment `maybe_abort_scan` finally converts the result to an `Err`: [3](#0-2) 

`get_filtered_indexed_accounts` in the RPC layer passes `scan_results_limit_bytes` straight through to this code path for `getProgramAccounts` (with `ProgramId` index), `getTokenAccountsByOwner`, and `getTokenAccountsByMint`: [4](#0-3) [5](#0-4) [6](#0-5) 

An attacker who stores several maximal-size accounts under a single indexed key (owner/mint/program) such that the accumulated size crosses `scan_results_limit_bytes` exactly on one of the max-size accounts can cause that account's full data buffer to be cloned into `collector`, temporarily inflating peak memory in the RPC-handling blocking thread by up to one maximal account's size before the scan errors out and `collector` is dropped.

### Impact Explanation
This is a resource-bound violation, not an unbounded-cost bug: the configured `scan_results_limit_bytes` is a soft cap that can be exceeded by exactly one account's serialized size (data length + `size_of::<AccountSharedData>()` + `size_of::<Pubkey>()`), capped by the runtime's maximum account data size. The eventual result of the RPC call is still an error (`RpcCustomError::ScanError`), so there is no data leak, but the peak transient heap usage for a single low-rate call is higher than what the operator configured via `--rpc-scan-results-limit-bytes`, contradicting the stated purpose of that flag (bounding scan memory cost). This matches the "unbounded/underestimated cost for a single low-rate call" bounty category, scoped to the bounded overshoot described (not unlimited memory growth).

### Likelihood Explanation
Requires only that the operator has enabled a `ProgramId`, `SplTokenOwner`, or `SplTokenMint` secondary index and configured `scan_results_limit_bytes` (both are supported, documented configuration options, not privileged operator misconfiguration in the excluded sense) [7](#0-6) . An unprivileged attacker can store several maximal-size accounts on-chain under an owner/mint/program key they control and issue a single `getProgramAccounts`/`getTokenAccountsByOwner` call that straddles the limit — well within the "one call" constraint. This is fully deterministic and repeatable.

### Recommendation
Move the size check ahead of the inclusion decision: compute the account's contribution to `sum` and check the limit before returning `true` from the filter/inclusion closure, so the account that would cross `scan_results_limit_bytes` is excluded from `collector` (or trimmed) and the scan aborts before that account is cloned in. Concretely, in `load_by_index_key_with_filter`, evaluate `accumulate_and_check_scan_result_size` first and only set `use_account = filter(account) && !exceeded` before calling `load_while_filtering`.

### Proof of Concept
```rust
// runtime/src/bank/tests.rs (extend existing test module)
#[test]
fn test_get_filtered_indexed_accounts_peak_memory_overshoot() {
    let (genesis_config, _mint_keypair) = create_genesis_config(500);
    let mut account_indexes = AccountSecondaryIndexes::default();
    account_indexes.indexes.insert(AccountIndex::ProgramId);
    let bank_config = BankTestConfig {
        accounts_db_config: AccountsDbConfig {
            account_indexes: Some(account_indexes),
            ..ACCOUNTS_DB_CONFIG_FOR_TESTING
        },
    };
    let bank = Arc::new(Bank::new_with_paths_for_tests(
        &genesis_config,
        Some(bank_config),
        vec![],
        None,
    ));

    let program_id = Pubkey::new_unique();
    // craft accounts whose accumulated size straddles the limit:
    // first account exactly at the limit boundary, second account maximal size
    let small_len = 100;
    let max_len = 10 * 1024 * 1024; // MAX_PERMITTED_DATA_LENGTH
    let limit = small_len + zero_len_account_size(); // limit crossed exactly after first account

    let addr1 = Pubkey::new_unique();
    bank.store_account(&addr1, &AccountSharedData::new(1, small_len, &program_id));
    let addr2 = Pubkey::new_unique();
    bank.store_account(&addr2, &AccountSharedData::new(1, max_len, &program_id));

    let result = bank.get_filtered_indexed_accounts(
        &IndexKey::ProgramId(program_id),
        |_| true,
        Some(limit),
    );

    // Scan must abort (existing behavior)...
    assert!(result.is_err());

    // ...but the bug is that internally, `collector` (dropped inside
    // load_by_index_key_with_filter before returning Err) briefly held
    // addr2's full max_len buffer -- i.e., peak memory during the call
    // exceeded `limit` by up to `max_len` bytes. This requires
    // instrumenting `collector`'s size (e.g., via a custom allocator or
    // by making `load_while_filtering` cloneable/instrumentable for the
    // unit test) to assert peak_collector_bytes <= limit + SLACK_CONSTANT
    // fails.
}
```
A precise invariant test requires instrumenting `collector`'s byte size at the point of push inside `load_while_filtering`/`load_by_index_key_with_filter` (e.g., temporarily exposing a hook or building a debug build with an allocation tracker) to assert that `collector`'s total materialized size never exceeds `scan_results_limit_bytes + slack`; as shown by the code trace above, this assertion fails by up to one maximal account's size.

### Citations

**File:** accounts-db/src/accounts.rs (L360-394)
```rust
    fn calc_scan_result_size(account: &AccountSharedData) -> usize {
        account.data().len()
            + std::mem::size_of::<AccountSharedData>()
            + std::mem::size_of::<Pubkey>()
    }

    /// Accumulate size of (pubkey + account) into sum.
    /// Return true iff sum > 'byte_limit_for_scan'
    fn accumulate_and_check_scan_result_size(
        sum: &AtomicUsize,
        account: &AccountSharedData,
        byte_limit_for_scan: &Option<usize>,
    ) -> bool {
        if let Some(byte_limit_for_scan) = byte_limit_for_scan.as_ref() {
            let added = Self::calc_scan_result_size(account);
            sum.fetch_add(added, Ordering::Relaxed)
                .saturating_add(added)
                > *byte_limit_for_scan
        } else {
            false
        }
    }

    fn maybe_abort_scan(
        result: ScanResult<Vec<KeyedAccountSharedData>>,
        config: &ScanConfig,
    ) -> ScanResult<Vec<KeyedAccountSharedData>> {
        if config.is_aborted() {
            ScanResult::Err(ScanError::Aborted(
                "The accumulated scan results exceeded the limit".to_string(),
            ))
        } else {
            result
        }
    }
```

**File:** accounts-db/src/accounts.rs (L413-428)
```rust
                |some_account_tuple| {
                    Self::load_while_filtering(&mut collector, some_account_tuple, |account| {
                        let use_account = filter(account);
                        if use_account
                            && Self::accumulate_and_check_scan_result_size(
                                &sum,
                                account,
                                &byte_limit_for_scan,
                            )
                        {
                            // total size of results exceeds size limit, so abort scan
                            config.abort();
                        }
                        use_account
                    });
                },
```

**File:** accounts-db/src/accounts_db.rs (L3398-3410)
```rust
        for pubkey in self.accounts_index.get_index_key_pubkeys(&index_key) {
            if config.is_aborted() {
                break;
            }
            if let Some((account, slot)) = self.do_load(
                ancestors,
                &pubkey,
                LoadHint::Unspecified,
                PopulateReadCache::False,
            ) {
                scan_func(Some((&pubkey, account, slot)));
            }
        }
```

**File:** rpc/src/rpc.rs (L309-341)
```rust
    pub async fn get_filtered_indexed_accounts(
        &self,
        bank: &Arc<Bank>,
        index_key: &IndexKey,
        program_id: &Pubkey,
        filters: Vec<RpcFilterType>,
        sort_results: bool,
    ) -> ScanResult<Vec<KeyedAccountSharedData>> {
        let bank = Arc::clone(bank);
        let index_key = index_key.to_owned();
        let program_id = program_id.to_owned();
        let byte_limit_for_scans = self.config.scan_results_limit_bytes;
        let mut accounts = self
            .runtime
            .spawn_blocking(move || {
                bank.get_filtered_indexed_accounts(
                    &index_key,
                    |account| {
                        // The program-id account index checks for Account owner on inclusion.
                        // However, due to the current AccountsDb implementation, an account may
                        // remain in storage as a zero-lamport AccountSharedData::Default() after
                        // being wiped and reinitialized in later updates. We include the redundant
                        // filters here to avoid returning these accounts.
                        account.owner().eq(&program_id)
                            && filters
                                .iter()
                                .all(|filter_type| filter_allows(filter_type, account))
                    },
                    byte_limit_for_scans,
                )
            })
            .await
            .expect("Failed to spawn blocking task")?;
```

**File:** rpc/src/rpc.rs (L2262-2280)
```rust
        if self
            .config
            .account_indexes
            .contains(&AccountIndex::ProgramId)
        {
            if !self.config.account_indexes.include_key(&program_id) {
                return Err(RpcCustomError::KeyExcludedFromSecondaryIndex {
                    index_key: program_id.to_string(),
                });
            }
            self.get_filtered_indexed_accounts(
                &bank,
                &IndexKey::ProgramId(program_id),
                &program_id,
                filters,
                sort_results,
            )
            .await
            .map_err(|e| RpcCustomError::ScanError {
```

**File:** rpc/src/rpc.rs (L2332-2352)
```rust
        if self
            .config
            .account_indexes
            .contains(&AccountIndex::SplTokenOwner)
        {
            if !self.config.account_indexes.include_key(&owner_key) {
                return Err(RpcCustomError::KeyExcludedFromSecondaryIndex {
                    index_key: owner_key.to_string(),
                });
            }
            self.get_filtered_indexed_accounts(
                &bank,
                &IndexKey::SplTokenOwner(owner_key),
                &program_id,
                filters,
                sort_results,
            )
            .await
            .map_err(|e| RpcCustomError::ScanError {
                message: e.to_string(),
            })
```

**File:** validator/src/commands/run/args/json_rpc_config.rs (L183-188)
```rust
            .takes_value(true)
            .help(
                "How large accumulated results from an accounts index scan can become. If this is \
                 exceeded, the scan aborts.",
            ),
    ]
```
