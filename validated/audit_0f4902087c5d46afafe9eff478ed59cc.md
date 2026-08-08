### Title
Hardcoded mainnet-beta-only non-circulating account list used unconditionally on all clusters produces wrong `getSupply`/circulating-supply RPC results - (File: runtime/src/non_circulating_supply.rs)

### Summary
`non_circulating_accounts()` returns a hardcoded, mainnet-beta-specific list of ~80 pubkeys and is consumed unconditionally by `calculate_non_circulating_supply()`, which is invoked from the unprivileged `getSupply` JSON-RPC handler. There is no cluster-type check gating this list, so the same mainnet-only addresses are applied when computing "non-circulating supply" on devnet, testnet, or any custom cluster, producing incorrect supply figures for those networks — the same root-cause pattern as the reported Uniswap adapter bug (a hardcoded address correct only for one deployment/network being applied indiscriminately across all deployments).

### Finding Description
The comment explicitly states the list is "Mainnet-beta accounts that should be considered non-circulating": [1](#0-0) 

`calculate_non_circulating_supply()` unconditionally seeds its working set from this list with no check on `ClusterType` (no `if cluster_type == ClusterType::MainnetBeta` guard anywhere in this function or its call sites): [2](#0-1) 

This mirrors the external report's root cause: a set of addresses that is only valid/meaningful for one specific deployment (there: Ethereum mainnet/Arbitrum/Optimism Uniswap V3 `PositionManager`; here: Solana mainnet-beta's known locked/non-circulating accounts) gets baked in as an unconditional constant and applied identically regardless of which network the code is actually running against.

### Impact Explanation
An RPC node operator running the `getSupply` (or supply-derived endpoints that rely on `NonCirculatingSupply`) on devnet, testnet, or any private/custom cluster gets a wrong-account-data result: pubkeys that are meaningless (or belong to unrelated/nonexistent accounts) on that cluster are still subtracted from circulating supply, and any devnet/testnet account that happens to reuse one of those addresses (or simply the balance lookups against those specific pubkeys) will misreport the non-circulating account set and total. This is a "wrong data returned" class of bug reachable by any unprivileged client issuing a single `getSupply` RPC call — it doesn't crash the validator or affect consensus, but it does silently corrupt a widely-consumed public API response (explorer/tooling circulating-supply figures) on every non-mainnet-beta cluster.

### Likelihood Explanation
Likelihood is high for any RPC-enabled devnet/testnet/custom-cluster node, since `getSupply` is a normal unprivileged RPC method any client can call, and the mainnet-only list is applied with no gating logic at all — the bug fires on every single call on non-mainnet clusters, not just under rare conditions.

### Recommendation
Gate `non_circulating_accounts()` (and `withdraw_authority()`, if similarly hardcoded) behind the bank/cluster's `ClusterType`, returning an empty list (or a per-cluster-appropriate list) when `cluster_type != ClusterType::MainnetBeta`, analogous to the report's recommendation to parameterize the hardcoded address instead of baking in a single deployment's constants.

### Proof of Concept
Not independently executable from static review; the mismatch is provable structurally: `non_circulating_accounts()` is documented as mainnet-beta-only data [1](#0-0)  yet `calculate_non_circulating_supply()` consumes it without any `ClusterType` check [2](#0-1) . Running `getSupply` against a devnet/testnet validator (which reuses this same function per the `rpc/src/rpc.rs` and `rpc/src/rpc_service.rs` call sites found in the codebase) will apply the mainnet address set verbatim, which could not be fully traced end-to-end in this review since the exact `get_supply` RPC handler body in `rpc/src/rpc.rs` was not retrievable through the available search tools — a Devin session with full file access would be needed to confirm the exact call chain and absence of any cluster gating at the RPC layer itself.

### Citations

**File:** runtime/src/non_circulating_supply.rs (L19-26)
```rust
pub fn calculate_non_circulating_supply(bank: &Bank) -> ScanResult<NonCirculatingSupply> {
    debug!("Updating Bank supply, epoch: {}", bank.epoch());
    let mut non_circulating_accounts_set: HashSet<Pubkey> = HashSet::new();

    for key in non_circulating_accounts() {
        non_circulating_accounts_set.insert(key);
    }
    let withdraw_authority_list = withdraw_authority();
```

**File:** runtime/src/non_circulating_supply.rs (L81-84)
```rust
// Mainnet-beta accounts that should be considered non-circulating
pub fn non_circulating_accounts() -> Vec<Pubkey> {
    [
        solana_pubkey::pubkey!("9huDUZfxoJ7wGMTffUE7vh1xePqef7gyrLJu9NApncqA"),
```
