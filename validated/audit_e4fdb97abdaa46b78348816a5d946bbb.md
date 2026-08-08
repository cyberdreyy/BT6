### Title
`getSupply` RPC handler computes circulating supply with an unchecked subtraction (`total_supply - non_circulating_supply.lamports`) that can underflow due to a TOCTOU race between the non-circulating scan and the capitalization read - ([File: rpc/src/rpc.rs])

### Summary
`JsonRpcRequestProcessor::get_supply` (backing the public, unprivileged `getSupply` JSON-RPC method) computes `non_circulating_supply` first via a full accounts scan, and only *afterwards* reads `bank.capitalization()` to compute `circulating = total_supply - non_circulating_supply.lamports` using plain (non-saturating) `u64` subtraction. This is structurally the same bug class as the reported "stale total value" issue: a total is computed, an unrelated intermediate step is allowed to run (and mutate bank state), and the total is used afterward without being kept in sync with that intermediate step, allowing the check/arithmetic to become inconsistent.

### Finding Description
`get_supply` in the `AccountsScan` RPC implementation does: [1](#0-0) 

1. It grabs `bank` via `self.bank(config.commitment)`. For `processed` (default) or `confirmed` commitment this can be a bank that is not yet frozen — i.e., it may still be actively receiving transactions/being modified by the replay/banking stage while the RPC handler is running.
2. `calculate_non_circulating_supply(&bank)` performs a full scan of every stake-program account (`bank.get_filtered_indexed_accounts` / `bank.get_program_accounts`) and, for each qualifying account, calls `bank.get_balance(pubkey)` to sum lamports: [2](#0-1) 
This scan is not a single atomic snapshot — it walks accounts one-by-one while `bank` (if unfrozen) can continue to be mutated concurrently by consensus/replay threads.
3. Only *after* this scan completes does the handler read `bank.capitalization()` as `total_supply`, and then computes `circulating: total_supply - non_circulating_supply.lamports` — a plain, non-saturating `u64` subtraction: [3](#0-2) 

Because the non-circulating lamport sum and the capitalization figure are captured at two different points in time, with an intervening (and potentially expensive, cluster-wide) accounts scan in between, the two totals are not guaranteed to be mutually consistent, exactly mirroring the reported flaw where `totalValues` becomes stale relative to values computed later after further state-changing processing. If, during the scan window, lamports move into stake accounts that get counted as non-circulating (e.g., delegations, transfers, or rewards booked in the intervening slot(s)) while `capitalization()` reflects an earlier, lower total, `non_circulating_supply.lamports` can exceed `total_supply`, causing the subtraction to underflow.

### Impact Explanation
This is reachable via a single, ordinary, unprivileged `getSupply` JSON-RPC call — no special role is required. The consequence is either:
- A silently wrong (wrapped) `circulating` value being returned to any RPC caller (since Rust release builds wrap `u64` subtraction by default rather than panic), which corrupts the reported supply figures for the entire cluster; or
- If overflow checks happen to be enabled for the build profile, a panic in the JSON-RPC processing thread from a normal, single unprivileged request.

Either outcome falls within the "wrong-slot/fork/account data returned" or "validator process crash from one request" categories called out as acceptable impacts.

### Likelihood Explanation
The race window is proportional to how long `calculate_non_circulating_supply` takes, which requires scanning every stake account on the network — this can be significant on a live mainnet-scale validator, giving a realistic window for the bank's capitalization/account balances to shift while the scan is in flight, particularly when `commitment=processed` (or `confirmed`) is used so the target bank is not frozen. This does not require malicious input, just ordinary chain activity coinciding with the call.

### Recommendation
Capture `total_supply = bank.capitalization()` and perform (or start) the non-circulating scan against the same, single consistent point of reference, and replace the raw subtraction with `total_supply.saturating_sub(non_circulating_supply.lamports)` to avoid underflow regardless of ordering. Ideally, only allow this RPC method against a frozen/confirmed-or-later bank so the two computations are guaranteed to observe the same immutable state.

### Proof of Concept
1. Start a validator/RPC node processing transactions (bank is not frozen for `processed`/`confirmed` commitment).
2. Issue a `getSupply` request (`{"jsonrpc":"2.0","id":1,"method":"getSupply"}`), which triggers `get_supply`: [4](#0-3) 
3. While `calculate_non_circulating_supply` is scanning (a potentially long operation across all stake accounts), have concurrent normal transaction processing shift lamports such that stake-account balances counted as non-circulating grow relative to what `bank.capitalization()` will report when read afterward at line 1133.
4. Observe the resulting `circulating = total_supply - non_circulating_supply.lamports` subtraction underflow (wrapping to a near-`u64::MAX` value in a release build, or panicking if overflow checks are active), corrupting the RPC response for a plain unprivileged query.

Note: I was unable to confirm from the indexed files whether the Agave workspace enables `overflow-checks` in its release profile (no `[profile]` overflow-checks setting was found in the indexed Cargo.toml content), so I cannot definitively state whether the underflow manifests as a panic or as silently wrapped (incorrect) data in this build configuration — this would need to be verified directly against the full `Cargo.toml` files, which may not be fully covered by the current index.

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

**File:** runtime/src/non_circulating_supply.rs (L19-78)
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
```
