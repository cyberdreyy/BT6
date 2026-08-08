### Title
`get_supply()` performs an unchecked subtraction that can underflow/panic on a single unprivileged RPC call - (File: rpc/src/rpc.rs)

### Summary
`JsonRpcRequestProcessor::get_supply()`, which backs the public `getSupply` JSON-RPC method, computes `circulating` as a raw, unchecked subtraction of two independently derived `u64` values instead of using `checked_sub`/`saturating_sub` as is done for the equivalent value elsewhere in the same codebase.

### Finding Description
`get_supply()` computes:

```rust
let total_supply = bank.capitalization();
...
Ok(new_response(
    &bank,
    RpcSupply {
        total: total_supply,
        circulating: total_supply - non_circulating_supply.lamports,
        non_circulating: non_circulating_supply.lamports,
        non_circulating_accounts,
    },
))
``` [1](#0-0) 

`total_supply` comes from `bank.capitalization()` — an incrementally-tracked counter of total lamports — while `non_circulating_supply.lamports` is computed independently, by summing account balances for a hardcoded list of accounts plus any locked-up/authority-restricted stake accounts discovered via a live accounts scan: [2](#0-1) 

This is structurally identical to the reported Sandclock bug: `totalUnderlying() - totalSponsored` in `totalUnderlyingMinusSponsored()` blindly subtracted two values that are tracked through separate code paths, and the contract itself elsewhere acknowledged that `sponsorAmount` can exceed `totalUnderlying()`. In `get_supply()`, `total_supply` (`capitalization`) and `non_circulating_supply.lamports` are likewise tracked through entirely separate mechanisms (an incremental counter vs. a live scan-and-sum), with no invariant enforced anywhere in this code path that the scanned subset sum can never exceed capitalization. If these two ever diverge — e.g., through the same class of accounting drift that historically has affected `capitalization` tracking, or from an unaccounted lamport source added to the non-circulating account list without a matching capitalization update — the subtraction underflows.

Notably, the codebase demonstrates awareness of exactly this hazard for the *same* computation and defensively uses `saturating_sub` in the HTTP `/v0/circulating-supply` handler: [3](#0-2) 

but the JSON-RPC `getSupply` handler (`rpc.rs`) was not given the same protection, leaving the raw `-` operator in place.

### Impact Explanation
`getSupply` is a standard, unprivileged JSON-RPC method reachable by any client with RPC access — no special role is required. If the subtraction underflows:
- In a debug/overflow-checked build, this panics inside the JSON-RPC request-handling thread pool at `rpc.rs:1148`, crashing/aborting the request-processing task and returning malformed error state to any waiting caller — a validator-process fault triggered by a single unprivileged query.
- In a release build without overflow checks, the subtraction silently wraps, producing a `circulating` value close to `u64::MAX` — i.e., wildly wrong data returned to every RPC consumer for a widely-relied-upon informational RPC.

Both outcomes match the "wrong data returned" / "crash from one request" categories that are in scope for this analysis.

### Likelihood Explanation
Likelihood depends on `total_supply` (`bank.capitalization()`) and the scanned `non_circulating_supply.lamports` staying consistent, which is not enforced by any code on this path — the same lack of an explicit invariant that caused the original Sandclock issue. I was not able to confirm from the available code whether `overflow-checks` is force-enabled in this project's release profile (no `[profile.release]`/`overflow-checks` setting was found in `Cargo.toml`), so I cannot be certain whether a real underflow event manifests as a panic or as silent wraparound in production builds; either way the missing defensive check is the same class of bug already fixed for the sibling `/v0/circulating-supply` code path.

### Recommendation
Mirror the defensive pattern already used in `calculate_circulating_supply_async` (`rpc/src/rpc_service.rs`) inside `get_supply()`:

```rust
circulating: total_supply.saturating_sub(non_circulating_supply.lamports),
```

so that the JSON-RPC `getSupply` handler cannot underflow/panic or return a wrapped value regardless of how `capitalization` and the non-circulating scan happen to relate at query time.

### Proof of Concept
1. Call the standard `getSupply` JSON-RPC method exposed by `get_supply()` at `rpc/src/rpc.rs:1121-1153`.
2. The handler computes `total_supply = bank.capitalization()` and independently computes `non_circulating_supply.lamports` via `calculate_non_circulating_supply()` (a live accounts scan) at `runtime/src/non_circulating_supply.rs:19-79`.
3. Because these two values are derived through unrelated mechanisms with no cross-check, any scenario in which the scanned non-circulating lamports exceed the tracked capitalization causes `total_supply - non_circulating_supply.lamports` at `rpc/src/rpc.rs:1148` to underflow — panicking the handling task under overflow-checked builds, or silently returning a near-`u64::MAX` "circulating supply" figure under release-mode wrapping arithmetic — from a single unprivileged `getSupply` request.

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
