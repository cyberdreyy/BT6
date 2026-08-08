This confirms the analog: `bank.vote_accounts()` returns the `StakesCache`'s full `VoteAccountsHashMap`, which caches **every** vote-program-owned account with nonzero lamports — not just staked ones — as shown by `check_and_store` inserting any correctly-sized, initialized vote account regardless of stake.### Title
Unbounded iteration over all vote accounts in `getVoteAccounts` RPC handler enables cheap, attacker-inflatable per-call cost - (File: `rpc/src/rpc.rs`)

### Summary
The `getVoteAccounts` JSON-RPC method (`JsonRpcRequestProcessor::get_vote_accounts`) iterates over the *entire* set of vote-program-owned accounts cached in the bank's `StakesCache`, with no pagination, no result cap, and (unless the caller supplies `votePubkey`) no filtering. Because creating a vote account is cheap and permissionless (it does not require any delegated stake), an attacker can grow this set arbitrarily, making every subsequent unauthenticated `getVoteAccounts` call progressively more expensive to service — the same "unbounded iteration over a growable, attacker-influenced collection" bug class as the referenced `NFTXEligiblityManager.distribute` finding over `_feeReceivers`.

### Finding Description
`get_vote_accounts` fetches the full `VoteAccountsHashMap` via `bank.vote_accounts()` and then does an unfiltered `.iter().filter_map(...)` scan over it, computing an `RpcVoteAccountInfo` (including cloning epoch-credits history) for every entry before partitioning into current/delinquent lists: [1](#0-0) 

Unlike `get_multiple_accounts`, which explicitly bounds the number of pubkeys processed per request via `MAX_MULTIPLE_ACCOUNTS`/`max_multiple_accounts`: [2](#0-1) 

`get_vote_accounts` has no analogous cap. The size of the set it scans is `bank.vote_accounts()`, which is backed by the `StakesCache` and includes *every* correctly-formed, non-zero-lamport vote-program account — not only those with delegated stake: [3](#0-2) [4](#0-3) 

Note that `check_and_store` inserts into `vote_accounts` any account owned by the vote program that is correctly sized and initialized, regardless of stake: [5](#0-4) 

Vote account creation (funding a system account to rent-exempt minimum, then `VoteInstruction::InitializeAccount`) requires no stake delegation and no special privilege — any wallet can submit these transactions. The only mitigation that limits an *unbounded* number of vote accounts, `VoteAccounts::clone_and_filter_for_vat` (SIMD-357 truncation), is applied only to the validator-admission-table (VAT) path used for consensus/gossip, not to the RPC `vote_accounts()` accessor used by `get_vote_accounts`: [6](#0-5) 

Consequently, a single low-rate `getVoteAccounts` call performs O(n) work (allocation, vote-state deserialization/view construction, epoch-credits copy) where `n` is the total number of vote accounts ever created and still funded on-chain — a quantity an attacker can inflate essentially without bound at modest, refundable cost.

### Impact Explanation
Each unauthenticated `getVoteAccounts` call costs CPU/memory proportional to the total number of vote accounts in existence, not to any request parameter. As the attacker-created vote-account count grows, the per-call cost of this single JSON-RPC method (served by any public RPC-enabled agave node) grows correspondingly, degrading that node's RPC responsiveness for legitimate callers with only occasional (low-rate) requests — matching the "unbounded cost for a single low-rate call" acceptance criterion.

### Likelihood Explanation
Likelihood is moderate-to-high: creating a vote account requires only rent-exempt funding of the account (fully recoverable by closing it) and standard, permissionless vote-program instructions; no stake, no validator identity, and no special authorization are needed. An attacker willing to spend a modest, largely-refundable amount of SOL and a handful of transactions can create thousands of vote accounts, after which every future `getVoteAccounts` call against that node/cluster state pays the increased scan cost.

### Recommendation
Bound the work performed per `getVoteAccounts` call independent of on-chain vote-account population size, e.g.:
- Enforce a maximum number of vote accounts returned/scanned per call (similar to `MAX_MULTIPLE_ACCOUNTS` for `get_multiple_accounts`), with pagination support.
- When no `votePubkey` filter is supplied, consider deriving the response from stake-weighted/epoch vote-account sets (which are already bounded via `clone_and_filter_for_vat`) rather than the unbounded raw `StakesCache` vote-accounts map.
- Alternatively, filter out zero-stake vote accounts before building the expensive `RpcVoteAccountInfo` records, since these are of little value to `getVoteAccounts` callers and are the primary vector for cheap inflation.

### Proof of Concept
1. Repeatedly submit `SystemInstruction::CreateAccount` (funded to rent-exempt minimum for `VoteStateV4`) + `VoteInstruction::InitializeAccount` transactions to create a large number (e.g., tens of thousands) of zero-stake vote accounts.
2. Observe that `bank.vote_accounts()` (`runtime/src/bank.rs:5796`) now returns a `VoteAccountsHashMap` whose size scales with the attacker's created accounts.
3. Call the `getVoteAccounts` JSON-RPC method against the target node; measure that response latency/CPU cost scales with the number of vote accounts created in step 1, even though the request itself carries no parameters proportional to that cost.
4. Repeat account creation to keep growing the set, showing the per-call cost of this single unauthenticated RPC method is effectively unbounded and attacker-controlled.

### Citations

**File:** rpc/src/rpc.rs (L1167-1201)
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

**File:** runtime/src/bank.rs (L5794-5799)
```rust
    /// current vote accounts for this bank along with the stake
    ///   attributed to each account
    pub fn vote_accounts(&self) -> Arc<VoteAccountsHashMap> {
        let stakes = self.stakes_cache.stakes();
        Arc::from(stakes.vote_accounts())
    }
```

**File:** runtime/src/stakes.rs (L98-135)
```rust
        let owner = account.owner();
        // Zero lamport accounts are not stored in accounts-db
        // and so should be removed from cache as well.
        if account.lamports() == 0 {
            if solana_vote_program::check_id(owner) {
                let _old_vote_account = {
                    let mut stakes = self.0.write().unwrap();
                    stakes.remove_vote_account(pubkey)
                };
            } else if stake_program::check_id(owner) {
                let mut stakes = self.0.write().unwrap();
                stakes.remove_stake_delegation(
                    pubkey,
                    new_rate_activation_epoch,
                    use_fixed_point_stake_math,
                );
            }
            return;
        }
        debug_assert_ne!(account.lamports(), 0u64);
        if solana_vote_program::check_id(owner) {
            if VoteStateVersions::is_correct_size_and_initialized(account.data()) {
                match VoteAccount::try_from(create_account_shared_data(account)) {
                    Ok(vote_account) => {
                        // drop the old account after releasing the lock
                        let _old_vote_account = {
                            let mut stakes = self.0.write().unwrap();
                            stakes.upsert_vote_account(pubkey, vote_account)
                        };
                    }
                    Err(_) => {
                        // drop the old account after releasing the lock
                        let _old_vote_account = {
                            let mut stakes = self.0.write().unwrap();
                            stakes.remove_vote_account(pubkey)
                        };
                    }
                }
```

**File:** vote/src/vote_account.rs (L212-244)
```rust
    pub fn clone_and_filter_for_vat(
        &self,
        max_vote_accounts: usize,
        minimum_vote_account_balance: u64,
    ) -> VoteAccounts {
        assert!(max_vote_accounts > 0, "max_vote_accounts must be > 0");
        let capacity = max_vote_accounts.min(self.vote_accounts.len());
        let mut entries_to_sort: Vec<(&Pubkey, &VoteAccount, u64)> = Vec::with_capacity(capacity);
        for (pubkey, (stake, vote_account)) in self.vote_accounts.iter() {
            let has_bls = vote_account
                .vote_state_view()
                .bls_pubkey_compressed()
                .is_some();
            let has_stake = *stake != 0u64;
            let has_balance = vote_account.lamports() >= minimum_vote_account_balance;

            if !has_bls || !has_stake || !has_balance {
                continue;
            }
            entries_to_sort.push((pubkey, vote_account, *stake));
        }

        let valid_len = entries_to_sort.len();
        if entries_to_sort.len() > max_vote_accounts {
            // Find the cutoff stake using partial sort (more efficient than full sort).
            let (_, cutoff_entry, _) =
                entries_to_sort.select_nth_unstable_by(max_vote_accounts, |a, b| b.2.cmp(&a.2));
            let floor_stake = cutoff_entry.2;

            // Per SIMD 357, we remove all vote accounts with stake smaller or equal to
            // the first truncated one.
            entries_to_sort.retain(|(_, _, stake)| *stake > floor_stake);
        }
```
