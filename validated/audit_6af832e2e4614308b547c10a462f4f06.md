### Title
Unbounded iteration over the network-wide vote-accounts set in `getVoteAccounts` allows unbounded per-call cost via cheap, unprivileged vote-account creation - (File: `rpc/src/rpc.rs`)

### Summary
The external report's bug class is a linear-complexity function (`verifyDoubleSigning`) whose input array can be grown without bound by a cheap, repeatable, unprivileged action (`updateDelegation`), so that a single subsequent call becomes arbitrarily expensive (denial of service / gas griefing). The equivalent pattern exists in Agave's JSON-RPC `getVoteAccounts` handler: it iterates over the *entire* `bank.vote_accounts()` map with no upper bound, and any unprivileged user can grow that map indefinitely by creating vote accounts (which require no stake and no validator/operator privilege), turning a single `getVoteAccounts` request into unbounded CPU/memory/JSON-serialization work on the RPC node.

### Finding Description
`JsonRpcRequestProcessor::get_vote_accounts` retrieves the full vote-accounts hashmap and filters/maps over every entry with no limit: [1](#0-0) 

```
let vote_accounts = bank.vote_accounts();
...
let (current_vote_accounts, delinquent_vote_accounts): (...) = vote_accounts
    .iter()
    .filter_map(|(vote_pubkey, (activated_stake, account))| { ... })
    .partition(...)
``` [2](#0-1) 

`bank.vote_accounts()` returns every vote account currently tracked by the stakes cache, regardless of whether it holds any delegated stake: [3](#0-2) 

The unit test `test_get_vote_accounts` confirms that a zero-stake vote account created by *any* unprivileged keypair (no operator/validator role required) is added to and remains in this map: [4](#0-3) 

Vote-account creation via the Vote program requires only rent-exempt lamports and a `VoteInit`; it is not gated by validator status, stake, or any registry, and there is no cap anywhere on how many such accounts can exist or on how many entries `get_vote_accounts` will process/serialize. The only bound applied is per-account (`MAX_RPC_VOTE_ACCOUNT_INFO_EPOCH_CREDITS_HISTORY` truncates the epoch-credits history of each entry), not on the number of accounts returned: [5](#0-4) 

This is directly analogous to `delegatedValidators` in the report: a cheap, unprivileged, repeatable action (`updateDelegation` ↔ creating a vote account / keeping it voting so it stays in the "current" partition) grows an array with no upper limit, and a downstream function with O(N) complexity (`verifyDoubleSigning` ↔ `get_vote_accounts`) must process the entire array on every call.

### Impact Explanation
A single `getVoteAccounts` RPC call has cost proportional to the total number of vote accounts in `bank.vote_accounts()`, which is attacker-controllable and unbounded. An attacker can cheaply create a very large number of vote accounts (only rent-exempt-minimum lamports and no stake are required) to inflate this map, causing every `getVoteAccounts` call on any RPC node serving that bank state to consume excessive CPU (iterating/filtering/partitioning) and memory (building and JSON-serializing a correspondingly large response). This matches the "unbounded cost for a single low-rate call" acceptance criterion — a low-rate query becomes disproportionately expensive due to unbounded growth of a shared, iteration-based data structure, potentially degrading or crashing the RPC service for legitimate clients.

### Likelihood Explanation
Likelihood is moderate-to-high: creating a vote account requires no special permission — any funded keypair can invoke the Vote program's initialize instruction. While each account has a rent cost, an attacker with a moderate budget (or by not closing accounts over time) can accumulate a large number of vote accounts, since there is no protocol-level cap on the number of vote accounts nor any pagination/limit on `getVoteAccounts`.

### Recommendation
- Add an explicit, enforced limit on the number of entries that `get_vote_accounts` will process/return per call (e.g., cap total accounts scanned, or require pagination), independent of stake.
- Consider bounding the size of `bank.vote_accounts()`/the stakes cache with respect to zero-stake or long-inactive vote accounts, or excluding zero-stake accounts from the "current" partition by default (not just from "delinquent").
- Document and enforce a maximum response size / iteration budget for this RPC method similar to `byte_limit_for_scans` used for indexed account scans.

### Proof of Concept
1. Using an unprivileged, funded keypair, repeatedly submit `VoteInstruction::initialize_account` (or equivalent) transactions to create N vote accounts with zero delegated stake, each requiring only rent-exempt lamports (no validator role, no stake needed) — mirroring `rpc.store_vote_account` in the existing test: [6](#0-5) 
2. Periodically submit cheap `tower_sync` vote transactions from each account so they remain within the "current" (non-delinquent) partition window used by `get_vote_accounts`'s partition logic: [7](#0-6) 
3. Issue a single `getVoteAccounts` JSON-RPC request. The handler at `rpc/src/rpc.rs:1155-1246` must iterate, filter, and serialize all N accounts with no upper bound, causing CPU/memory cost proportional to attacker-controlled N for that one call.

### Citations

**File:** rpc/src/rpc.rs (L1167-1230)
```rust
        let bank = self.bank(config.commitment);
        let commission_rate_in_basis_points = bank
            .feature_set
            .is_active(&agave_feature_set::commission_rate_in_basis_points::id());
        let vote_accounts = bank.vote_accounts();
        let epoch_vote_accounts = bank
            .epoch_vote_accounts(bank.get_epoch_and_slot_index(bank.slot()).0)
            .ok_or_else(Error::invalid_request)?;
        let delinquent_validator_slot_distance = config
            .delinquent_slot_distance
            .unwrap_or(DELINQUENT_VALIDATOR_SLOT_DISTANCE);
        let (current_vote_accounts, delinquent_vote_accounts): (
            Vec<RpcVoteAccountInfo>,
            Vec<RpcVoteAccountInfo>,
        ) = vote_accounts
            .iter()
            .filter_map(|(vote_pubkey, (activated_stake, account))| {
                if let Some(filter_by_vote_pubkey) = filter_by_vote_pubkey
                    && *vote_pubkey != filter_by_vote_pubkey
                {
                    return None;
                }

                let vote_state_view = account.vote_state_view();
                let last_vote = vote_state_view.last_voted_slot().unwrap_or(0);
                let num_epoch_credits = vote_state_view.num_epoch_credits();
                let epoch_credits = vote_state_view
                    .epoch_credits_iter()
                    .skip(
                        num_epoch_credits
                            .saturating_sub(MAX_RPC_VOTE_ACCOUNT_INFO_EPOCH_CREDITS_HISTORY),
                    )
                    .map(Into::into)
                    .collect();

                Some(RpcVoteAccountInfo {
                    vote_pubkey: vote_pubkey.to_string(),
                    node_pubkey: vote_state_view.node_pubkey().to_string(),
                    activated_stake: *activated_stake,
                    commission: if commission_rate_in_basis_points {
                        // Derive percent from native bps, clamping to u8::MAX.
                        let bps = vote_state_view.inflation_rewards_commission();
                        bps.div_ceil(100).min(u8::MAX as u16) as u8
                    } else {
                        vote_state_view.commission()
                    },
                    inflation_rewards_commission_bps: Some(if commission_rate_in_basis_points {
                        vote_state_view.inflation_rewards_commission()
                    } else {
                        vote_state_view.commission() as u16 * 100
                    }),
                    root_slot: vote_state_view.root_slot().unwrap_or(0),
                    epoch_credits,
                    epoch_vote_account: epoch_vote_accounts.contains_key(vote_pubkey),
                    last_vote,
                })
            })
            .partition(|vote_account_info| {
                if bank.slot() >= delinquent_validator_slot_distance {
                    vote_account_info.last_vote > bank.slot() - delinquent_validator_slot_distance
                } else {
                    vote_account_info.last_vote > 0
                }
            });
```

**File:** rpc/src/rpc.rs (L7772-7787)
```rust
        assert_eq!(bank.vote_accounts().len(), 1);

        // Create a vote account with no stake.
        let alice_vote_keypair = Keypair::new();
        let alice_vote_state = VoteStateV4::new_with_defaults(
            &alice_vote_keypair.pubkey(),
            &VoteInit {
                node_pubkey: mint_keypair.pubkey(),
                authorized_voter: alice_vote_keypair.pubkey(),
                authorized_withdrawer: alice_vote_keypair.pubkey(),
                commission: 0,
            },
            &bank.get_sysvar_cache_for_tests().get_clock().unwrap(),
        );
        rpc.store_vote_account(&alice_vote_keypair.pubkey(), alice_vote_state);
        assert_eq!(bank.vote_accounts().len(), 2);
```

**File:** runtime/src/bank.rs (L5794-5799)
```rust
    /// current vote accounts for this bank along with the stake
    ///   attributed to each account
    pub fn vote_accounts(&self) -> Arc<VoteAccountsHashMap> {
        let stakes = self.stakes_cache.stakes();
        Arc::from(stakes.vote_accounts())
    }
```
