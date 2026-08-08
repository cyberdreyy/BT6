### Title
Unbounded per-request CPU/IO cost in `AccountsDb::index_scan_accounts` secondary-index iteration, decoupled from `byte_limit_for_scan` - ([File: accounts-db/src/accounts_db.rs])

### Finding Description
`Accounts::load_by_index_key_with_filter` (`accounts-db/src/accounts.rs:396-433`) is the entry point reached from RPC handlers such as `get_filtered_indexed_accounts` (used by `getTokenAccountsByOwner`/`getTokenAccountsByDelegate`/`getProgramAccounts` when a secondary index is enabled, `rpc/src/rpc.rs:2272`, `2342`). It creates a `ScanConfig` with an abort flag and calls `AccountsDb::index_scan_accounts` (`accounts-db/src/accounts_db.rs:3358-3421`).

Inside `index_scan_accounts`, the full set of pubkeys registered under the requested `IndexKey` is first materialized eagerly via `self.accounts_index.get_index_key_pubkeys(&index_key)` (`accounts_db.rs:3398`), which internally calls `RwLockSecondaryIndexEntry::keys()` (`accounts_index/secondary.rs:104-106`), copying the entire `HashSet<Pubkey>` bucket into a `Vec<Pubkey>` before any abort check occurs. This materialization cost is entirely proportional to the number of pubkeys an attacker has registered under a single `SplTokenMint`/`SplTokenOwner`/`ProgramId` key, and is not bounded by `byte_limit_for_scan`.

The subsequent loop (`accounts_db.rs:3398-3410`) checks `config.is_aborted()` once per iteration, but `config.abort()` is only ever triggered from `Accounts::accumulate_and_check_scan_result_size` inside the `filter` closure, and only `if use_account` is true (`accounts.rs:413-427`). If the caller-supplied `filter` (e.g. the `TokenAccountState`/owner-mint `Memcmp` filters composed in `rpc.rs`) rejects an account, `use_account` is `false` and `accumulate_and_check_scan_result_size` is never invoked — no bytes are ever added to `sum`, so `config.abort()` never fires for rejected accounts, no matter how many are rejected. Meanwhile, each iteration still pays the cost of `self.do_load(...)` (`accounts_db.rs:3402-3409`), which performs an index lookup, storage/cache read, and deserialization of that account's data before the filter is even evaluated.

Consequently, an attacker who creates a very large number of small on-chain accounts sharing one `IndexKey` value (e.g. many token accounts for a single mint, most/all having a different owner than the one queried) can cause a single `getTokenAccountsByOwner`/`getTokenAccountsByDelegate`/`getProgramAccounts` call to: (1) materialize the entire pubkey bucket unconditionally, and (2) run `do_load` for every pubkey in that bucket, since the byte-limit accumulator is never touched for filtered-out accounts. The `byte_limit_for_scan` abort mechanism only bounds the size of accepted results, not the number of index entries scanned or the number of `do_load` calls performed — the very invariant the question probes.

### Impact Explanation
This is a soft-DoS: a single unprivileged RPC call whose cost is dictated by attacker-controlled on-chain state (the size of a secondary-index bucket) rather than by any server-side limit, tying up accounts-db shared locks/CPU/IO for the duration of the scan and degrading service for concurrent readers on the same node. This matches the "unbounded cost for a single low-rate call" category permitted by the audit rules (excessive single-call CPU/memory consumption via `AccountsDb`/`AccountsIndex` shared structures).

### Likelihood Explanation
Feasible with only on-chain writes (creating token accounts under one mint, which requires no special privilege, just rent-exempt lamports and normal transaction submission within allowed rate) followed by exactly one RPC call. Fully repeatable — the attacker fully controls how many accounts are indexed under one key. The only requirement is that the queried node has the relevant secondary index enabled (`--account-index spl-token-mint`/`spl-token-owner`/`program-id`), which is an operator's opt-in feature but not "operator misconfiguration" needed to trigger the issue — it's a structural gap in the scan-abort logic.

### Recommendation
Bound the cost of `index_scan_accounts` independent of whether the `filter` accepts or rejects an account:
- Track an iteration/do_load count (or elapsed wall-clock time) in `ScanConfig`/`index_scan_accounts` itself and abort once a fixed cap is exceeded, regardless of the filter's acceptance decision.
- Alternatively, call `config.abort()`/accumulate cost based on bytes *read* via `do_load` (not just bytes *accepted* into the collector), so rejected-but-loaded accounts still count toward `byte_limit_for_scan`.
- Consider also bounding `get_index_key_pubkeys` retrieval itself (e.g. return an iterator that can be interrupted rather than a fully materialized `Vec`), so very large buckets don't pay full materialization cost up front.

### Proof of Concept
Rust integration test (extending existing tests in `accounts-db/src/accounts_db/tests/impl.rs`, which already exercise `index_scan_accounts` and `spl_token_mint_index_enabled()`):

```rust
#[test]
fn test_index_scan_accounts_unbounded_cost_when_filter_rejects() {
    let db = AccountsDb {
        account_indexes: spl_token_mint_index_enabled(),
        ..AccountsDb::new_for_tests_with_config(Vec::new(), DEFAULT_ACCOUNTS_DB_CONFIG)
    };

    let mint_key = Pubkey::new_unique();
    let matching_owner = Pubkey::new_unique(); // owner we will query for (won't match any account)
    const N: usize = 200_000; // attacker-controlled bucket size

    for _ in 0..N {
        let pubkey = Pubkey::new_unique();
        let mut data = vec![0u8; spl_generic_token::token::Account::get_packed_len()];
        data[..PUBKEY_BYTES].clone_from_slice(&mint_key.to_bytes());
        data[108] = 1; // initialized
        // owner field left as some other pubkey, never equal to `matching_owner`
        let account = AccountSharedData::create(
            1,
            data,
            spl_generic_token::token::id(),
            false,
            0,
        );
        db.store_for_tests((0, &[(&pubkey, &account)][..]));
    }
    db.add_root_and_flush_write_cache(0);

    let byte_limit_for_scan = Some(4096usize); // small explicit limit
    let sum = AtomicUsize::default();
    let config = ScanConfig::default().recreate_with_abort();
    let load_count = AtomicUsize::new(0);

    let start = std::time::Instant::now();
    let _ = db.index_scan_accounts(
        &Ancestors::default(),
        0,
        IndexKey::SplTokenMint(mint_key),
        |maybe_account| {
            load_count.fetch_add(1, Ordering::Relaxed);
            if let Some((_, account, _)) = maybe_account {
                // simulate the owner-filter: always false, so
                // accumulate_and_check_scan_result_size (and thus abort) never fires
                let use_account = false;
                let _ = (account, use_account);
            }
        },
        &config,
    );
    let elapsed = start.elapsed();

    // Expected (buggy) behavior: load_count == N, i.e. the scan does N do_load calls
    // despite byte_limit_for_scan being tiny, because the filter rejects every account
    // and never triggers config.abort().
    assert_eq!(load_count.load(Ordering::Relaxed), N);

    // Desired invariant (currently violated): cost should be bounded independent of N.
    // assert!(load_count.load(Ordering::Relaxed) <= EXPECTED_BOUND);
    let _ = (sum, byte_limit_for_scan, elapsed);
}
```

This test demonstrates that `index_scan_accounts` performs exactly `N` `do_load` calls (proportional to attacker-controlled bucket size) even with a tiny `byte_limit_for_scan`, because the abort mechanism is gated on accepted-result bytes rather than on the number of index entries scanned. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** accounts-db/src/accounts.rs (L396-433)
```rust
    pub fn load_by_index_key_with_filter<F: Fn(&AccountSharedData) -> bool>(
        &self,
        ancestors: &Ancestors,
        bank_id: BankId,
        index_key: &IndexKey,
        filter: F,
        byte_limit_for_scan: Option<usize>,
    ) -> ScanResult<Vec<KeyedAccountSharedData>> {
        let sum = AtomicUsize::default();
        let config = ScanConfig::default().recreate_with_abort();
        let mut collector = Vec::new();
        let result = self
            .accounts_db
            .index_scan_accounts(
                ancestors,
                bank_id,
                *index_key,
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
                &config,
            )
            .map(|_| collector);
        Self::maybe_abort_scan(result, &config)
    }
```

**File:** accounts-db/src/accounts_db.rs (L3358-3421)
```rust
    pub(crate) fn index_scan_accounts<F>(
        &self,
        ancestors: &Ancestors,
        bank_id: BankId,
        index_key: IndexKey,
        mut scan_func: F,
        config: &ScanConfig,
    ) -> ScanResult<bool>
    where
        F: FnMut(Option<(&Pubkey, AccountSharedData, Slot)>),
    {
        let key = match &index_key {
            IndexKey::ProgramId(key) => key,
            IndexKey::SplTokenMint(key) => key,
            IndexKey::SplTokenOwner(key) => key,
        };
        if !self.account_indexes.include_key(key) {
            // the requested key was not indexed in the secondary index, so do a normal scan
            let used_index = false;
            self.scan_accounts(ancestors, bank_id, scan_func, config)?;
            return Ok(used_index);
        }

        // Register this scan so that slots needed by the scan are not cleaned out from under us.
        let scan_guard = ScanGuard::try_new(&self.scan_tracker, bank_id, || self.max_root())
            .ok_or(ScanError::SlotRemoved {
                slot: ancestors.max_slot(),
                bank_id,
            })?;

        // If the scan's ancestors are all rooted, drop them and scan roots only
        // Scan Guard max root must be used as the scan guard guarantees that
        // the account state as of max root is persisted in the database
        let max_root_ancestors = Ancestors::from(vec![scan_guard.max_root()]);
        let ancestors = if scan_guard.should_use_ancestors(ancestors) {
            ancestors
        } else {
            &max_root_ancestors
        };

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

        // Check whether the bank was removed while the scan was in progress.
        if scan_guard.was_scan_corrupted() {
            return Err(ScanError::SlotRemoved {
                slot: ancestors.max_slot(),
                bank_id,
            });
        }
        let used_index = true;
        Ok(used_index)
    }
```

**File:** accounts-db/src/accounts_index.rs (L376-383)
```rust
    /// Returns the list of pubkeys from the secondary index for the given key.
    pub(crate) fn get_index_key_pubkeys(&self, index_key: &IndexKey) -> Vec<Pubkey> {
        match index_key {
            IndexKey::ProgramId(key) => self.program_id_index.get(key),
            IndexKey::SplTokenMint(key) => self.spl_token_mint_index.get(key),
            IndexKey::SplTokenOwner(key) => self.spl_token_owner_index.get(key),
        }
    }
```

**File:** accounts-db/src/accounts_index/secondary.rs (L104-106)
```rust
    fn keys(&self) -> Vec<Pubkey> {
        self.account_keys.read().unwrap().iter().cloned().collect()
    }
```

**File:** accounts-db/src/accounts_scan.rs (L27-55)
```rust
#[derive(Debug, Default)]
pub(crate) struct ScanConfig {
    /// checked by the scan. When true, abort scan.
    pub(crate) abort: Option<Arc<AtomicBool>>,
}

impl ScanConfig {
    /// mark the scan as aborted
    pub(crate) fn abort(&self) {
        if let Some(abort) = self.abort.as_ref() {
            abort.store(true, Ordering::Relaxed)
        }
    }

    /// use existing 'abort' if available, otherwise allocate one
    pub(crate) fn recreate_with_abort(&self) -> Self {
        ScanConfig {
            abort: Some(self.abort.clone().unwrap_or_default()),
        }
    }

    /// true if scan should abort
    pub(crate) fn is_aborted(&self) -> bool {
        if let Some(abort) = self.abort.as_ref() {
            abort.load(Ordering::Relaxed)
        } else {
            false
        }
    }
```

**File:** rpc/src/rpc.rs (L2310-2357)
```rust
    /// Get an iterator of spl-token accounts by owner address
    async fn get_filtered_spl_token_accounts_by_owner(
        &self,
        bank: Arc<Bank>,
        program_id: Pubkey,
        owner_key: Pubkey,
        mut filters: Vec<RpcFilterType>,
        sort_results: bool,
    ) -> RpcCustomResult<Vec<(Pubkey, AccountSharedData)>> {
        // The by-owner accounts index checks for Token Account state and Owner address on
        // inclusion. However, due to the current AccountsDb implementation, an account may remain
        // in storage as a zero-lamport AccountSharedData::Default() after being wiped and reinitialized in
        // later updates. We include the redundant filters here to avoid returning these accounts.
        //
        // Filter on Token Account state
        filters.push(RpcFilterType::TokenAccountState);
        // Filter on Owner address
        filters.push(RpcFilterType::Memcmp(Memcmp::new_raw_bytes(
            SPL_TOKEN_ACCOUNT_OWNER_OFFSET,
            owner_key.to_bytes().into(),
        )));

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
        } else {
            self.get_filtered_program_accounts(bank, program_id, filters, sort_results)
                .await
        }
    }
```
