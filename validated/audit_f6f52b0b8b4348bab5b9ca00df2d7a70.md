### Title
`getSupply` RPC handler subtracts a live-scanned value from `capitalization` without a checked/saturating operation, allowing an arithmetic-underflow panic - ([File: rpc/src/rpc.rs])

### Summary
`JsonRpcRequestProcessor::get_supply` computes `RpcSupply.circulating` as `total_supply - non_circulating_supply.lamports` using a plain, unchecked `u64` subtraction. `total_supply` is `bank.capitalization()`, an atomic counter read at one point in time, while `non_circulating_supply.lamports` is independently computed moments earlier by scanning and summing live account balances (`calculate_non_circulating_supply`). This is structurally the same pattern as the Derby `blacklistProtocol` bug: a "cached" aggregate total is subtracted by a freshly recomputed value that is not guaranteed to be less-than-or-equal-to it, because the two quantities are read from a mutable, concurrently-updated state at different instants.

### Finding Description
`get_supply` is reachable via the unprivileged `getSupply` JSON-RPC method [1](#0-0) . It calls `self.calculate_non_circulating_supply(&bank)`, which spawns a blocking task that scans all stake-program accounts (and other statically-known non-circulating accounts), summing their *live* lamport balances via `bank.get_balance(pubkey)` [2](#0-1) . After this scan completes, the handler separately reads `bank.capitalization()` [3](#0-2)  and then computes `total_supply - non_circulating_supply.lamports` with a bare `-` operator [4](#0-3) .

When `config.commitment` resolves to a non-frozen "working"/processed bank (`self.bank(config.commitment)`), the bank is still being actively mutated by concurrent transaction processing while the scan runs. Lamports can move into or out of non-circulating accounts, or capitalization itself can change (e.g., due to account creation/closure, burns, or rent/rewards bookkeeping) between the time the non-circulating balances are summed and the time `capitalization()` is subsequently read. Because these two reads are not taken atomically against a single consistent state, it's possible for the freshly-summed `non_circulating_supply.lamports` to exceed the `total_supply` value read afterward — exactly analogous to the reported Derby bug where a live-recomputed protocol balance could exceed the previously cached `savedTotalUnderlying`.

There is a nearly identical, already-hardened version of this exact computation in `rpc_service.rs`'s REST endpoint, which uses `saturating_sub` specifically to avoid this class of issue [5](#0-4) . The JSON-RPC `getSupply` handler in `rpc.rs`, however, was not given the same protection and still uses unchecked subtraction [6](#0-5) .

### Impact Explanation
If the subtraction underflows:
- In a build with overflow checks enabled, this triggers a Rust arithmetic-underflow panic within the async RPC task handling the `getSupply` request, from a single unprivileged JSON-RPC call.
- In a build without overflow checks (typical release profile), the subtraction silently wraps to a value near `u64::MAX`, causing `getSupply` to report a wildly incorrect (astronomically large) "circulating" supply to any caller — a wrong-data-returned condition for a read-only, single-call API.

Either outcome maps to the accepted impact classes: a crash/panic from a single request, or misreporting of state from a query, with no privileged access required.

### Likelihood Explanation
Likelihood is dependent on timing: the race window exists only while the underlying bank is non-frozen (i.e., a "processed"/working bank, or a bank actively finalizing rewards/rebalancing lamports among non-circulating accounts) and only while there is a large enough non-circulating-account scan taking non-trivial time relative to concurrent state mutation. On a live mainnet-class validator processing continuous transaction load and periodic large-scale lamport movements (e.g., epoch rewards distribution touching lockup/stake accounts that are part of the `non_circulating_accounts()` list), this window is realistically reachable by any client repeatedly polling `getSupply` with `commitment: "processed"`, without needing precise timing control—similar to how the original Derby report notes the bug is likely to manifest under normal operating conditions rather than a contrived edge case.

### Recommendation
Change the unchecked subtraction in `get_supply` to a `saturating_sub` (mirroring the pattern already used in `calculate_circulating_supply_async` in `rpc_service.rs`), or better, compute `total_supply` and initiate the non-circulating scan against a single, frozen snapshot to avoid the two values being sampled at different points in a mutable bank's lifetime:

```rust
circulating: total_supply.saturating_sub(non_circulating_supply.lamports),
```

### Proof of Concept
1. Run a validator with a working/processed bank actively receiving transactions.
2. Concurrently: (a) have a client repeatedly issue `getSupply` with `{"commitment": "processed"}`; (b) drive normal transaction load / trigger reward distribution or transfers that alter balances of accounts in the `non_circulating_accounts()` set or affect `bank.capitalization()`, timed to land between the scan performed in `calculate_non_circulating_supply` and the subsequent `bank.capitalization()` read in `get_supply`.
3. Observe that `non_circulating_supply.lamports` (summed from balances read at time T1) can exceed `total_supply` (capitalization read at time T2 > T1), causing the unchecked subtraction at `rpc/src/rpc.rs:1148` to underflow — producing either a panic (overflow-checked build) or a nonsensical, wrapped `circulating` supply value returned to the caller (non-checked build).

### Citations

**File:** rpc/src/rpc.rs (L1121-1132)
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
```

**File:** rpc/src/rpc.rs (L1133-1133)
```rust
        let total_supply = bank.capitalization();
```

**File:** rpc/src/rpc.rs (L1144-1148)
```rust
        Ok(new_response(
            &bank,
            RpcSupply {
                total: total_supply,
                circulating: total_supply - non_circulating_supply.lamports,
```

**File:** runtime/src/non_circulating_supply.rs (L49-78)
```rust
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
