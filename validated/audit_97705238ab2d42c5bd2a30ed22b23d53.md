### Title
`getTokenLargestAccounts` RPC materializes full unbounded account set before top-K reduction, enabling attacker-driven memory exhaustion - ([File: rpc/src/rpc.rs])

### Summary
`JsonRpcRequestProcessor::get_token_largest_accounts` awaits `get_filtered_spl_token_accounts_by_mint`, which fully collects **all** matching accounts for a mint into a `Vec<(Pubkey, AccountSharedData)>` before the caller reduces them into a bounded `BinaryHeap` of size `NUM_LARGEST_ACCOUNTS`. An attacker who creates a very large number of ordinary spl-token accounts for a single mint (unprivileged, standard account creation) can cause a single `getTokenLargestAccounts` call to allocate memory proportional to the total number of matching accounts rather than the fixed output size.

### Finding Description
`get_token_largest_accounts` (`rpc/src/rpc.rs:2076-2130`) calls:
```rust
for (address, account) in self
    .get_filtered_spl_token_accounts_by_mint(Arc::clone(&bank), mint_owner, mint, vec![], true)
    .await?
{ ... }
``` [1](#0-0) 

`get_filtered_spl_token_accounts_by_mint` (`rpc/src/rpc.rs:2360-2405`) routes to either `get_filtered_indexed_accounts` (if a secondary `SplTokenMint` index is configured) or `get_filtered_program_accounts` (otherwise, or the fallback branch), both of which return `RpcCustomResult<Vec<(Pubkey, AccountSharedData)>>` — a fully-materialized `Vec` of every matching account, with account data included. [2](#0-1) 

Only after this full-scan `Vec` is returned does the caller iterate and reduce it into a `BinaryHeap::<Reverse<(u64, Pubkey)>>::with_capacity(NUM_LARGEST_ACCOUNTS)`: [3](#0-2) 

By contrast, the analogous native-account "largest accounts" scan, `AccountsDb::load_largest_accounts` in `accounts-db/src/accounts.rs`, performs the top-K reduction incrementally *inside* the scan callback — pushing/popping the heap for each account as it's visited, never materializing an intermediate `Vec` of all matches: [4](#0-3) 

This asymmetry means `getTokenLargestAccounts`'s underlying account-count/data scan is unbounded by output size: any unprivileged actor can create ordinary (non-privileged) SPL token accounts owned by themselves for a chosen mint — e.g., minting many small accounts to many different owner pubkeys, or simply many token accounts they control — and none of RPC's account-scan filters cap the *total* number of accounts matched before the top-K bound is applied. There is no limit on `filters=vec![]` account count returned by `get_filtered_program_accounts`/`get_filtered_indexed_accounts` analogous to `MAX_GET_PROGRAM_ACCOUNT_FILTERS`, which only bounds filter list length, not result set size. [5](#0-4) 

### Impact Explanation
A single `getTokenLargestAccounts` call against a mint with a very large number of matching token accounts forces the RPC node to allocate a `Vec` sized to the entire scan result (pubkeys + full account data), rather than bounded by `NUM_LARGEST_ACCOUNTS` output entries. With enough matching accounts, this can exhaust RPC-node memory or cause severe latency spikes on that single request, matching the "RPC crash / non-consensus resource exhaustion" bounty category. This is scoped to the RPC-serving process (not consensus/replay) but is a real RPC-liveness bug: response memory is not bounded by output size as it should be for a "top-K" endpoint.

### Likelihood Explanation
Fully attacker-reachable with no special privileges: creating spl-token accounts for a mint is ordinary permissionless account creation (rent-paying `CreateAccount` + `InitializeAccount`), and the RPC call itself requires only public RPC access. It is a single-call trigger (not "repeated calls, multiple clients"), satisfying the audit's requirement that RPC issues need repeated calls to be excluded — this is one call, one victim node. Feasibility scales with the attacker's willingness to fund rent for many token accounts (a moderate but bounded cost, e.g. via the minimum-rent-exempt balance per account), and is fully repeatable against any public RPC node with `getTokenLargestAccounts` enabled and account indexing configured or not (both code paths return an unbounded `Vec`).

### Recommendation
Refactor `get_filtered_spl_token_accounts_by_mint`/`get_filtered_program_accounts`/`get_filtered_indexed_accounts` (or add a dedicated scan path for `get_token_largest_accounts`) to perform the `BinaryHeap` top-K reduction incrementally inside the account-scan callback — mirroring `AccountsDb::load_largest_accounts` — instead of collecting the full matching set into a `Vec` first. Alternatively, impose a maximum result-set size for unindexed/indexed mint-filtered scans used specifically by `get_token_largest_accounts`, or push the top-K logic down into the accounts-db scan layer for this specific call.

### Proof of Concept
```rust
// rpc/src/rpc_full_bench.rs (conceptual)
// 1. Create N (e.g. 1_000_000) spl-token accounts for a single mint via ordinary
//    CreateAccount + InitializeAccount instructions, each funded by attacker keypair(s),
//    each with a distinct pubkey, all owned by spl-token program, all referencing `mint`.
// 2. Issue a single JSON-RPC request:
//    { "jsonrpc":"2.0","id":1,"method":"getTokenLargestAccounts","params":["<mint>"] }
// 3. Instrument get_filtered_spl_token_accounts_by_mint to assert/measure the returned
//    Vec<(Pubkey, AccountSharedData)>.len() == N (not NUM_LARGEST_ACCOUNTS == 20),
//    demonstrating the intermediate allocation scales with N, not with the fixed
//    output size returned to the client.
```
Because the `Vec` returned from `get_filtered_spl_token_accounts_by_mint` at `rpc/src/rpc.rs:2091-2099` has size equal to the number of matching accounts (verified via code read, not merely output count), the memory cost of a single `getTokenLargestAccounts` call is `O(N)` in the number of attacker-created matching accounts rather than `O(NUM_LARGEST_ACCOUNTS)`.

### Citations

**File:** rpc/src/rpc.rs (L2089-2116)
```rust
        let mut token_balances =
            BinaryHeap::<Reverse<(u64, Pubkey)>>::with_capacity(NUM_LARGEST_ACCOUNTS);
        for (address, account) in self
            .get_filtered_spl_token_accounts_by_mint(
                Arc::clone(&bank),
                mint_owner,
                mint,
                vec![],
                true,
            )
            .await?
        {
            let amount = StateWithExtensions::<TokenAccount>::unpack(account.data())
                .map(|account| account.base.amount)
                .unwrap_or(0);

            let new_entry = (amount, address);
            if token_balances.len() >= NUM_LARGEST_ACCOUNTS {
                let Reverse(entry) = token_balances
                    .peek()
                    .expect("BinaryHeap::peek should succeed when len > 0");
                if *entry >= new_entry {
                    continue;
                }
                token_balances.pop();
            }
            token_balances.push(Reverse(new_entry));
        }
```

**File:** rpc/src/rpc.rs (L2359-2405)
```rust
    /// Get an iterator of spl-token accounts by mint address
    async fn get_filtered_spl_token_accounts_by_mint(
        &self,
        bank: Arc<Bank>,
        program_id: Pubkey,
        mint_key: Pubkey,
        mut filters: Vec<RpcFilterType>,
        sort_results: bool,
    ) -> RpcCustomResult<Vec<(Pubkey, AccountSharedData)>> {
        // The by-mint accounts index checks for Token Account state and Mint address on inclusion.
        // However, due to the current AccountsDb implementation, an account may remain in storage
        // as be zero-lamport AccountSharedData::Default() after being wiped and reinitialized in later
        // updates. We include the redundant filters here to avoid returning these accounts.
        //
        // Filter on Token Account state
        filters.push(RpcFilterType::TokenAccountState);
        // Filter on Mint address
        filters.push(RpcFilterType::Memcmp(Memcmp::new_raw_bytes(
            SPL_TOKEN_ACCOUNT_MINT_OFFSET,
            mint_key.to_bytes().into(),
        )));
        if self
            .config
            .account_indexes
            .contains(&AccountIndex::SplTokenMint)
        {
            if !self.config.account_indexes.include_key(&mint_key) {
                return Err(RpcCustomError::KeyExcludedFromSecondaryIndex {
                    index_key: mint_key.to_string(),
                });
            }
            self.get_filtered_indexed_accounts(
                &bank,
                &IndexKey::SplTokenMint(mint_key),
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

**File:** rpc/src/rpc.rs (L2471-2481)
```rust
pub(crate) fn verify_filters(filters: &[RpcFilterType]) -> Result<()> {
    if filters.len() > MAX_GET_PROGRAM_ACCOUNT_FILTERS {
        return Err(Error::invalid_params(format!(
            "Too many filters provided; max {MAX_GET_PROGRAM_ACCOUNT_FILTERS}"
        )));
    }
    for filter in filters {
        verify_filter(filter)?;
    }
    Ok(())
}
```

**File:** accounts-db/src/accounts.rs (L266-301)
```rust
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
        Ok(account_balances
            .into_sorted_vec()
            .into_iter()
            .map(|Reverse((balance, pubkey))| (pubkey, balance))
            .collect())
```
