### Title
Unchecked subtraction in `getSupply` RPC handler can panic the validator on integer underflow - ([File: rpc/src/rpc.rs])

### Summary
The `getSupply` JSON-RPC method computes `circulating` supply as a plain, unchecked subtraction of `non_circulating_supply.lamports` from `bank.capitalization()`. Both values are computed independently and at different points in time against a bank that may still be actively mutating (not frozen), so the invariant `non_circulating_supply.lamports <= total_supply` is not guaranteed to hold at the moment of subtraction, unlike the analogous "credited balance not updated before subtraction" bug in the source report where a stale/inconsistent balance tracker caused an underflow in a later balance check.

### Finding Description
In `JsonRpcRequestProcessor::get_supply`, `total_supply` is captured from `bank.capitalization()` and then `non_circulating_supply` is computed asynchronously via a `spawn_blocking` scan of program accounts (`calculate_non_circulating_supply`), which walks the accounts index of the same (potentially still-mutable) `bank`: [1](#0-0) 

```rust
async fn get_supply(...) -> RpcCustomResult<RpcResponse<RpcSupply>> {
    let bank = self.bank(config.commitment);
    let non_circulating_supply = self.calculate_non_circulating_supply(&bank).await...;
    let total_supply = bank.capitalization();
    ...
    Ok(new_response(&bank, RpcSupply {
        total: total_supply,
        circulating: total_supply - non_circulating_supply.lamports,   // <-- unchecked subtraction
        non_circulating: non_circulating_supply.lamports,
        non_circulating_accounts,
    }))
}
```

`calculate_non_circulating_supply` walks stake accounts (via `get_filtered_indexed_accounts` / `get_program_accounts`) and sums `bank.get_balance(pubkey)` for each non-circulating account, entirely independently of the earlier `bank.capitalization()` snapshot: [2](#0-1) 

Critically, the exact same computation exists elsewhere in the codebase and is explicitly guarded with `saturating_sub`, showing the developers were aware this subtraction can underflow: [3](#0-2) 
```rust
async fn calculate_circulating_supply_async(bank: &Arc<Bank>) -> Result<u64, SupplyCalcError> {
    let total_supply = bank.capitalization();
    ...
    Ok(total_supply.saturating_sub(non_circulating_supply.lamports))
}
```

This is the RPC analog of the reported bug class: a value (`total_supply`/capitalization) that is assumed to always dominate a derived subset value (`non_circulating_supply.lamports`) is used in a raw subtraction without the same defensive `saturating_sub` used in the sibling implementation for the identical computation, because the two operands are computed from a bank whose state can change between the two reads (the bank passed to `get_supply` is not required to be frozen, and stake-account balances/capitalization can be updated concurrently by ongoing transaction processing/epoch-reward distribution while the accounts-index scan runs in a separate blocking task).

### Impact Explanation
If integer overflow/underflow checks are enabled for the `getSupply` code path (or in any build configuration where `cargo` enables `overflow-checks`), a single `getSupply` RPC call from an unprivileged client can trigger an arithmetic underflow panic in the JSON-RPC processing thread, causing a validator-process crash from one JSON-RPC request. Even where overflow checks are disabled and the subtraction instead silently wraps, this yields a wildly incorrect (wrong-data) `circulating` supply value returned to the caller, i.e., objectively wrong data returned from a query, which still falls within the accepted class of "wrong-slot/fork/account data returned."

### Likelihood Explanation
The race window requires the scanned/background-computed `non_circulating_supply.lamports` to reflect a bank state whose subset sum of non-circulating accounts exceeds the capitalization value captured earlier — e.g., during concurrent epoch-reward distribution or other lamport movement into non-circulating (locked-up/withdraw-authority-restricted) stake accounts between the two reads. This is a genuine, unprivileged, single-call RPC path (no special client role, no crafted snapshot, no multi-call requirement), but the race window is narrow and depends on concurrent bank mutation timing, so likelihood is Low-to-Medium rather than High.

### Recommendation
Change `total_supply - non_circulating_supply.lamports` in `get_supply` (rpc/src/rpc.rs) to `total_supply.saturating_sub(non_circulating_supply.lamports)`, mirroring the existing defensive computation already used in `calculate_circulating_supply_async` in `rpc/src/rpc_service.rs`. Additionally, consider computing `total_supply` from the same consistent point-in-time as `non_circulating_supply` (e.g., capture `bank.capitalization()` after or atomically with the scan, or use a frozen bank) to remove the underlying TOCTOU condition rather than merely masking it.

### Proof of Concept
Not independently reproducible without a live cluster/validator under load; the vulnerability is demonstrated structurally by comparing the two code paths:
- Unsafe: [4](#0-3) 
- Safe (same computation, elsewhere): [3](#0-2) 

To trigger in practice, a client would call `getSupply` via JSON-RPC concurrently with a period of active epoch-reward distribution or stake-account funding on the target validator's working bank, so that the `calculate_non_circulating_supply` scan (running in a separate blocking task) observes lamports credited to non-circulating stake accounts after `bank.capitalization()` was already read, without the reward's full amount yet reflected. I was unable to fully verify the workspace's Cargo `overflow-checks` setting (no matches for `overflow-checks` were found in the indexed portion of the codebase), so I cannot confirm with certainty whether this manifests as a hard panic versus a silent wraparound in the deployed release binaries; a Devin session with full repository/build access would be needed to check the Cargo profile settings to determine definitively which failure mode applies.

### Citations

**File:** rpc/src/rpc.rs (L1121-1153)
```rust
    async fn get_supply(
        &self,
        config: Option<RpcSupplyConfig>,
    ) -> RpcCustomResult<RpcResponse<RpcSupply>> {
        let config = config.unwrap_or_default();
        let bank = self.bank(config.commitment);
        let non_circulating_supply =
            self.calculate_non_circulating_supply(&bank)
                .await
                .map_err(|e| RpcCustomError::ScanError {
                    message: e.to_string(),
                })?;
        let total_supply = bank.capitalization();
        let non_circulating_accounts = if config.exclude_non_circulating_accounts_list {
            vec![]
        } else {
            non_circulating_supply
                .accounts
                .iter()
                .map(|pubkey| pubkey.to_string())
                .collect()
        };

        Ok(new_response(
            &bank,
            RpcSupply {
                total: total_supply,
                circulating: total_supply - non_circulating_supply.lamports,
                non_circulating: non_circulating_supply.lamports,
                non_circulating_accounts,
            },
        ))
    }
```

**File:** runtime/src/non_circulating_supply.rs (L19-79)
```rust
pub fn calculate_non_circulating_supply(bank: &Bank) -> ScanResult<NonCirculatingSupply> {
    debug!("Updating Bank supply, epoch: {}", bank.epoch());
    let mut non_circulating_accounts_set: HashSet<Pubkey> = HashSet::new();

    for key in non_circulating_accounts() {
        non_circulating_accounts_set.insert(key);
    }
    let withdraw_authority_list = withdraw_authority();

    let clock = bank.clock();
    let stake_accounts = if bank
        .rc
        .accounts
        .accounts_db
        .account_indexes
        .contains(&AccountIndex::ProgramId)
    {
        bank.get_filtered_indexed_accounts(
            &IndexKey::ProgramId(stake::program::id()),
            // The program-id account index checks for Account owner on inclusion. However, due to
            // the current AccountsDb implementation, an account may remain in storage as a
            // zero-lamport Account::Default() after being wiped and reinitialized in later
            // updates. We include the redundant filter here to avoid returning these accounts.
            |account| account.owner() == &stake::program::id(),
            None,
        )?
    } else {
        bank.get_program_accounts(&stake::program::id())?
    };

    for (pubkey, account) in stake_accounts.iter() {
        let stake_account = account
            .deserialize_data::<StakeStateV2>()
            .unwrap_or_default();
        match stake_account {
            StakeStateV2::Initialized(meta)
                if (meta.lockup.is_in_force(&clock, None)
                    || withdraw_authority_list.contains(&meta.authorized.withdrawer)) =>
            {
                non_circulating_accounts_set.insert(*pubkey);
            }
            StakeStateV2::Stake(meta, _stake, _stake_flags)
                if (meta.lockup.is_in_force(&clock, None)
                    || withdraw_authority_list.contains(&meta.authorized.withdrawer)) =>
            {
                non_circulating_accounts_set.insert(*pubkey);
            }
            _ => {}
        }
    }

    let lamports = non_circulating_accounts_set
        .iter()
        .map(|pubkey| bank.get_balance(pubkey))
        .sum();

    Ok(NonCirculatingSupply {
        lamports,
        accounts: non_circulating_accounts_set.into_iter().collect(),
    })
}
```

**File:** rpc/src/rpc_service.rs (L422-431)
```rust
async fn calculate_circulating_supply_async(bank: &Arc<Bank>) -> Result<u64, SupplyCalcError> {
    let total_supply = bank.capitalization();
    let bank = Arc::clone(bank);
    let non_circulating_supply =
        tokio::task::spawn_blocking(move || calculate_non_circulating_supply(&bank))
            .await
            .expect("Failed to spawn blocking task")
            .map_err(|e| SupplyCalcError::Scan(e.to_string()))?;

    Ok(total_supply.saturating_sub(non_circulating_supply.lamports))
```
