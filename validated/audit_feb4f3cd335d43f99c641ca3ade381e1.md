### Title
Unprivileged attacker can pollute another pubkey's SPL-token-owner secondary index, causing unbounded `getTokenAccountsByOwner`/`getProgramAccounts` scan cost on a single RPC call - (File: `accounts-db/src/accounts_index/secondary.rs`, `accounts-db/src/accounts_index.rs`, `rpc/src/rpc.rs`)

### Summary
When the `spl-token-owner` secondary index is enabled, `AccountsIndex::update_secondary_indexes` inserts every SPL Token/Token-2022 account's *unpacked owner field* into `spl_token_owner_index` without verifying that the token account's real chain-of-custody owner authorized the association [1](#0-0) . Because the `owner` field of an SPL token account is just account data (any address can be written there when the account is created, at the attacker's own expense), an unprivileged attacker can create arbitrarily many token accounts that all name an arbitrary victim pubkey as `owner`. This pushes entries into that victim key's index bucket, `spl_token_owner_index.index[victim] -> Vec/HashSet<Pubkey>` [2](#0-1) , a structure with no upper bound on entries per key. When the RPC method `getTokenAccountsByOwner` is later called for that victim, the whole index bucket is read and every referenced account is individually loaded and returned in one request [3](#0-2) [4](#0-3) , giving the attacker control over the cost of a single, low-rate call made by/against the victim.

### Finding Description
This is the Agave analog of the reported `LiquidityFarming.sol` bug: an unprivileged party can append to a data structure keyed by *someone else's* identity, and the victim later pays the iteration cost.

- Insertion path: `update_secondary_indexes` -> `update_spl_token_secondary_indexes` unpacks the `owner` field straight from account data and inserts `(owner_key, pubkey)` into `spl_token_owner_index` with no signature/authorization check tying `owner_key` to the transaction signer [5](#0-4) .
- `SecondaryIndex::insert` unconditionally grows the forward-index entry for `key` (the owner) [6](#0-5) ; there is no per-key cap.
- Read path: `getTokenAccountsByOwner` -> `get_filtered_spl_token_accounts_by_owner` -> `get_filtered_indexed_accounts` -> `index_scan_accounts`, which iterates `self.accounts_index.get_index_key_pubkeys(&index_key)` and does a `do_load` for every pubkey found under that owner key [3](#0-2) [4](#0-3) . This scan is triggered by a single RPC request and its cost scales with however many accounts the attacker chose to insert under the victim's key.
- The only operator-side mitigation is `--account-index-exclude-key`/`--account-index-include-key`, applied per specific pubkey and only effective if the operator anticipates the exact victim key in advance [7](#0-6) ; it is not a general bound on index-bucket size.

### Impact Explanation
An RPC node that has enabled the `spl-token-owner` secondary index (a supported production configuration, not a debug/bootstrap-only flag) can be made to do unbounded work per `getTokenAccountsByOwner` request for any chosen victim address, at a cost to the attacker of only the rent-exempt lamports for however many token accounts they wish to create. This can consume disproportionate CPU/memory in the RPC scan path and degrade or stall RPC service for a single, otherwise-innocuous query, matching "unbounded cost for a single low-rate call."

### Likelihood Explanation
Requires the operator to run with `--account-index spl-token-owner` (common on RPC-serving nodes that support `getTokenAccountsByOwner`/wallet-indexing use cases). Creating SPL token accounts with an arbitrary `owner` field is a normal, permissionless SPL Token operation (`InitializeAccount`/`InitializeAccount3` writes the owner field from instruction data, not from a signer check) — no special privilege is required, only enough SOL for rent-exemption per account.

### Recommendation
Bound the number of accounts a single index bucket can accumulate per outer key (e.g., cap entries in `spl_token_owner_index`/`program_id_index`), or cap the amount of work `get_filtered_indexed_accounts`/`index_scan_accounts` will perform for a single query (paging, hard limit with truncation + explicit error) similar to how other list-returning RPCs (`getConfirmedSignaturesForAddress2`, `getSignatureStatuses`) enforce `MAX_..._LIMIT` constants [8](#0-7) .

### Proof of Concept
1. Run a validator/RPC node with `--account-index spl-token-owner`.
2. As an unprivileged attacker, repeatedly create SPL Token (or Token-2022) accounts (`InitializeAccount`) with the `owner` field set to a fixed victim pubkey (attacker only needs rent-exempt SOL per account; no victim signature or authorization is required).
3. Each created account causes `AccountsIndex::update_secondary_indexes` to append the new account pubkey under `spl_token_owner_index[victim]` [1](#0-0) .
4. Call `getTokenAccountsByOwner(victim, {programId: TOKEN_PROGRAM_ID})`; the RPC node performs `get_filtered_indexed_accounts` -> `index_scan_accounts`, loading and encoding every attacker-inserted account in one request/response cycle [4](#0-3) , with cost proportional to how many accounts the attacker chose to create.

### Citations

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

**File:** accounts-db/src/accounts_index/secondary.rs (L113-153)
```rust
#[derive(Debug, Default)]
pub struct SecondaryIndex<SecondaryIndexEntryType: SecondaryIndexEntry + Default + Sync + Send> {
    metrics_name: &'static str,
    // Map from index keys to index values
    pub index: DashMap<Pubkey, SecondaryIndexEntryType>,
    pub reverse_index: DashMap<Pubkey, SecondaryReverseIndexEntry>,
    stats: SecondaryIndexStats,
}

impl<SecondaryIndexEntryType: SecondaryIndexEntry + Default + Sync + Send>
    SecondaryIndex<SecondaryIndexEntryType>
{
    pub fn new(metrics_name: &'static str) -> Self {
        Self {
            metrics_name,
            ..Self::default()
        }
    }

    /// Inserts `inner_key` into `key`'s map.
    pub fn insert(&self, key: &Pubkey, inner_key: &Pubkey) {
        // Note: Always lock the reverse index first, so we synchronize with remove().
        // Pre-size to 1 to avoid push() over-allocating an empty Vec to capacity 4.
        let reverse_index_entry = self
            .reverse_index
            .entry(*inner_key)
            .or_insert_with(|| RwLock::new(Vec::with_capacity(1)));
        let mut outer_keys = reverse_index_entry.write().unwrap();

        // Now insert into the index.
        // Note, we do this get()-then-unwrap instead of calling entry() directly, because
        // get() is a read lock whereas entry() is a write lock.  We assume `key` already has
        // a map created, so optimize for the common case and only take a read lock.
        self.index
            .get(key)
            .unwrap_or_else(|| self.index.entry(*key).or_default().downgrade())
            .insert_if_not_exists(inner_key, &self.stats.num_inner_keys);

        if !outer_keys.contains(key) {
            outer_keys.push(*key);
        }
```

**File:** rpc/src/rpc.rs (L2332-2356)
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
        } else {
            self.get_filtered_program_accounts(bank, program_id, filters, sort_results)
                .await
        }
```

**File:** rpc/src/rpc.rs (L2533-2539)
```rust
    let limit = limit.unwrap_or(MAX_GET_CONFIRMED_SIGNATURES_FOR_ADDRESS2_LIMIT);

    if limit == 0 || limit > MAX_GET_CONFIRMED_SIGNATURES_FOR_ADDRESS2_LIMIT {
        return Err(Error::invalid_params(format!(
            "Invalid limit; max {MAX_GET_CONFIRMED_SIGNATURES_FOR_ADDRESS2_LIMIT}"
        )));
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

**File:** validator/src/commands/run/args/account_secondary_indexes.rs (L11-59)
```rust
impl FromClapArgMatches for AccountSecondaryIndexes {
    fn from_clap_arg_match(matches: &ArgMatches) -> Result<Self> {
        let account_indexes: HashSet<AccountIndex> = matches
            .values_of("account_indexes")
            .unwrap_or_default()
            .map(|value| match value {
                "program-id" => AccountIndex::ProgramId,
                "spl-token-mint" => AccountIndex::SplTokenMint,
                "spl-token-owner" => AccountIndex::SplTokenOwner,
                _ => unreachable!(),
            })
            .collect();

        let account_indexes_include_keys: HashSet<Pubkey> =
            values_t!(matches, "account_index_include_key", Pubkey)
                .unwrap_or_default()
                .iter()
                .cloned()
                .collect();

        let account_indexes_exclude_keys: HashSet<Pubkey> =
            values_t!(matches, "account_index_exclude_key", Pubkey)
                .unwrap_or_default()
                .iter()
                .cloned()
                .collect();

        let exclude_keys = !account_indexes_exclude_keys.is_empty();
        let include_keys = !account_indexes_include_keys.is_empty();

        let keys = if !account_indexes.is_empty() && (exclude_keys || include_keys) {
            let account_indexes_keys = AccountSecondaryIndexesIncludeExclude {
                exclude: exclude_keys,
                keys: if exclude_keys {
                    account_indexes_exclude_keys
                } else {
                    account_indexes_include_keys
                },
            };
            Some(account_indexes_keys)
        } else {
            None
        };

        Ok(AccountSecondaryIndexes {
            keys,
            indexes: account_indexes,
        })
    }
```
