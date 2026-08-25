## Title
Unchecked subtraction in `getSupply` RPC handler can panic the JSON-RPC thread on integer underflow - (File: `rpc/src/rpc.rs`)

### Summary
The `getSupply` RPC method computes `circulating = total_supply - non_circulating_supply.lamports` using plain, unchecked `u64` subtraction. This is the exact same computation the codebase performs in `rpc_service.rs`'s `/v0/circulating-supply` REST endpoint, but there it is correctly implemented with `saturating_sub`. The `getSupply` JSON-RPC path lacks this guard, so if the two independently-computed values (`bank.capitalization()` and the summed non-circulating balances from an out-of-band accounts scan) are ever observed such that `non_circulating_supply.lamports > total_supply`, the subtraction underflows and panics, analogous to the reported `mintCap - totalSupply` underflow-revert bug in `sUSX.maxDeposit`.

### Finding Description
`JsonRpcRequestProcessor::get_supply` reads two values that are computed independently and asynchronously, not as one atomic snapshot: [1](#0-0) 

- `non_circulating_supply` is produced by `calculate_non_circulating_supply(&bank)`, run on a blocking thread, which performs an expensive full scan of stake-program accounts and then sums balances for a growing `HashSet<Pubkey>` via `bank.get_balance(pubkey)` calls issued one-by-one: [2](#0-1) 

- `total_supply` is read afterward as `bank.capitalization()`, a separate atomic load of a counter that is mutated independently (via `fetch_add`/`fetch_sub`) as the underlying bank continues to process/commit transactions, epoch rewards, and builtin/precompile account changes: [3](#0-2) [4](#0-3) 

For the `processed` (and even `confirmed`) commitment level, `self.bank(config.commitment)` returns a bank that can still be actively mutated concurrently by the banking/replay stage while the RPC handler's multi-step, non-atomic scan is in flight. The final line then performs the subtraction with no overflow protection: [5](#0-4) 

Critically, the codebase demonstrates that the developers are aware this subtraction needs saturation — the equivalent REST handler for `/v0/circulating-supply` uses `saturating_sub` for the identical computation: [6](#0-5) 

The `getSupply` JSON-RPC method (used far more widely by wallets/exchanges/explorers than the REST endpoint) was left without this guard, making it the outlier unsafe implementation of the same "total minus subset" pattern.

### Impact Explanation
If the two independently-sampled values diverge such that the non-circulating sum ever exceeds the later-read `total_supply`, the subtraction panics with an arithmetic overflow in a debug build, or produces a semantically nonsensical wrapped `circulating` value in a release build without overflow checks — either way this is a crash/incorrect-state-serving bug reachable purely from an ordinary, unauthenticated `getSupply` RPC call, which falls within the "RPC request handling" scope. A panic inside the JSON-RPC request thread can be leveraged to repeatedly disrupt RPC service availability for that node.

### Likelihood Explanation
This requires a timing race between the non-circulating account scan (bank-wide index walk over stake-program accounts plus a fixed list of pubkeys) and the bank's capitalization counter being asynchronously mutated by ongoing transaction/epoch-reward processing on the same working bank — most plausible during epoch boundaries where large batches of stake/vote accounts (which are part of the "non-circulating" set) receive inflation rewards while `getSupply` is being served for a `processed`/`confirmed` bank. This is a narrower window than the original report's straightforward owner-triggered underflow, so likelihood is lower, but the reachability is fully unprivileged (any RPC client) and the code path is identical in pattern to the reported bug class.

### Recommendation
Change `rpc/src/rpc.rs`'s `get_supply` to mirror the fix already used in `rpc_service.rs`:
```rust
circulating: total_supply.saturating_sub(non_circulating_supply.lamports),
```
This avoids the possibility of a panic or wraparound, consistent with how the sibling REST implementation already guards the identical computation.

### Proof of Concept
1. Run a validator and issue `getSupply` at `processed` or `confirmed` commitment while the bank is actively processing an epoch-boundary reward distribution that increases balances of locked stake/vote accounts (members of the non-circulating set).
2. The RPC handler first spends time scanning/summing non-circulating balances (`calculate_non_circulating_supply`), then separately reads `bank.capitalization()`.
3. Because these two reads are not taken as a single atomic snapshot, construct a timing window (e.g., by repeatedly polling `getSupply` during heavy concurrent reward/account churn) where the summed non-circulating lamports briefly exceeds the later-read capitalization value, triggering the unchecked `total_supply - non_circulating_supply.lamports` subtraction to underflow/panic.

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

**File:** runtime/src/bank.rs (L5729-5732)
```rust
    /// Return the total capitalization of the Bank
    pub fn capitalization(&self) -> u64 {
        self.capitalization.load(Relaxed)
    }
```

**File:** rpc/src/rpc_service.rs (L422-432)
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
}
```
