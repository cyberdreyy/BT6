### Title
`getSupply` RPC handler computes `circulating` via unchecked subtraction that can underflow/panic if non-circulating lamports exceed reported capitalization - (File: rpc/src/rpc.rs)

### Summary
`JsonRpcRequestProcessor::get_supply()` computes `circulating: total_supply - non_circulating_supply.lamports` using a plain, non-saturating subtraction, unlike the equivalent computation elsewhere in the codebase (`calculate_circulating_supply_async` in `rpc/src/rpc_service.rs`) which uses `saturating_sub`. This mirrors the reported bug class: one code path performs an accounting update/derivation using an unchecked arithmetic operation while a parallel, "correct" path in the same codebase uses a safe operation for the same computation, and the two values feeding the subtraction are not guaranteed to be internally consistent.

### Finding Description
In `rpc/src/rpc.rs`, the `getSupply` JSON-RPC method handler is implemented as: [1](#0-0) 

```rust
async fn get_supply(...) -> RpcCustomResult<RpcResponse<RpcSupply>> {
    ...
    let non_circulating_supply = self.calculate_non_circulating_supply(&bank).await...;
    let total_supply = bank.capitalization();
    ...
    Ok(new_response(&bank, RpcSupply {
        total: total_supply,
        circulating: total_supply - non_circulating_supply.lamports,
        non_circulating: non_circulating_supply.lamports,
        non_circulating_accounts,
    }))
}
```

`total_supply` comes from `bank.capitalization()` [2](#0-1) , an atomically-tracked counter that is incremented/decremented independently every time an account's lamports change via `store_account_and_update_capitalization` [3](#0-2) . `non_circulating_supply.lamports` is computed by a completely separate live scan that sums `bank.get_balance(pubkey)` over a hard-coded list of ~100 pubkeys plus dynamically-discovered locked/authority-restricted stake accounts [4](#0-3) .

Because these two values are derived from independent mechanisms (one incremental counter maintained across all account writes, the other a live re-scan of a specific account subset at query time), they are not mathematically guaranteed to satisfy `non_circulating_supply.lamports <= total_supply` at every instant — for example, mid-scan the bank/accounts could be updated concurrently, or the hard-coded `non_circulating_accounts()` list could reference addresses no longer reflecting the current supply invariant. The exact analog to the reported bug is that `rpc/src/rpc_service.rs::calculate_circulating_supply_async` performs the identical computation for the `/v0/circulating-supply` REST endpoint using `saturating_sub` [5](#0-4) , demonstrating that the authors are aware this subtraction is not provably safe and intentionally use safe arithmetic in that code path, while the JSON-RPC `getSupply` handler was left using a raw `-` operator.

### Impact Explanation
If `non_circulating_supply.lamports` ever exceeds `total_supply` at the moment of the RPC call (e.g., due to a race between the capitalization counter and the live account scan across different bank slots/being non-atomic, or drift in the hardcoded `non_circulating_accounts()` list), the subtraction will underflow. In a debug/overflow-checked build this crashes the validator process. In a release build this silently wraps to a near-`u64::MAX` value, causing the JSON-RPC `getSupply` response (`circulating` field) to be badly wrong — which any unprivileged client consuming this widely-used RPC method (wallets, explorers, exchanges) would observe as an incorrect supply figure.

### Likelihood Explanation
This is reachable by any unprivileged client issuing a single `getSupply` JSON-RPC call — no special privileges required. However, I could not fully verify from static code review alone whether `non_circulating_supply.lamports` can concretely exceed `bank.capitalization()` under real cluster conditions (this would require confirming the on-chain state of the hardcoded pubkey list against current capitalization, and any race conditions in `calculate_non_circulating_supply`'s scan versus the atomic capitalization counter). This is a real code inconsistency (unchecked vs. `saturating_sub` for the identical computation) but the likelihood of actually triggering the underflow could not be conclusively proven from the available code.

### Recommendation
Change `circulating: total_supply - non_circulating_supply.lamports` to `circulating: total_supply.saturating_sub(non_circulating_supply.lamports)` in `rpc/src/rpc.rs`, matching the safe pattern already used in `rpc/src/rpc_service.rs`'s `calculate_circulating_supply_async`.

### Proof of Concept
Not independently reproducible from static analysis alone — a concrete PoC would require constructing a bank/cluster state where the sum of balances of `non_circulating_accounts()` plus locked stake accounts exceeds `bank.capitalization()` at the time of the `getSupply` call, which was not verified in this review.

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

**File:** runtime/src/bank.rs (L4821-4851)
```rust
    pub(crate) fn store_account_and_update_capitalization(
        &self,
        pubkey: &Pubkey,
        new_account: &AccountSharedData,
    ) {
        let old_account_data_size = if let Some(old_account) =
            self.get_account_with_fixed_root_no_cache(pubkey)
        {
            match new_account.lamports().cmp(&old_account.lamports()) {
                std::cmp::Ordering::Greater => {
                    let diff = new_account.lamports() - old_account.lamports();
                    trace!("store_account_and_update_capitalization: increased: {pubkey} {diff}");
                    self.capitalization.fetch_add(diff, Relaxed);
                }
                std::cmp::Ordering::Less => {
                    let diff = old_account.lamports() - new_account.lamports();
                    trace!("store_account_and_update_capitalization: decreased: {pubkey} {diff}");
                    self.capitalization.fetch_sub(diff, Relaxed);
                }
                std::cmp::Ordering::Equal => {}
            }
            old_account.data().len()
        } else {
            trace!(
                "store_account_and_update_capitalization: created: {pubkey} {}",
                new_account.lamports()
            );
            self.capitalization
                .fetch_add(new_account.lamports(), Relaxed);
            0
        };
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
