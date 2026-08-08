### Title
Unbounded per-key secondary-index scan in `getTokenAccountsByOwner`/`getTokenAccountsByDelegate` allows single-call, attacker-inflatable compute DoS - (File: `accounts-db/src/accounts_db.rs`, `accounts-db/src/accounts.rs`, `rpc/src/rpc.rs`)

### Summary
The Quest-protocol bug is a class of "unbounded enumeration keyed by an externally-inflatable ownership index causes unbounded work in a single call." The Agave analog is the SPL-token secondary index used by `getTokenAccountsByOwner`/`getTokenAccountsByDelegate`: the number of entries scanned for a given owner key is controlled entirely by how many SPL token accounts anyone chooses to create with that owner's pubkey written into the account data, and the scan's only cost-limiting mechanism (a byte-size abort) is applied only to accounts that pass the caller's filter — not to the total number of index entries that must be loaded to evaluate that filter.

### Finding Description
When the `SplTokenOwner` secondary index is enabled, `get_filtered_spl_token_accounts_by_owner` routes the lookup to `get_filtered_indexed_accounts`, which calls `load_by_index_key_with_filter`: [1](#0-0) [2](#0-1) 

That function delegates to `index_scan_accounts`, which pulls **every** pubkey registered under the requested owner key from the secondary index and unconditionally loads each one via `do_load` before the caller's filter or size limit is ever consulted: [3](#0-2) 

The only abort mechanism, `accumulate_and_check_scan_result_size`, is invoked inside the filter closure and only accumulates bytes for accounts that already satisfy `use_account` — i.e., accounts that pass the token-account-state/owner/mint memcmp filters: [4](#0-3) 

Critically, the secondary index itself is populated purely from account *data* content, not from any permission or consent check: any account whose SPL token (or Token-2022) layout encodes a given pubkey in the "owner" field gets inserted into `spl_token_owner_index` for that pubkey, regardless of who created the account or whether that owner ever authorized it: [5](#0-4) [6](#0-5) 

Because `get_index_key_pubkeys` returns the full, uncapped `Vec<Pubkey>` for a given index key, and every one of those pubkeys triggers a `do_load` regardless of whether it will ultimately be counted or filtered out, an attacker can drive this list to be arbitrarily large for any target owner pubkey simply by creating many SPL token accounts with that pubkey written into the owner field and a mint/state that causes them to fail the RPC caller's filter (e.g., accounts for a mint different from the one being queried, or zero-lamport tombstones). This exactly mirrors `RabbitHoleReceipt.getOwnedTokenIdsOfQuest`'s pattern: the true cost of the "get all my tokens/accounts" query is proportional to an attacker-controlled count of items registered against the victim's public key, not to the size of the useful result.

### Impact Explanation
A single `getTokenAccountsByOwner` (or `getTokenAccountsByDelegate`) JSON-RPC call against a target owner whose index bucket has been inflated by an attacker forces the serving validator/RPC node to perform an unbounded number of `do_load` account lookups (each an index + storage read) with no cap tied to the number of index entries — only a byte-limit abort that doesn't fire until after a matching account is loaded. This is unbounded compute/I/O cost for a single low-rate call, directly analogous to the referenced report's "claim() reaches block gas limit due to attacker-inflated per-owner list" DoS, but manifesting as RPC-node resource exhaustion/latency rather than gas loss.

### Likelihood Explanation
Creating SPL token accounts with an arbitrary `owner` field is a normal, permissionless SPL Token operation (`InitializeAccount`/`InitializeAccount2/3` let the creator specify any owner pubkey) — the victim's cooperation or knowledge is not required. An attacker only pays the rent-exempt minimum per dummy account, which is cheap relative to the ongoing cost imposed on every future `getTokenAccountsByOwner` query against that pubkey by anyone (including the victim or dependent dApps/wallets), so the attack is inexpensive and repeatable, and the resulting query is a single call, matching the rules' "unbounded cost for a single low-rate call" acceptance criterion.

### Recommendation
Cap the number of pubkeys pulled from `get_index_key_pubkeys`/scanned by `index_scan_accounts` per request (independent of the post-filter byte-size abort), or move the size/limit check to run per-iteration before `do_load`, so a request bounded to loading `N` unfiltered candidates cannot be inflated by attacker-created entries in the secondary index that are guaranteed to fail the caller's filter.

### Proof of Concept
1. Attacker repeatedly submits `InitializeAccount`/`InitializeAccount3` (or Token-2022 equivalent) instructions creating token accounts for an unrelated mint, each with `owner` field set to victim's pubkey, paying only rent-exemption per account.
2. This causes `update_spl_token_secondary_indexes` to insert victim's pubkey → each new account pubkey into `spl_token_owner_index` with no cap. [7](#0-6) 
3. Any subsequent `getTokenAccountsByOwner(victim, {mint: legitimate_mint})` call causes `index_scan_accounts` to iterate and `do_load` every one of the attacker's planted pubkeys (which all fail the mint/state filter and are discarded, never tripping the byte-limit abort), making the single RPC call's cost scale linearly with attacker-inserted junk entries. [8](#0-7)

### Citations

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

**File:** accounts-db/src/accounts.rs (L367-381)
```rust
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

**File:** accounts-db/src/accounts_db.rs (L3396-3410)
```rust
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

**File:** accounts-db/src/accounts_index/secondary.rs (L252-258)
```rust
    pub fn get(&self, key: &Pubkey) -> Vec<Pubkey> {
        if let Some(inner_keys_map) = self.index.get(key) {
            inner_keys_map.keys()
        } else {
            vec![]
        }
    }
```
