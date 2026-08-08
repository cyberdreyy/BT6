### Title
`calculate_non_circulating_supply` misclassifies attacker-controlled stake accounts as non-circulating on any cluster by matching hardcoded mainnet-beta pubkeys - ([File: runtime/src/non_circulating_supply.rs])

### Summary
`calculate_non_circulating_supply` classifies a stake account as non-circulating if its lockup is active *or* its `authorized.withdrawer` matches one of the hardcoded pubkeys returned by `withdraw_authority()`, with no check on `ClusterType`. Because `authorized.withdrawer` is just data set at `Initialize`/`Authorize` time (no signature or ownership of that pubkey's private key is required), any unprivileged user can set their own stake account's withdrawer field to one of these public, hardcoded addresses on devnet/testnet and have their liquid, unlocked stake reported as "non-circulating" by `getSupply`/`getLargestAccounts`.

### Finding Description
`calculate_non_circulating_supply` iterates all stake-program accounts and, for `StakeStateV2::Initialized`/`StakeStateV2::Stake`, adds the pubkey to `non_circulating_accounts_set` when: [1](#0-0) 
i.e. `meta.lockup.is_in_force(&clock, None) || withdraw_authority_list.contains(&meta.authorized.withdrawer)`. The `withdraw_authority_list` comes from `withdraw_authority()`, a static list explicitly documented as "Withdraw authority for autostaked accounts on mainnet-beta": [2](#0-1) 
There is no `bank.cluster_type()` (or similar) gating anywhere in `calculate_non_circulating_supply`, so this list is applied identically regardless of whether the bank is running mainnet-beta, testnet, or devnet.

Setting `authorized.withdrawer` on a stake account is a normal, permissionless operation performed via the Stake program's `Initialize` (or `Authorize`) instruction; it only requires the caller to specify a pubkey value in the instruction data — it does not require possessing the private key for that pubkey, since it merely designates who *would* need to sign future withdraw operations. Consequently, an attacker with no special privileges can create a stake account, initialize it with `Authorized { withdrawer: <hardcoded_mainnet_pubkey>, .. }`, leave `lockup` unset (or expired), and this satisfies the OR-condition and gets it added to `non_circulating_accounts_set`.

### Impact Explanation
This causes `getSupply` and `getLargestAccounts` (both consuming `calculate_non_circulating_supply` results via `rpc/src/rpc.rs`) to misreport the attacker's fully liquid, non-locked stake as non-circulating supply on testnet/devnet — a wrong-data-returned condition per the bounty categories ("account data returned that does not belong / is not derived from the requested account correctly"). This is a data-integrity/misreporting bug in a public RPC method reachable with a single unprivileged transaction plus a subsequent single RPC call, not a crash or consensus issue, so impact is scoped to RPC data-accuracy for supply-reporting endpoints on non-mainnet clusters.

### Likelihood Explanation
High feasibility and full repeatability: the hardcoded pubkeys are public (visible in this very source file), the stake `Initialize`/`Authorize` instruction accepts any pubkey as `withdrawer` without requiring its private key, and the resulting stake account is then always scanned by `calculate_non_circulating_supply` on any cluster type. No special conditions, timing, or validator control are required — only a single stake-account creation transaction on devnet/testnet.

### Recommendation
Gate the `withdraw_authority()`/`non_circulating_accounts()` static-list checks on `bank.cluster_type() == ClusterType::MainnetBeta` (or otherwise skip them for non-mainnet clusters), so that on testnet/devnet only the lockup-based (`meta.lockup.is_in_force`) classification applies.

### Proof of Concept
Integration test outline (extending the existing test module in `runtime/src/non_circulating_supply.rs`):
1. Build a `GenesisConfig` with `cluster_type: ClusterType::Devnet` (or `Testnet`).
2. Insert a stake-program account with `StakeStateV2::Initialized(Meta { authorized: Authorized { staker: Pubkey::default(), withdrawer: withdraw_authority()[0] }, lockup: Lockup::default(), .. })` (no active lockup).
3. Call `calculate_non_circulating_supply(&bank)`.
4. Assert (expected to fail today, demonstrating the bug): the account pubkey should NOT be in `non_circulating_supply.accounts` and its lamports should NOT be counted in `non_circulating_supply.lamports`, since there is no lockup on this non-mainnet cluster; currently the implementation includes it regardless of `ClusterType`.

### Citations

**File:** runtime/src/non_circulating_supply.rs (L53-67)
```rust
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
```

**File:** runtime/src/non_circulating_supply.rs (L302-304)
```rust
// Withdraw authority for autostaked accounts on mainnet-beta
pub fn withdraw_authority() -> Vec<Pubkey> {
    [
```
