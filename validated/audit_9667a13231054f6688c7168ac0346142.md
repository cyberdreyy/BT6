### Title
Unbounded per-request cost for `getTokenLargestAccounts` via attacker-inflated SPL-Token-Mint secondary index - ([File: rpc/src/rpc.rs], [File: accounts-db/src/accounts_db.rs])

### Summary
The reported bug class is unbounded work forced onto an unprivileged caller because an attacker can cheaply grow an array (`claimerToBuyers`) that a single downstream call must iterate in full, with no minimum-size gate. The closest analog in Agave is the `getTokenLargestAccounts` JSON-RPC handler, which — when the validator is run with the SPL-Token-Mint secondary index enabled — resolves the full list of token account pubkeys for a mint via the secondary index and loads every one of them for a single low-rate RPC call, with no cap on the number of entries scanned.

### Finding Description
`getTokenLargestAccounts` is exposed as an unprivileged, unauthenticated RPC method in the `AccountsScan` trait [1](#0-0) , implemented by looking up accounts tied to a mint. When the node has the `SplTokenMint` account index enabled (a common configuration for RPC/API nodes to make these exact token queries efficient), lookups for a given mint go through `index_scan_accounts`, which pulls **all** pubkeys registered under that mint key from the secondary index and individually loads each one with no upper bound on count: [2](#0-1) 

The pubkey list itself comes from an unbounded `Vec<Pubkey>` maintained per index key: [3](#0-2) [4](#0-3) 

Because any user can permissionlessly create new SPL token accounts for an existing mint (each is a cheap, ordinary transaction, analogous to the "small purchases" in the report that populate `claimerToBuyers`), an attacker can inflate the `SplTokenMint` index entry for a chosen mint to an arbitrarily large size. Once inflated, **any single unprivileged caller** issuing one `getTokenLargestAccounts` request for that mint forces the RPC node to perform a `do_load` for every entry in the bloated list before it can compute the top-N balances — there is no minimum balance or size filter comparable to the "minimum purchase amount" remediation recommended in the original report. This mirrors the structural fault: fee/cost paid by one caller (the claimer / the RPC node) is proportional to an array size that a different, unprivileged party fully controls and can grow with dust-value entries.

### Impact Explanation
A single, unprivileged, low-rate JSON-RPC call (`getTokenLargestAccounts`) can be made to perform O(n) account loads where n is attacker-controlled and unbounded, since the attacker can keep creating new (or reusing rent-exempt-minimum) SPL token accounts for the target mint at negligible cost. This can significantly degrade or stall the RPC-serving thread/node handling the request for a duration disproportionate to the cost the attacker incurred, satisfying the "unbounded cost for a single low-rate call" acceptance criterion.

### Likelihood Explanation
This requires the node operator to have enabled the `--account-index spl-token-mint` secondary index (a real, documented, and commonly-used RPC node configuration option) [5](#0-4) . Given that configuration, the attack requires only ordinary permissionless token-account creation transactions against a mint the attacker also controls or can freely mint into, making exploitation straightforward for anyone targeting a public RPC endpoint that serves this index-enabled configuration.

### Recommendation
Cap the number of index-key pubkeys scanned/loaded per `getTokenLargestAccounts` (and similarly for other index-key-driven RPC scans) to a fixed maximum, or require pagination/minimum-balance filtering before the secondary index scan proceeds, analogous to the report's recommended minimum-purchase-amount gate. Consider tracking running max-heap candidates incrementally with early pruning (as `load_largest_accounts` already does for `getLargestAccounts` via a bounded `BinaryHeap`, see [6](#0-5) ) so that per-account load cost does not scale unbounded with attacker-inflated index size.

### Proof of Concept
1. Configure/target a validator RPC node running with `--account-index spl-token-mint` enabled.
2. As an attacker, mint a large number (tens of thousands) of near-zero-balance token accounts for a mint you control, each via a cheap standard `CreateAccount` + `InitializeAccount`/`MintTo` transaction sequence — populating the `SplTokenMint` secondary index entry for that mint [7](#0-6) .
3. Issue a single `getTokenLargestAccounts` RPC call for that mint from any unauthenticated client.
4. Observe that the RPC node must call `do_load` once per indexed pubkey [2](#0-1) , causing request latency/cost to scale linearly with the attacker-inflated index size rather than with any cost paid by the attacker per request.

### Citations

**File:** rpc/src/rpc.rs (L3391-3397)
```rust
        #[rpc(meta, name = "getTokenLargestAccounts")]
        fn get_token_largest_accounts(
            &self,
            meta: Self::Metadata,
            mint_str: String,
            commitment: Option<CommitmentConfig>,
        ) -> BoxFuture<Result<RpcResponse<Vec<RpcTokenAccountBalance>>>>;
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

**File:** accounts-db/src/accounts_index.rs (L573-578)
```rust
            if account_indexes.contains(&AccountIndex::SplTokenMint)
                && let Some(mint_key) = G::unpack_account_mint(account_data)
                && account_indexes.include_key(mint_key)
            {
                self.spl_token_mint_index.insert(mint_key, pubkey);
            }
```

**File:** accounts-db/src/accounts_index.rs (L582-596)
```rust
    pub fn get_index_key_size(&self, index: &AccountIndex, index_key: &Pubkey) -> Option<usize> {
        match index {
            AccountIndex::ProgramId => self.program_id_index.index.get(index_key).map(|x| x.len()),
            AccountIndex::SplTokenOwner => self
                .spl_token_owner_index
                .index
                .get(index_key)
                .map(|x| x.len()),
            AccountIndex::SplTokenMint => self
                .spl_token_mint_index
                .index
                .get(index_key)
                .map(|x| x.len()),
        }
    }
```

**File:** validator/src/commands/run/args/account_secondary_indexes.rs (L73-76)
```rust
    #[test_case("program-id", AccountIndex::ProgramId)]
    #[test_case("spl-token-mint", AccountIndex::SplTokenMint)]
    #[test_case("spl-token-owner", AccountIndex::SplTokenOwner)]
    fn verify_args_struct_by_command_run_with_account_indexes(
```

**File:** accounts-db/src/accounts.rs (L266-296)
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
```
