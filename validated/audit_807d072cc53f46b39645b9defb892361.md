## Finding

### Title
Unbounded iteration over all vote accounts in `getVoteAccounts` RPC handler causes unbounded per-call cost - (File: `rpc/src/rpc.rs`)

### Summary
The `getVoteAccounts` JSON-RPC method iterates over every vote account known to the bank with no pagination, offset, or count limit, unlike sibling RPC handlers (`getMultipleAccounts`, `getInflationReward`, `getSignatureStatuses`, `simulateTransaction` accounts) which all enforce explicit maximum-item constants. Since vote accounts can be created permissionlessly by any funded account for the cost of the vote-account rent-exempt minimum, the size of this array is attacker-influenceable and grows unboundedly over time, mirroring the reported `massUpdatePools()` gas-overflow pattern where an unbounded, externally-growable array is iterated in full on every call.

### Finding Description
`JsonRpcRequestProcessor::get_vote_accounts` retrieves the full set of vote accounts via `bank.vote_accounts()` and then does a single unbounded `.iter().filter_map(...)` pass over the entire collection to build `current`/`delinquent` responses: [1](#0-0) 

There is no limit constant analogous to `MAX_MULTIPLE_ACCOUNTS`, `MAX_GET_INFLATION_REWARD_ADDRESSES`, or `MAX_GET_SIGNATURE_STATUSES_QUERY_ITEMS` applied to the number of vote accounts processed: [2](#0-1) 

Other similarly account-scanning RPC entry points enforce bounds before doing work, e.g. `get_multiple_accounts` rejects requests over `MAX_MULTIPLE_ACCOUNTS`: [3](#0-2) 
and `get_inflation_reward` rejects requests over `MAX_GET_INFLATION_REWARD_ADDRESSES`: [4](#0-3) 

`getVoteAccounts` has no such check, and per vote account the work includes decoding `vote_state_view()`, iterating `epoch_credits_iter()`, and building an `RpcVoteAccountInfo`: [5](#0-4) 

The number of vote accounts on a live cluster is not bounded by protocol/consensus rules; any funded account can create a new vote account paying only the vote-account rent-exempt minimum balance, so the set that `getVoteAccounts` must scan is attacker-influenceable and grows over time, similar to how `massUpdatePools()`'s `pools` array grows without an enforced cap.

### Impact Explanation
A single `getVoteAccounts` call against an API node forces a full, unbounded scan/decode of all live vote accounts, with per-item cost (vote-state decoding, epoch-credits slicing) proportional to the total count. As the number of vote accounts on the network grows (which any user can cheaply inflate by creating many low-stake or zero-activity vote accounts), the CPU and response-size cost of a single call grows without bound, degrading or potentially stalling the RPC-serving thread handling that request for a low, one-call-per-request rate — consistent with "unbounded cost for a single low-rate call."

### Likelihood Explanation
Likelihood is moderate: creating vote accounts is permissionless and inexpensive (rent-exempt minimum only), and `getVoteAccounts` is a commonly exposed, unauthenticated JSON-RPC method on public API nodes. No special privileges, multiple calls, or additional infrastructure are required to grow the underlying data set or to trigger the expensive call.

### Recommendation
Add pagination/limit parameters (e.g., `offset`/`limit`, or a hard cap similar to `MAX_MULTIPLE_ACCOUNTS`) to `get_vote_accounts`, or otherwise document/enforce a bound on iterated vote-account count, mirroring the guard rails already present in `get_multiple_accounts` and `get_inflation_reward`.

### Proof of Concept
1. On a test cluster, programmatically create N (e.g., 100,000) minimally-funded vote accounts (only the vote-account rent-exempt minimum balance is required per account).
2. Call `getVoteAccounts` against an RPC node serving that bank.
3. Observe that `get_vote_accounts` in `rpc/src/rpc.rs` (lines 1155–1246) performs a full `O(N)` iterate-and-decode pass with no limit, and that request latency/CPU cost scales linearly with the attacker-created vote-account count, unlike `getMultipleAccounts`/`getInflationReward` which reject oversized requests up front. [6](#0-5)

### Citations

**File:** rpc/src/rpc.rs (L1155-1183)
```rust
    fn get_vote_accounts(
        &self,
        config: Option<RpcGetVoteAccountsConfig>,
    ) -> Result<RpcVoteAccountStatus> {
        let config = config.unwrap_or_default();

        let filter_by_vote_pubkey = if let Some(ref vote_pubkey) = config.vote_pubkey {
            Some(verify_pubkey(vote_pubkey)?)
        } else {
            None
        };

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
```

**File:** rpc/src/rpc.rs (L1190-1222)
```rust
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
```

**File:** rpc/src/rpc.rs (L3305-3313)
```rust
                let max_multiple_accounts = meta
                    .config
                    .max_multiple_accounts
                    .unwrap_or(MAX_MULTIPLE_ACCOUNTS);
                if pubkey_strs.len() > max_multiple_accounts {
                    return Err(Error::invalid_params(format!(
                        "Too many inputs provided; max {max_multiple_accounts}"
                    )));
                }
```

**File:** rpc/src/rpc.rs (L4282-4286)
```rust
            if address_strs.len() > MAX_GET_INFLATION_REWARD_ADDRESSES {
                return Box::pin(future::err(Error::invalid_params(format!(
                    "Too many inputs provided; max {MAX_GET_INFLATION_REWARD_ADDRESSES}"
                ))));
            }
```

**File:** rpc-client-types/src/request.rs (L152-160)
```rust
pub const MAX_GET_SIGNATURE_STATUSES_QUERY_ITEMS: usize = 256;
pub const MAX_GET_CONFIRMED_SIGNATURES_FOR_ADDRESS_SLOT_RANGE: u64 = 10_000;
pub const MAX_GET_CONFIRMED_BLOCKS_RANGE: u64 = 500_000;
pub const MAX_GET_CONFIRMED_SIGNATURES_FOR_ADDRESS2_LIMIT: usize = 1_000;
pub const MAX_MULTIPLE_ACCOUNTS: usize = 100;
pub const MAX_GET_INFLATION_REWARD_ADDRESSES: usize = 32;
pub const NUM_LARGEST_ACCOUNTS: usize = 20;
pub const MAX_GET_PROGRAM_ACCOUNT_FILTERS: usize = 4;
pub const MAX_GET_SLOT_LEADERS: usize = 5000;
```
