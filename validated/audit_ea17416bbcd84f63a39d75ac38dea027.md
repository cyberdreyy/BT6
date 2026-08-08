### Title
Secondary-index `getProgramAccounts` scan cost scales with total accounts under a program, not with filter selectivity or an explicit request bound - (File: `accounts-db/src/accounts.rs`)

### Summary
`load_by_index_key_with_filter` only bounds the *accumulated size of matched results* (`byte_limit_for_scan`) via `accumulate_and_check_scan_result_size`, but the underlying `index_scan_accounts` call in `accounts-db/src/accounts_db.rs` iterates and performs a full `do_load` for *every* pubkey registered under the `AccountIndex::ProgramId` (or SPL mint/owner) secondary index before the caller's filter (including a selective `Memcmp`) is even evaluated. An attacker who can write on-chain accounts owned by a single, non-preloaded program can inflate that program's index bucket to size N, then issue one `getProgramAccounts` call with a highly selective filter; the RPC still walks and loads all N accounts, so per-call CPU/memory cost is proportional to N with no independent ceiling.

### Finding Description
The RPC entrypoint `get_filtered_program_accounts` in `rpc/src/rpc.rs` routes to `get_filtered_indexed_accounts` (`rpc/src/rpc.rs:309-347`) when `AccountIndex::ProgramId` is enabled, which calls `bank.get_filtered_indexed_accounts` (`runtime/src/bank.rs:5134-5147`) → `Accounts::load_by_index_key_with_filter` (`accounts-db/src/accounts.rs:396-433`).

`load_by_index_key_with_filter` delegates iteration to `AccountsDb::index_scan_accounts` (`accounts-db/src/accounts_db.rs:3358-3421`), which does:
```
for pubkey in self.accounts_index.get_index_key_pubkeys(&index_key) {
    if config.is_aborted() { break; }
    if let Some((account, slot)) = self.do_load(...) {
        scan_func(Some((&pubkey, account, slot)));
    }
}
``` [1](#0-0) 

This loop performs a full storage load (`do_load`) for *every* pubkey in the program's index bucket, unconditionally, before the caller-supplied filter runs inside `scan_func`. The filter and the byte-size accounting only run on the result of the load, inside `load_while_filtering`/`accumulate_and_check_scan_result_size`: [2](#0-1) 

The only abort mechanism, `config.abort()`, is triggered solely by `accumulate_and_check_scan_result_size` when the running total of *matched* (post-filter) results' byte size exceeds `byte_limit_for_scan` (the `scan_results_limit_bytes` / `--accounts-index-scan-results-limit-mb` config, which defaults to `None`, i.e., disabled): [3](#0-2) 

If the attacker's `Memcmp` filter is selective enough that few or no of the N accounts match, `use_account` is false for almost every account, so `accumulate_and_check_scan_result_size` never accumulates enough matched bytes to hit the limit and `config.abort()` is never called. `config.is_aborted()` is therefore never true, and the loop in `index_scan_accounts` runs to completion over all N pubkeys, performing N `do_load` calls (each a storage read plus deserialization) regardless of filter selectivity. This confirms the invariant violation: the enforced ceiling (`scan_results_limit_bytes`) bounds *matched output size*, not *scan input size*, so cost is unbounded in N for a selective filter, and by default (`scan_results_limit_bytes = None`) there is no ceiling of any kind.

An unstaked attacker can grow N arbitrarily by creating accounts owned by any single program ID (does not need to be a real, widely-used program — any owner pubkey qualifies, since the index is keyed by owner) via ordinary account-creation transactions, which requires no special privilege, staking, or leader/validator control — only standard write access to create accounts, consistent with the stated attacker model.

### Impact Explanation
This matches the Agave bounty's "RPC DoS with a single low-rate call" category, scoped specifically to the secondary-index `getProgramAccounts` case (explicitly in-scope per the question, as opposed to unfiltered `getProgramAccounts` without secondary indexes, which is out of scope). A single call causes CPU (N account loads/deserializations) and I/O proportional to N, which the attacker fully controls by pre-populating accounts under a chosen owner. Because `scan_results_limit_bytes` defaults to unset and even when set only limits matched-result bytes (not scan-loop iterations), there is no explicit, output-independent ceiling on the cost of a single RPC call, contrary to the intended per-request scan bound.

### Likelihood Explanation
Feasible and repeatable with only unprivileged capabilities: any client can (a) submit ordinary transactions creating N accounts owned by an attacker-chosen program/pubkey, and (b) issue a single `getProgramAccounts` call with `filters` containing a selective `Memcmp` targeting a byte pattern unlikely to match any of the N accounts. This requires no validator/leader control, no operator misconfiguration beyond the default (index enabled, default `scan_results_limit_bytes = None`), and no more than one RPC call per attempt — satisfying the "single call" constraint in the rules. Repeating with larger N linearly increases per-call cost, demonstrating unbounded scaling.

### Recommendation
Enforce a ceiling on the *number of index-scan iterations / do_load operations* performed per call (e.g., a hard cap on candidate pubkeys pulled from `get_index_key_pubkeys`, or check `config.is_aborted()`/an iteration counter against a configurable max scan count independent of matched-result size) in `AccountsDb::index_scan_accounts` (`accounts-db/src/accounts_db.rs:3398-3410`), so cost is bounded before filtering rather than only after accumulating matched bytes. Additionally, make `scan_results_limit_bytes` (and/or an added scan-iteration limit) enabled by default rather than `None`.

### Proof of Concept
Rust integration test outline (in `runtime/src/bank/tests.rs`, extending the existing `test_get_filtered_indexed_accounts_limit_exceeded`/`test_get_filtered_indexed_accounts` harness at `runtime/src/bank/tests.rs:3470-3571`):
```rust
#[test]
fn test_index_scan_cost_scales_with_program_account_count_despite_selective_filter() {
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
        &genesis_config, Some(bank_config), vec![], None,
    ));

    let program_id = Pubkey::new_unique();
    const N: usize = 200_000; // attacker-controlled, unbounded
    for _ in 0..N {
        let pubkey = Pubkey::new_unique();
        let account = AccountSharedData::new(1, 128, &program_id);
        bank.store_account(&pubkey, &account);
    }

    // Highly selective filter that matches nothing (simulating Memcmp on a
    // pattern no account has), no byte_limit_for_scan set (default None).
    let start = std::time::Instant::now();
    let result = bank
        .get_filtered_indexed_accounts(
            &IndexKey::ProgramId(program_id),
            |account| account.data().starts_with(&[0xDE, 0xAD, 0xBE, 0xEF]), // never matches
            None,
        )
        .unwrap();
    let elapsed = start.elapsed();

    assert!(result.is_empty(), "selective filter matched nothing as intended");
    // Expected failing assertion demonstrating the bug: latency/CPU work should be
    // bounded independent of N, but scales linearly with N because index_scan_accounts
    // performs N `do_load` calls before the filter runs.
    assert!(
        elapsed.as_millis() < 50,
        "scan cost scaled with N ({N} accounts) despite selective filter: {elapsed:?}"
    );
}
```
Run this with increasing `N` (e.g., 10_000, 100_000, 500_000) and assert wall-clock time / instrumented `do_load` call count grows roughly linearly with N, confirming the absence of a ceiling independent of on-chain data size for the program owner index.

### Citations

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

**File:** accounts-db/src/accounts.rs (L366-381)
```rust
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
```

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
