### Title
`calculate_non_circulating_supply` never applies the operator-configured accounts-index scan-results byte limit, making a single `getSupply` RPC call able to scan an unbounded number of stake accounts even when the `ProgramId` secondary index is enabled - (`runtime/src/non_circulating_supply.rs`)

### Summary
`calculate_non_circulating_supply`, which backs the `getSupply` JSON-RPC method, calls `bank.get_filtered_indexed_accounts(&IndexKey::ProgramId(stake::program::id()), ..., None)` with a hard-coded `None` byte limit, instead of forwarding the operator-configured `scan_results_limit_bytes` that `JsonRpcRequestProcessor::get_filtered_indexed_accounts` passes for ordinary `getProgramAccounts` calls. As a result, even when a validator has the `ProgramId` secondary index enabled and has configured `--accounts-index-scan-results-limit-mb` to bound scan cost, that protection is silently bypassed for `getSupply`, letting an attacker who creates a very large number of on-chain stake accounts force one `getSupply` call to load and deserialize all of them.

### Finding Description
`calculate_non_circulating_supply` (`runtime/src/non_circulating_supply.rs:29-47`) chooses between two account-retrieval paths based on whether the `ProgramId` index is configured: [1](#0-0) 

- When the index is configured, it calls `bank.get_filtered_indexed_accounts(&IndexKey::ProgramId(stake::program::id()), |account| ..., None)`, explicitly passing `None` as `byte_limit_for_scan`.
- Compare this to the equivalent RPC path used for ordinary `getProgramAccounts` in `JsonRpcRequestProcessor::get_filtered_indexed_accounts` (`rpc/src/rpc.rs:309-341`), which forwards `self.config.scan_results_limit_bytes` (populated from the operator flag `--accounts-index-scan-results-limit-mb`, see `validator/src/commands/run/args/json_rpc_config.rs:58-64`) so that `load_by_index_key_with_filter` can abort the scan once results exceed the configured size (`runtime/src/bank/tests.rs:3470-3502` demonstrates this abort behavior via `ScanError`).

Because `calculate_non_circulating_supply` is called only from `JsonRpcRequestProcessor::calculate_non_circulating_supply` (`rpc/src/rpc.rs:298-307`), which just forwards to the runtime function without any wrapping limit, the byte-limit protection that exists for `getProgramAccounts` is never exercised for `getSupply`. An attacker who creates a large number of `StakeStateV2::Initialized`/`Stake` accounts via ordinary stake-program instructions can thus force `getSupply` to enumerate and deserialize the entire population of stake accounts on the index-key path, with per-request cost scaling with total on-chain stake-account count rather than any explicit, enforced cap — even on nodes that have taken the precaution of configuring a scan byte limit and enabling the secondary index.

Note: the alternate, non-indexed fallback (`bank.get_program_accounts(&stake::program::id())`, line 46) is the classic "unfiltered `getProgramAccounts` without secondary indexes" case, which is explicitly excluded by the audit's SECURITY.md scope rules. The finding above is scoped specifically to the indexed path, where the exclusion does not apply because the limiting mechanism exists in the codebase but is not wired into this code path.

### Impact Explanation
A single low-rate `getSupply` request can cause the RPC-handling thread (via `spawn_blocking`) to iterate, load, and deserialize an attacker-controlled number of stake accounts, consuming CPU and memory proportional to attacker-authored on-chain state rather than any bounded/administrator-enforced limit. This matches the "unbounded cost for a single low-rate call" impact category (node CPU/memory degradation from a single request).

### Likelihood Explanation
Feasible and repeatable: creating `StakeStateV2::Initialized` accounts is an ordinary, unprivileged stake-program operation requiring only minimal lamports per account, and can be done across many slots at ≤1 tx per `CLUSTER_SLOT_TIME_TARGET/2`. The precondition is that the validator has the `ProgramId` secondary index enabled (a common RPC deployment configuration to serve `getProgramAccounts`-family calls efficiently); no operator misconfiguration is required beyond that intended, common setup — the bug is that the existing byte-limit safeguard for that setup is not applied to this particular code path.

### Recommendation
Thread a byte-limit parameter through `calculate_non_circulating_supply` (e.g., accept `scan_results_limit_bytes: Option<usize>` from `JsonRpcConfig`) and pass it into `bank.get_filtered_indexed_accounts` instead of hard-coding `None`, mirroring the behavior already used for the general `getProgramAccounts` RPC path in `rpc/src/rpc.rs`. Consider also applying the same limit (or a dedicated one) to the non-indexed `bank.get_program_accounts` fallback branch.

### Proof of Concept
Rust integration test plan (extending the existing `test_calculate_non_circulating_supply` in `runtime/src/non_circulating_supply.rs:450-546`):
1. Build a `Bank` with the `ProgramId` secondary index enabled and `scan_results_limit_bytes`-equivalent semantics wired through a modified `calculate_non_circulating_supply(bank, limit)` signature.
2. Insert an increasing number `N` (e.g., 10; 10,000; 100,000) of `StakeStateV2::Initialized` accounts owned by the stake program, each with minimal lamports/space.
3. Call `calculate_non_circulating_supply` and measure wall-time/allocations for each `N`.
4. Assert that: (a) time/memory scales roughly linearly with `N`, and (b) no `ScanError`/abort occurs regardless of `N`, proving the absence of any enforced cap on this path — in contrast to `test_get_filtered_indexed_accounts_limit_exceeded` (`runtime/src/bank/tests.rs:3470-3502`), which shows the same underlying `get_filtered_indexed_accounts` API does support aborting given a limit that `calculate_non_circulating_supply` simply never supplies.

### Citations

**File:** runtime/src/non_circulating_supply.rs (L29-47)
```rust
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
```
