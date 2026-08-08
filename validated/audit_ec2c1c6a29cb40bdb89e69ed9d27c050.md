### Title
`getProgramAccounts` can return an account no longer owned by the queried program due to a stale `ProgramId` secondary index entry - ([File: rpc/src/rpc.rs], [File: accounts-db/src/accounts_db.rs])

### Summary
This is a direct analog of the report's bug class: a stale identifier→entity mapping (deleted profile handle still resolving to the old profile id) is trusted without re-validating that the mapping still matches the entity's current state. In agave, `getFilteredProgramAccounts` (via the `AccountIndex::ProgramId` secondary index) can return an account keyed under a `program_id` that is no longer that account's actual `owner`, because the secondary-index lookup path for `ProgramId` (unlike the `SplTokenOwner`/`SplTokenMint` paths) does not re-verify `account.owner() == program_id` before returning results.

### Finding Description
`AccountsDb::purge_secondary_indexes_for_dead_keys` explicitly documents that stale secondary-index entries persist when an account is re-created with a different value (e.g. re-assigned to a different program owner) while still "cache-live," and states that "scans tolerate stale entries by post-filtering against account data" [1](#0-0) .

This reliance on callers to post-filter is confirmed by the runtime test `test_get_filtered_indexed_accounts`, which explicitly stores an account under `program_id`, then re-stores the *same pubkey* with a *different* owner (`another_program_id`) in a child bank, and shows that `get_filtered_indexed_accounts(&IndexKey::ProgramId(program_id), |_| true, None)` still returns the account (now owned by `another_program_id`) — the comment states this "demonstrates the need for a redundant post-processing filter" [2](#0-1) . The test then shows the *correct* mitigation requires the caller to add a `|account| account.owner() == &program_id` post filter [3](#0-2) .

`rpc/src/rpc.rs`'s SPL-token-specific helpers apply this mitigation explicitly: `get_filtered_spl_token_accounts_by_owner` and `get_filtered_spl_token_accounts_by_mint` both push `RpcFilterType::TokenAccountState` and an owner/mint `Memcmp` filter with a comment stating this compensates for stale zero-lamport tombstones left by the secondary index [4](#0-3) [5](#0-4) .

However, `get_filtered_program_accounts` — the handler backing the plain `getProgramAccounts` JSON-RPC method when the `ProgramId` secondary index is enabled — only forwards the caller-supplied `filters` into `get_filtered_indexed_accounts` with no automatic `owner == program_id` post-filter added: [6](#0-5) . If the caller supplies no filters (or filters unrelated to ownership), a stale `ProgramId` index entry from an account whose owner has since changed to another program can be returned as if it still belonged to the queried `program_id`.

### Impact Explanation
An unprivileged JSON-RPC caller invoking `getProgramAccounts` for `program_id` A can receive an account payload for a pubkey whose current owner is actually program B (a different, unrelated program), if that account previously belonged to A and the secondary index entry has not yet been purged (which per `accounts_db.rs` comments can persist across cache-live windows). This is a "wrong data returned" class of issue: a client relying on `getProgramAccounts` to enumerate accounts owned by a specific program can be fed stale/incorrect ownership information, potentially leading to wrong on-chain state assumptions in downstream tooling (e.g., indexers, wallets, or bots that trust the returned account's association with the program without re-checking `owner`).

### Likelihood Explanation
Requires: (1) the validator operator to have enabled the `ProgramId` secondary index (`--account-index program-id`), which is an operator-configured, non-default feature; and (2) an account whose owner field changed away from `program_id` while a stale secondary-index entry is still cache-resident. This is a narrower condition than the always-active token-owner/mint paths, and the stale window is bounded by cache flush/purge timing described in `accounts_db.rs`, so it is not a persistent, deterministic condition but a real, reachable window given the documented mechanics — same root cause pattern as the original report's cross-referenced but unvalidated identifier reuse.

### Recommendation
In `get_filtered_program_accounts` (`rpc/src/rpc.rs`), when going through the `ProgramId` secondary-index path (`get_filtered_indexed_accounts` with `IndexKey::ProgramId(program_id)`), add a mandatory post-filter checking `account.owner() == &program_id`, mirroring the pattern already used in `get_filtered_spl_token_accounts_by_owner`/`_by_mint` for `SplTokenOwner`/`SplTokenMint`, and as demonstrated as the fix in `test_get_filtered_indexed_accounts`.

### Proof of Concept
1. Enable the `ProgramId` account secondary index on a validator/RPC node.
2. Create an account with `owner = program_id_A`.
3. In a subsequent slot, re-store the same pubkey's account data with `owner = program_id_B` (re-purposing the same address, or in practice, any transaction that reassigns account ownership while the older entry is still cache-resident/not yet purged from the secondary index, per `accounts_db.rs:1359-1367`).
4. Call `getProgramAccounts(program_id_A)` with no filters.
5. Per the same mechanics exercised by `test_get_filtered_indexed_accounts` [2](#0-1) , the RPC response can include the pubkey's current account data (now owned by `program_id_B`) under the `program_id_A` query, because `get_filtered_program_accounts`'s `ProgramId` index path does not add an owner-equality post-filter [6](#0-5) .

### Citations

**File:** accounts-db/src/accounts_db.rs (L1359-1367)
```rust
    /// Purges each key in `removed_keys` from the enabled secondary indexes, unless the key is
    /// still alive in the write cache. `removed_keys` must be keys that are not present in the
    /// primary index
    ///
    /// The cache check is all-or-nothing per key: a key kept because it is cache-live retains all
    /// of its secondary entries, including stale ones from its dead rooted versions (e.g. an old
    /// mint after the account is re-created with a new one). Scans tolerate stale entries by
    /// post-filtering against account data, and they are removed the next time the key dies while
    /// not cache-resident.
```

**File:** runtime/src/bank/tests.rs (L3524-3551)
```rust
    let indexed_accounts = bank
        .get_filtered_indexed_accounts(&IndexKey::ProgramId(program_id), |_| true, None)
        .unwrap();
    assert_eq!(indexed_accounts.len(), 1);
    assert_eq!(indexed_accounts[0], (address, account));

    // Even though the account is re-stored in the bank (and the index) under a new program id,
    // it is still present in the index under the original program id as well. This
    // demonstrates the need for a redundant post-processing filter.
    let another_program_id = Pubkey::new_unique();
    let new_account = AccountSharedData::new(1, 0, &another_program_id);
    let bank = Bank::new_from_parent_with_bank_forks(
        bank_forks.as_ref(),
        bank.clone(),
        SlotLeader::default(),
        bank.slot() + 1,
    );
    bank.store_account(&address, &new_account);
    let indexed_accounts = bank
        .get_filtered_indexed_accounts(&IndexKey::ProgramId(program_id), |_| true, None)
        .unwrap();
    assert_eq!(indexed_accounts.len(), 1);
    assert_eq!(indexed_accounts[0], (address, new_account.clone()));
    let indexed_accounts = bank
        .get_filtered_indexed_accounts(&IndexKey::ProgramId(another_program_id), |_| true, None)
        .unwrap();
    assert_eq!(indexed_accounts.len(), 1);
    assert_eq!(indexed_accounts[0], (address, new_account.clone()));
```

**File:** runtime/src/bank/tests.rs (L3553-3561)
```rust
    // Post-processing filter
    let indexed_accounts = bank
        .get_filtered_indexed_accounts(
            &IndexKey::ProgramId(program_id),
            |account| account.owner() == &program_id,
            None,
        )
        .unwrap();
    assert!(indexed_accounts.is_empty());
```

**File:** rpc/src/rpc.rs (L2262-2282)
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
                message: e.to_string(),
            })
```

**File:** rpc/src/rpc.rs (L2318-2331)
```rust
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

```

**File:** rpc/src/rpc.rs (L2368-2380)
```rust
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
```
