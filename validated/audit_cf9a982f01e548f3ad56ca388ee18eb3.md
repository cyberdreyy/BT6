Found a concrete analog in the `getProgramAccounts` RPC path: when the account-index feature (`AccountIndex::ProgramId`) is not enabled — which is the common case since secondary indexes are opt-in and memory-expensive — the accounts scan used to serve the request enforces no size limit at all, unlike the indexed path which does.

### Title
Unbounded `getProgramAccounts` RPC scan enables cheap attacker-grown state to trigger RPC node memory exhaustion - ([File: rpc/src/rpc.rs])

### Summary
`getProgramAccounts` requests that hit the non-indexed scan path (`Accounts::load_by_program_with_filter`) collect every matching account into an in-memory `Vec` with no byte-size cap, while the indexed path (`load_by_index_key_with_filter`) explicitly enforces `scan_results_limit_bytes`. An ordinary, unprivileged user can cheaply create a large number of accounts owned by any given program (paying only the refundable rent-exempt minimum) and then issue a single `getProgramAccounts` call for that program, forcing the RPC node to buffer an attacker-controlled, effectively unbounded amount of account data in memory in one request.

### Finding Description
`get_filtered_program_accounts` in the RPC layer branches on whether a secondary `AccountIndex::ProgramId` index is configured: [1](#0-0) 

When the index is *not* configured (the default/common configuration, since maintaining secondary indexes is memory-intensive and not enabled by default), the code takes the `else` branch and explicitly documents that no byte limit is applied: "this path does not need to provide a mb limit because we only want to support secondary indexes" — and calls `bank.get_filtered_program_accounts` with no size constraint whatsoever.

This bottoms out in `Accounts::load_by_program_with_filter`, which collects into an unbounded `Vec` via `scan_accounts` with a plain `ScanConfig::default()` (no abort/size tracking): [2](#0-1) 

By contrast, the indexed path (`load_by_index_key_with_filter`) is deliberately size-guarded: it uses `ScanConfig::default().recreate_with_abort()` and calls `accumulate_and_check_scan_result_size` against a caller-supplied `byte_limit_for_scan`, aborting the scan once the accumulated result size crosses the limit: [3](#0-2) 

That limit is only threaded through the indexed RPC path (`get_filtered_indexed_accounts`, using `self.config.scan_results_limit_bytes`): [4](#0-3) 

The asymmetry mirrors the VUSD bug class: an attacker can permissionlessly grow on-chain state that is cheap for them to create (rent is refundable, unlike gas) but expensive for a privileged/infrastructure component — here, the RPC node — to fully materialize in memory when it later has to service a legitimate, unprivileged read request against that same program ID.

### Impact Explanation
A user can create an arbitrarily large number of accounts owned by a widely-used program (e.g., System Program or any popular on-chain program without a mint/owner-scoped secondary index) and then send a single `getProgramAccounts` RPC request for that program. Because the non-indexed scan path has no `byte_limit_for_scan`, the RPC node will attempt to load and buffer all matching accounts' full data into memory in one response, which can exhaust the node's memory and crash the RPC process (denial of service against RPC infrastructure), affecting all other clients relying on that node.

### Likelihood Explanation
Likelihood is moderate-to-high: `AccountIndex::ProgramId` secondary indexing is not universally enabled (it is costly to maintain), so many RPC deployments run the unguarded path by default. Creating many rent-exempt accounts owned by a common program is inexpensive and fully permissionless — it requires no special privilege, just standard `CreateAccount` transactions.

### Recommendation
Thread a `byte_limit_for_scan`/`scan_results_limit_bytes` cap into the non-indexed `get_filtered_program_accounts` / `load_by_program_with_filter` path the same way it is enforced for `load_by_index_key_with_filter`, aborting the scan and returning an error once the accumulated result size exceeds a configurable threshold, regardless of whether a secondary index is present.

### Proof of Concept
1. Submit many `CreateAccount` transactions assigning a large number of new accounts (each holding non-trivial `data_len`) to a program ID that is not covered by a `ProgramId` secondary index on the target RPC node.
2. Call `getProgramAccounts` for that program ID against the RPC node.
3. Observe the RPC node's `get_filtered_program_accounts` → `Accounts::load_by_program_with_filter` path collecting all matching accounts without any size limit check (contrast with `accumulate_and_check_scan_result_size` used in the indexed path), leading to unbounded memory growth for the single request and potential OOM/crash of the RPC process.

### Citations

**File:** rpc/src/rpc.rs (L309-347)
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
        if sort_results {
            // Avoid copying pubkeys (using Ord::cmp(a, b) silences clippy::unnecessary_sort_by).
            accounts.sort_unstable_by(|(addr_a, _), (addr_b, _)| Ord::cmp(addr_a, addr_b));
        }
        Ok(accounts)
    }
```

**File:** rpc/src/rpc.rs (L2260-2307)
```rust
    ) -> RpcCustomResult<Vec<(Pubkey, AccountSharedData)>> {
        optimize_filters(&mut filters);
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
                message: e.to_string(),
            })
        } else {
            // this path does not need to provide a mb limit because we only want to support secondary indexes
            let mut accounts = self
                .runtime
                .spawn_blocking(move || {
                    bank.get_filtered_program_accounts(
                        &program_id,
                        |account: &AccountSharedData| {
                            filters
                                .iter()
                                .all(|filter_type| filter_allows(filter_type, account))
                        },
                    )
                    .map_err(|e| RpcCustomError::ScanError {
                        message: e.to_string(),
                    })
                })
                .await
                .expect("Failed to spawn blocking task")?;
            if sort_results {
                // Avoid copying pubkeys (using Ord::cmp(a, b) silences clippy::unnecessary_sort_by).
                accounts.sort_unstable_by(|(addr_a, _), (addr_b, _)| Ord::cmp(addr_a, addr_b));
            }
            Ok(accounts)
        }
```

**File:** accounts-db/src/accounts.rs (L338-358)
```rust
    pub fn load_by_program_with_filter<F: Fn(&AccountSharedData) -> bool>(
        &self,
        ancestors: &Ancestors,
        bank_id: BankId,
        program_id: &Pubkey,
        filter: F,
    ) -> ScanResult<Vec<KeyedAccountSharedData>> {
        let mut collector = Vec::new();
        self.accounts_db
            .scan_accounts(
                ancestors,
                bank_id,
                |some_account_tuple| {
                    Self::load_while_filtering(&mut collector, some_account_tuple, |account| {
                        account.owner() == program_id && filter(account)
                    })
                },
                &ScanConfig::default(),
            )
            .map(|_| collector)
    }
```

**File:** accounts-db/src/accounts.rs (L360-434)
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
