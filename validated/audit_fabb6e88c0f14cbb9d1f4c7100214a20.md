### Title
Unbounded growth of the SPL-token secondary index inflates `getProgramAccounts`/`getTokenAccountsByOwner` scans, enabling cheap RPC-side DoS - (File: accounts-db/src/accounts_index/secondary.rs, accounts-db/src/accounts_db.rs)

### Summary
The Canto finding shows how an attacker can cheaply grow an array (`tickTracking_`) that a shared, unbounded-cost loop must fully traverse on every subsequent mint/burn/harvest, turning a per-account bookkeeping structure into a DoS vector for all users. Agave has a structurally analogous pattern in the accounts-db secondary index used to serve `getProgramAccounts`/`getTokenAccountsByOwner(Mint)`: the `SplTokenOwner`/`SplTokenMint`/`ProgramId` secondary index (`SecondaryIndex<RwLockSecondaryIndexEntry>`) stores, per key, an ever-growing `HashSet<Pubkey>` of accounts, and any RPC scan by that key iterates and loads every entry one at a time with no cap, while holding index/scan locks that block cleaning.

### Finding Description
`AccountsIndex` maintains three secondary indexes (`program_id_index`, `spl_token_mint_index`, `spl_token_owner_index`), each a `SecondaryIndex<RwLockSecondaryIndexEntry>` backed by a `DashMap<Pubkey, RwLockSecondaryIndexEntry>` whose value is an unbounded `HashSet<Pubkey>` of "inner" account keys [1](#0-0) . Every time an SPL token account is created or its owner/mint data is written, `update_spl_token_secondary_indexes` calls `insert`, unconditionally growing the set for that owner/mint key with no upper bound and no fee proportional to the resulting scan cost [2](#0-1) . Creating an SPL token account is cheap (one rent-exempt account creation, parallelizable across many transactions/signers), directly analogous to the "extremely small swaps" used to cheaply inflate `tickTracking_`.

When any client queries by that index key (e.g. `getTokenAccountsByOwner`, `getProgramAccounts` filtered on owner/mint/program), `index_scan_accounts` retrieves the *entire* key list via `get_index_key_pubkeys` and then serially calls `do_load` for every single pubkey, with no cap on the number of entries processed: [3](#0-2) 
This scan is registered under a `ScanGuard` that pins `max_root()`/ancestors and therefore blocks `clean_accounts` from reclaiming those slots while the scan is in flight [4](#0-3) . The RPC entry point `get_filtered_indexed_accounts` (invoked from `get_filtered_program_accounts` / `get_filtered_spl_token_accounts_by_owner` / `by_mint`) forwards straight into this path with no limit on the number of matched inner keys before the abort/config check is consulted [5](#0-4) .

Because the index is a global `DashMap` shared by all banks/threads on the validator, and the scan holds a `ScanGuard` that participates in root/clean scheduling, one attacker can (a) cheaply balloon the reverse-index entry for a pubkey they control by creating a very large number of token accounts owned by (or minted by) that key, then (b) trigger (or have any third party unknowingly trigger, since `getTokenAccountsByOwner` is a standard public RPC method) a scan that must walk the entire unbounded list doing per-pubkey index+storage loads while pinning `max_root()` against cleaning — degrading or effectively hanging the RPC node for that request and blocking the accounts-background cleaning service for the pinned slot range, exactly mirroring the Canto pattern of "cheap unbounded array growth by an attacker forces a full, unbounded iteration in a shared critical operation."

### Impact Explanation
This is scoped to RPC nodes with secondary indexes enabled (`--account-index spl-token-owner`/`spl-token-mint`/`program-id`), which is a supported, common configuration for RPC service providers. An attacker spending only normal rent/account-creation costs can force an RPC node into long, unbounded, single-threaded, lock-holding scans (`do_load` per entry, no batching/limit), causing:
- RPC request-handling stalls/timeouts for the targeted key, consuming worker/blocking-thread-pool capacity that other RPC clients depend on.
- Prolonged `ScanGuard` pinning of `max_root()`, delaying `clean_accounts`/`AccountsBackgroundService`, which increases account storage bloat and index memory pressure network-wide for that node.

This does not directly corrupt consensus state or permit unauthorized fund movement, but it is a concrete ingest/RPC-availability DoS reachable purely from ordinary, unprivileged transactions (account creations) plus a standard RPC call, matching the "RPC crash / ingest starvation" acceptance criteria.

### Likelihood Explanation
High for any RPC operator running secondary indexes: creating large numbers of SPL token accounts under one owner/mint is inexpensive and requires no special privilege, and the vulnerable scan path (`index_scan_accounts` → sequential `do_load`) has no cap on the number of index entries it will process per request. The only mitigating factor is that secondary indexes are opt-in (not enabled on validators that don't index by owner/mint/program), which limits blast radius to nodes that have chosen this configuration — a substantial fraction of public RPC infrastructure.

### Recommendation
- Cap the number of inner keys processed per secondary-index scan (e.g., enforce a hard maximum matching `getProgramAccounts`/`getTokenAccountsByOwner` result-size limits) and reject/paginate requests whose secondary-index entry exceeds that bound before doing any `do_load` calls.
- Track and periodically log/alert on secondary index entries whose `len()` grows anomalously large (the existing `log_secondary_indexes`/`log_contents` top-20 reporting exists but is not enforced) [6](#0-5) .
- Consider batching/parallelizing `do_load` in `index_scan_accounts` and releasing the `ScanGuard`/root pin incrementally, so a single oversized key cannot indefinitely block `clean_accounts`.
- Optionally rate-limit or charge additional RPC-level cost for scans whose matched-key count exceeds a threshold.

### Proof of Concept
1. Run a validator/RPC node with `--account-index spl-token-owner` enabled.
2. As an ordinary user, submit a large number of `create_account` + `InitializeAccount` (SPL Token) instructions, all setting the same `owner` pubkey (e.g., 200,000 token accounts owned by attacker-controlled key `K`). Each creation is a normal, cheap, unprivileged transaction; `update_spl_token_secondary_indexes` inserts each new account pubkey into `spl_token_owner_index`'s entry for `K` with no bound [7](#0-6) .
3. Call the public RPC method `getTokenAccountsByOwner(K, ...)` (or `getProgramAccounts` filtered by owner). This routes to `get_filtered_spl_token_accounts_by_owner` → `get_filtered_indexed_accounts` → `index_scan_accounts`, which fetches all ~200,000 pubkeys via `get_index_key_pubkeys` and calls `do_load` sequentially for each one while a `ScanGuard` pins the root [3](#0-2) .
4. Observe that the request takes disproportionately long (roughly linear in attacker-controlled entry count, each entry requiring an index lookup + potential disk/append-vec read), consuming the RPC worker for that duration and delaying background `clean_accounts` for the pinned max-root range — a DoS reachable purely through normal token-account creation and a standard RPC call.

### Citations

**File:** accounts-db/src/accounts_index.rs (L192-227)
```rust
pub struct AccountsIndex<T: IndexValue, U: DiskIndexValue + From<T> + Into<T>> {
    pub account_maps: Box<[Arc<InMemAccountsIndex<T, U>>]>,
    pub bin_calculator: PubkeyBinCalculator,
    program_id_index: SecondaryIndex<RwLockSecondaryIndexEntry>,
    spl_token_mint_index: SecondaryIndex<RwLockSecondaryIndexEntry>,
    spl_token_owner_index: SecondaryIndex<RwLockSecondaryIndexEntry>,

    storage: AccountsIndexStorage<T, U>,

    pub purge_older_root_entries_one_slot_list: AtomicUsize,
}

impl<T: IndexValue, U: DiskIndexValue + From<T> + Into<T>> AccountsIndex<T, U> {
    pub fn default_for_tests() -> Self {
        Self::new(&ACCOUNTS_INDEX_CONFIG_FOR_TESTING, Arc::default())
    }

    pub fn new(config: &AccountsIndexConfig, exit: Arc<AtomicBool>) -> Self {
        let (account_maps, bin_calculator, storage) = Self::allocate_accounts_index(config, exit);
        info!("AccountsIndex bin calculator: {bin_calculator:?}");
        Self {
            purge_older_root_entries_one_slot_list: AtomicUsize::default(),
            account_maps,
            bin_calculator,
            program_id_index: SecondaryIndex::<RwLockSecondaryIndexEntry>::new(
                "program_id_index_stats",
            ),
            spl_token_mint_index: SecondaryIndex::<RwLockSecondaryIndexEntry>::new(
                "spl_token_mint_index_stats",
            ),
            spl_token_owner_index: SecondaryIndex::<RwLockSecondaryIndexEntry>::new(
                "spl_token_owner_index_stats",
            ),
            storage,
        }
    }
```

**File:** accounts-db/src/accounts_index.rs (L557-580)
```rust
    fn update_spl_token_secondary_indexes<G: spl_generic_token::token::GenericTokenAccount>(
        &self,
        token_id: &Pubkey,
        pubkey: &Pubkey,
        account_owner: &Pubkey,
        account_data: &[u8],
        account_indexes: &AccountSecondaryIndexes,
    ) {
        if *account_owner == *token_id {
            if account_indexes.contains(&AccountIndex::SplTokenOwner)
                && let Some(owner_key) = G::unpack_account_owner(account_data)
                && account_indexes.include_key(owner_key)
            {
                self.spl_token_owner_index.insert(owner_key, pubkey);
            }

            if account_indexes.contains(&AccountIndex::SplTokenMint)
                && let Some(mint_key) = G::unpack_account_mint(account_data)
                && account_indexes.include_key(mint_key)
            {
                self.spl_token_mint_index.insert(mint_key, pubkey);
            }
        }
    }
```

**File:** accounts-db/src/accounts_db.rs (L3381-3396)
```rust
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

**File:** accounts-db/src/accounts_index/secondary.rs (L260-273)
```rust
    /// log top 20 (owner, # accounts) in descending order of # accounts
    pub fn log_contents(&self) {
        let mut entries = self
            .index
            .iter()
            .map(|entry| (entry.value().len(), *entry.key()))
            .collect::<Vec<_>>();
        entries.sort_unstable();
        entries
            .iter()
            .rev()
            .take(20)
            .for_each(|(v, k)| info!("owner: {k}, accounts: {v}"));
    }
```
