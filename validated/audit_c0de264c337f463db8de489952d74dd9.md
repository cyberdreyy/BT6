### Title
Unbounded per-request storage scan in `calculate_non_circulating_supply` via attacker-inflated stake-owned account set - ([File: runtime/src/non_circulating_supply.rs])

### Summary
`calculate_non_circulating_supply` falls back to `bank.get_program_accounts(&stake::program::id())` — an unfiltered, full-storage scan — whenever the bank's `AccountIndex::ProgramId` secondary index is not enabled, which is the default configuration for most validators/RPC nodes. An unprivileged client can inflate the number of accounts owned by the stake program (since `system_program::create_account` lets any payer set an arbitrary `owner` field, including `stake::program::id()`, without stake-program cooperation) and thereby make every subsequent `getSupply` (and `getStakeMinimumDelegation`-adjacent) RPC call more expensive.

### Finding Description
`calculate_non_circulating_supply` checks whether the bank's `accounts_db.account_indexes` contains `AccountIndex::ProgramId`; if not, it calls the unindexed `bank.get_program_accounts(&stake::program::id())?` fallback path [1](#0-0) . This function is invoked by the JSON-RPC `getSupply` handler in `rpc/src/rpc.rs` on every client-triggered request, and by `rpc/src/rpc_service.rs` in the periodic supply-cache path.

An attacker only needs to submit ordinary transactions (`system_program::create_account`) that assign `owner = stake::program::id()` to many small accounts. `system_program::create_account` does not require the target program's participation to set the `owner` field — this is a well-known Solana primitive used for legitimate account creation before program initialization, and it's explicitly called out as attacker-controllable in the prompt. This does not require the account to hold valid stake-program-formatted data; `calculate_non_circulating_supply`'s deserialization simply falls back to `unwrap_or_default()` on failure, so garbage/junk data is fine [2](#0-1) .

Once the secondary program-id index is absent, every one of these junk accounts becomes part of the O(N) `get_program_accounts` full-storage scan that must be traversed on each `getSupply` call, where N is entirely attacker-controlled and grows unbounded with the number of low-cost stake-owned accounts the attacker creates.

### Impact Explanation
This matches the "unbounded cost for a single low-rate call" bounty category: a client issuing `getSupply` at the RPC-call-rate limit still forces the validator to perform O(N) work, with N scaling with attacker-supplied on-chain state, not with any RPC parameter that a length/commitment/quota check could reject. This can materially degrade RPC responsiveness/availability for a bounded, low-frequency legitimate query, without needing multiple clients or exceeding the one-call-per-`CLUSTER_SLOT_TIME_TARGET/2` throttle.

### Likelihood Explanation
Feasibility depends entirely on whether `AccountIndex::ProgramId` is enabled on the target node. Enabling this index (via `--account-index program-id`) is an opt-in validator/RPC-operator configuration choice; it is not the Agave default. Since the question stipulates "a bank that lacks `AccountIndex::ProgramId`", and this is the common configuration for most RPC-serving nodes that don't specifically build the program-id index (index-building carries its own memory/CPU overhead operators often avoid), the precondition is realistic and not rare. Creating a large number of small stake-owned accounts costs the attacker only the standard rent-exempt-minimum lamports and transaction fees per account, and this cost scales linearly with attacker effort/capital, making the attack repeatable and incremental.

### Recommendation
- Cap the number of accounts / total bytes scanned by `bank.get_program_accounts` when used from `calculate_non_circulating_supply`'s non-indexed fallback, or short-circuit/deny the fallback path entirely when the secondary index is absent, returning a well-defined error/degraded response instead of scanning the whole account store.
- Alternatively (and preferably), cache `getSupply`/non-circulating-supply results with a TTL so repeated attacker-triggered RPC calls do not each re-trigger the expensive scan, decoupling the per-call cost from attacker-created account count.
- Consider making `AccountIndex::ProgramId` mandatory for JSON-RPC-serving nodes, or documenting/enforcing that `getSupply` requires it, since the unindexed fallback's cost model is fundamentally unsafe to expose to arbitrary untrusted callers.

### Proof of Concept
Integration/benchmark test plan (Rust, in `runtime/src/non_circulating_supply.rs` or a new bench):
```rust
#[test]
fn test_calculate_non_circulating_supply_cost_scales_with_attacker_accounts() {
    // Build a bank WITHOUT AccountIndex::ProgramId (default AccountSecondaryIndexes::default()).
    // For N in [1_000, 10_000, 100_000]:
    //   1. Create N accounts owned by stake::program::id() with minimal/garbage data
    //      (simulating attacker use of system_program::create_account with owner=stake::program::id()).
    //   2. bank.store_account(&pubkey, &junk_account) for each.
    //   3. Measure wall-clock time (and/or allocation count) of calculate_non_circulating_supply(&bank).
    // Assert: elapsed_time(N=100_000) / elapsed_time(N=1_000) grows roughly linearly with N
    //         (i.e., no upper bound/cap independent of attacker-controlled account count),
    //         demonstrating the absence of a bounded-cost guarantee for the getSupply RPC path.
}
```
Expected result: measured cost (time and/or bytes scanned via `get_program_accounts`) increases proportionally with the attacker-created account count N, confirming the fallback path in [3](#0-2)  has no cap, in contrast to the indexed path which is bounded by the actual number of legitimate stake accounts tracked in the `IndexKey::ProgramId` index.

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

**File:** runtime/src/non_circulating_supply.rs (L49-52)
```rust
    for (pubkey, account) in stake_accounts.iter() {
        let stake_account = account
            .deserialize_data::<StakeStateV2>()
            .unwrap_or_default();
```
