No `overflow-checks` override was found in the repo's Cargo profiles, meaning the workspace relies on Cargo's default (overflow checks disabled in `release` profile, enabled only in `dev`). This means in a production/release validator binary, the unguarded subtraction identified below will silently wrap rather than panic — which changes the impact from a crash to a "wrong data returned" issue. I could not find an explicit `[profile.release]` `overflow-checks = true` setting anywhere in the indexed portions of the repo, but this is not a complete guarantee since not all Cargo.toml files may be indexed.

### Title
`getSupply` RPC handler underflows `circulating` supply via un-saturated subtraction - (File: rpc/src/rpc.rs)

### Summary
`get_supply`, backing the unprivileged `getSupply` JSON-RPC method, computes `circulating` supply as a plain `total_supply - non_circulating_supply.lamports` subtraction with no overflow protection, unlike the equivalent logic used elsewhere in the codebase which explicitly uses `saturating_sub`.

### Finding Description
`JsonRpcRequestProcessor::get_supply` computes the non-circulating lamports via `calculate_non_circulating_supply`, then derives `circulating` with unchecked subtraction: [1](#0-0) 

This is the same pattern as the Sherlock finding: a downstream, derived value (`circulating`) is computed by subtracting a percentage/portion-tracking amount (`non_circulating_supply.lamports`) from a total (`total_supply`) without verifying the invariant `non_circulating <= total` holds at the point of computation — mirroring how `BancorExchangeProvider::_getScaledAmountOut` applied `exitContribution` without checking remaining supply.

Critically, this codebase already contains the *correct* pattern for the exact same computation in a different code path — the `/v0/circulating-supply` REST handler: [2](#0-1) 

That function explicitly uses `total_supply.saturating_sub(non_circulating_supply.lamports)`, showing the codebase authors are aware this subtraction is not provably safe and deliberately guarded it in one location, but omitted the guard in `rpc.rs::get_supply`.

`non_circulating_supply.lamports` is the sum of balances of a fixed list of ~100 hardcoded mainnet pubkeys plus any stake accounts matching lockup/withdraw-authority criteria, computed via `bank.get_balance(pubkey)` for each and summed: [3](#0-2) 

While `total_supply = bank.capitalization()` should track the sum of all lamports in the bank and thus normally bound any subset sum, this relies on `capitalization()` staying perfectly synchronized with the true sum of all account balances across all bank states (including snapshot restores, forks, and any capitalization-tracking bugs elsewhere in the runtime). If that invariant is ever violated for any reason (e.g., a bug in incremental capitalization tracking, or a bank state where the non-circulating list's balances are computed against a stale/inconsistent view), the unguarded subtraction underflows.

### Impact Explanation
Since the workspace has no `overflow-checks = true` override found for `release` profile builds, this subtraction would wrap around rather than panic in a production validator, returning a wildly incorrect (near-`u64::MAX`) `circulating` value to any RPC caller, which qualifies as "wrong ... account data returned" from a single unprivileged query. If any build configuration does enable overflow checks (debug builds, or an untracked profile), the same code path would instead panic the RPC-serving thread/process on a single `getSupply` call, matching the "validator-process crash ... from one request" criterion.

### Likelihood Explanation
Triggering this requires `non_circulating_supply.lamports > bank.capitalization()`, which should not happen under normal, bug-free bank/capitalization bookkeeping. I could not find, within the available indexed context, a concrete state-transition path that provably breaks this invariant on demand — so this is best characterized as a defense-in-depth gap that mirrors the reported bug class (an arithmetic operation assuming an invariant without checking it) rather than a proven, independently-triggerable underflow. The strongest evidence for its severity is that the codebase already treats this exact computation as unsafe enough to warrant `saturating_sub` in a parallel implementation.

### Recommendation
Change `circulating: total_supply - non_circulating_supply.lamports` in `rpc/src/rpc.rs::get_supply` to `circulating: total_supply.saturating_sub(non_circulating_supply.lamports)`, matching the safe pattern already used in `rpc_service.rs::calculate_circulating_supply_async`, to eliminate the discrepancy and remove any possibility of a panic or wraparound regardless of build profile.

### Proof of Concept
Not independently reproducible from the indexed context: exploiting this requires first inducing `non_circulating_supply.lamports > bank.capitalization()`, and I did not find a demonstrated mechanism in this codebase to force that precondition. The finding is reported as a code-level defect (missing `saturating_sub`) analogous to the reported bug class, with the inconsistency against `rpc_service.rs`'s guarded version as direct supporting evidence rather than a working exploit trace.

### Citations

**File:** rpc/src/rpc.rs (L1121-1152)
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
