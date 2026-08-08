### Title
Unbounded per-key secondary-index scan in `getTokenAccountsByOwner`/`getTokenAccountsByDelegate`/`getTokenLargestAccounts` allows a single low-rate RPC call to consume unbounded CPU/gas-equivalent - ([File: accounts-db/src/accounts_db.rs])

### Summary
The reported bug class is a scan whose cost scales with the number of entities associated with a single key (players in a cell), with no cap, invoked from a single external call, causing the callback to run out of gas and get the whole state machine stuck. The closest reachable analog in `agave` is the RPC secondary-index scan path used by `getTokenAccountsByOwner`, `getTokenAccountsByDelegate`, and `getTokenLargestAccounts`: when a secondary index is enabled for the requested key, `index_scan_accounts` iterates **every** pubkey registered under that index key and performs a full account load for each one, with no limit, pagination, or cost cap, all from a single JSON-RPC request.

### Finding Description
`get_token_accounts_by_owner` and `get_token_accounts_by_delegate` in `rpc/src/rpc.rs` route into `get_filtered_spl_token_accounts_by_owner`/`get_filtered_spl_token_accounts_by_mint`, which — when the corresponding `AccountIndex` (`SplTokenOwner`/`SplTokenMint`) is enabled — call `get_filtered_indexed_accounts`, which ultimately calls into `AccountsDb::index_scan_accounts`: [1](#0-0) [2](#0-1) 

```rust
for pubkey in self.accounts_index.get_index_key_pubkeys(&index_key) {
    if config.is_aborted() { break; }
    if let Some((account, slot)) = self.do_load(...) {
        scan_func(Some((&pubkey, account, slot)));
    }
}
``` [3](#0-2) 

This loop has no upper bound: it iterates every pubkey ever indexed under the requested owner/mint/delegate/program key and performs a `do_load` (a full account-data read from `AccountsDb`) for each one. `getTokenLargestAccounts` similarly funnels through `get_filtered_spl_token_accounts_by_mint`, unpacking every returned token account to compare balances: [4](#0-3) 

Unlike the `_checkSolePlayer()` case, this is not fully out of scope, because the rule only excludes *unfiltered* `getProgramAccounts` calls without a secondary index. Here, the vulnerable path is specifically the *indexed* branch, which is reachable with a single request (no need for repeated calls or multiple clients), and the amount of work is proportional to however many accounts have accumulated under one key over the life of the chain (e.g., a popular mint's set of token accounts, or a delegate/owner key reused across many wallets) — directly analogous to the unbounded per-key player list in the reported bug.

### Impact Explanation
An RPC node with `--account-index spl-token-owner`/`spl-token-mint` enabled can be driven into large, uncontrolled CPU/memory work by a single JSON-RPC call targeting a key with a very large index fan-out (e.g., a mint or delegate address with hundreds of thousands to millions of associated accounts). Because there is no limit on the number of pubkeys pulled from `get_index_key_pubkeys`, nor a cap on the number of `do_load` calls performed, a single call can hold the scan-lock/tracker for an extended period and consume proportional CPU and I/O, degrading or effectively stalling the RPC service for that node — a single-call, unbounded-cost condition, mirroring the external report's core defect (per-key state scanned without bound, triggered by one call).

### Likelihood Explanation
This requires an RPC node to have the relevant secondary index enabled (this is an opt-in operator configuration, common at some API providers to make these RPC methods usable at all), and requires an index key with a large number of associated accounts to exist on-chain — both realistic conditions for public RPC endpoints serving mainstream SPL tokens. No special privileges are needed to invoke `getTokenAccountsByOwner`, `getTokenAccountsByDelegate`, or `getTokenLargestAccounts`; this is a standard unprivileged JSON-RPC call.

### Recommendation
- Add a hard limit on the number of pubkeys read out of `get_index_key_pubkeys`/processed per index-scan call, returning a `RpcCustomError` (similar to `KeyExcludedFromSecondaryIndex`) when the index-key cardinality exceeds a configurable threshold.
- Consider pagination/streaming for `getTokenAccountsByOwner`/`ByDelegate` and `getTokenLargestAccounts` so a single call cannot force an unbounded amount of `do_load` work.
- Track and enforce a byte/account-count budget for the whole request similar to bounded-batch RPC methods (e.g., `MAX_MULTIPLE_ACCOUNTS` used for `getMultipleAccounts`).

### Proof of Concept
Not independently reproduced against a live cluster (no filesystem/terminal access in this session); the finding is based on static analysis of the code paths cited above. To validate: run an RPC node with `--account-index spl-token-mint` enabled, seed the local ledger with a very large number of token accounts for a single mint, and issue a single `getTokenLargestAccounts`/`getTokenAccountsByOwner` call for that mint; measure whether request latency/CPU scales linearly (unbounded) with the number of indexed accounts and can be pushed to degrade the node with one request.

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
