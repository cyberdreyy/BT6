### Title
Stale vote/stake accounts remain in `StakesCache` after owner reassignment, corrupting delegated-stake and vote-account state used for consensus/RPC - (File: runtime/src/stakes.rs)

### Summary
`StakesCache::check_and_store` in `runtime/src/stakes.rs` is the single choke point that is supposed to keep the bank-level cache of vote/stake accounts synchronized with accounts-db after every transaction, analogous to `_updateTokenInRegistry` in the referenced report. It only updates or evicts a cache entry when the *current* account owner matches the vote or stake program. If a previously cached vote or stake account's owner is changed to an arbitrary program (e.g. by a transaction that reassigns/overwrites the account with the system program or any other owner), `check_and_store` takes neither the vote-program nor the stake-program branch, so the stale, no-longer-valid entry is never removed from the cache.

### Finding Description
`check_and_store` is called from `Bank::update_stakes_cache` (`runtime/src/bank.rs:5756-5792`) for every account touched by a successfully executed transaction. Its logic branches strictly on the account's current owner: [1](#0-0) 

The code contains an explicit acknowledgment of this gap: [2](#0-1) 

If `owner` is neither the vote program nor the stake program (e.g. an account previously owned by the vote/stake program is reassigned to `system_program` or any other program id, or the data is otherwise made to look like a foreign account), none of the `if`/`else if` branches execute, and the function silently returns without touching `stakes.vote_accounts` / `stakes.stake_delegations`. The old entry — including its cached `activated_stake`, `node_pubkey`, and `staked_nodes` bookkeeping — remains in the `StakesCache` even though the true account in accounts-db is no longer a vote/stake account.

This is functionally identical to the reported "missing call to update registry" bug class: a per-account update function is only invoked on a subset of the state transitions that should trigger it, so the higher-level (registry/cache) structure can permanently diverge from the true account state.

A repository-level test explicitly reproduces and marks this as unresolved: [3](#0-2) [4](#0-3) 

The test shows that after `bank.store_account` changes a staked vote account's owner to a bogus program, `bank.vote_accounts().len()` still reports the stale entry (`assert_eq!(bank.vote_accounts().len(), 1)`), and the `check_owner_change` parameter is hard-coded to `false` in `test_stake_vote_account_validity`, i.e. the assertion that would catch owner-change corruption is deliberately disabled.

A `evict_invalid_stakes_cache_entries` feature-gate id exists in `feature-set/src/lib.rs` describing "evict invalid stakes cache entries on epoch boundaries", but no code in the indexed codebase actually checks or acts on this feature id for this scenario — it only appears in the feature list/description, meaning there is no runtime mitigation wired to this specific owner-reassignment case within the current epoch.

### Impact Explanation
`StakesCache` (`stakes_cache`) backs `Bank::vote_accounts()`, delegated-stake totals, and ultimately epoch stakes / leader schedule computation and RPC responses (e.g. `getVoteAccounts`). If an ordinary user transaction reassigns the owner of what was a cached vote or stake account (this is entirely achievable via a CPI/transaction the account's current owner program permits, or simply by writing to an account no longer subject to vote/stake-program ownership invariants), the validator's in-memory view of active stake/vote weight becomes stale:
- Delegated stake and vote-account presence reported by `bank.vote_accounts()` can remain non-zero/present for an account that is provably no longer a valid vote/stake account, which feeds directly into stake-weighted calculations (`calculate_activated_stake`, reward distribution, and epoch-stakes snapshots for leader-schedule/vote-account computations).
- This can produce consensus-relevant state divergence between what accounts-db actually holds and what the bank's stakes cache reports, and can also return incorrect data via `getVoteAccounts` RPC.

### Likelihood Explanation
Likelihood is constrained by how attacker-reachable "owner reassignment of a stake/vote account" is in practice — the stake and vote programs guard account ownership under normal instruction paths, so a legitimate stake/vote account's owner field is not trivially rewritable by an arbitrary user without a bug elsewhere (e.g., a lamports-draining/close-and-reopen or account-reassignment primitive). However, the codebase's own test explicitly demonstrates and accepts (via `check_owner_change: false`) that the current implementation is known-vulnerable to this class of state corruption whenever an owner change to a cached pubkey occurs, and there is no enforced runtime safeguard closing this gap for the current epoch.

### Recommendation
In `StakesCache::check_and_store`, before branching on the *current* owner, check whether `pubkey` is already present in `stakes.vote_accounts()` or `stakes.stake_delegations()` and the current owner no longer matches the expected program; if so, unconditionally evict the stale entry (call `remove_vote_account` / `remove_stake_delegation`) regardless of what the new owner is. This closes the gap noted in the existing TODO and should be exercised by re-enabling `check_owner_change: true` in `test_stake_vote_account_validity`.

### Proof of Concept
1. Start a bank with a validator that has both a staked vote account and a stake account cached in `StakesCache` (as in `check_stake_vote_account_validity`, `runtime/src/bank/tests.rs:8521-8589`).
2. Submit a state mutation (via `bank.store_account`, or equivalently a transaction whose executed instructions rewrite the account) that changes the vote account's `owner` field to an arbitrary pubkey while keeping lamports non-zero.
3. Call `Bank::update_stakes_cache` (this happens automatically after every transaction batch) — `StakesCache::check_and_store` is invoked with the new owner, hits neither the vote-program nor stake-program branch, and returns without evicting the entry.
4. Observe that `bank.vote_accounts()` still contains the account and reports its previous delegated stake (`assert_eq!(bank.vote_accounts().len(), 1)` in the existing test), even though the account is no longer owned by the vote program — demonstrating the cache is out of sync with accounts-db ground truth.

### Citations

**File:** runtime/src/stakes.rs (L87-116)
```rust
    pub(crate) fn check_and_store(
        &self,
        pubkey: &Pubkey,
        account: &impl ReadableAccount,
        new_rate_activation_epoch: Option<Epoch>,
        use_fixed_point_stake_math: bool,
    ) {
        // TODO: If the account is already cached as a vote or stake account
        // but the owner changes, then this needs to evict the account from
        // the cache. see:
        // https://github.com/solana-labs/solana/pull/24200#discussion_r849935444
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
```

**File:** runtime/src/bank/tests.rs (L8478-8488)
```rust
#[test]
fn test_stake_vote_account_validity() {
    let thread_pool = ThreadPoolBuilder::new().num_threads(1).build().unwrap();
    // TODO: stakes cache should be hardened for the case when the account
    // owner is changed from vote/stake program to something else. see:
    // https://github.com/solana-labs/solana/pull/24200#discussion_r849935444
    check_stake_vote_account_validity(
        false, // check owner change
        |bank: &Bank| bank._load_vote_and_stake_accounts(&thread_pool, null_tracer()),
    );
}
```

**File:** runtime/src/bank/tests.rs (L8557-8589)
```rust
    );

    // Modify staked vote account owner; a vote account owned by another program could be
    // freely modified with malicious data
    let bogus_vote_program = Pubkey::new_unique();
    vote_account.set_lamports(original_lamports);
    vote_account.set_owner(bogus_vote_program);
    bank.store_account(
        &validator_vote_keypairs0.vote_keypair.pubkey(),
        &vote_account,
    );

    assert_eq!(bank.vote_accounts().len(), 1);

    // Modify stake account owner; a stake account owned by another program could be freely
    // modified with malicious data
    let bogus_stake_program = Pubkey::new_unique();
    let mut stake_account = bank
        .get_account(&validator_vote_keypairs1.stake_keypair.pubkey())
        .unwrap_or_default();
    stake_account.set_owner(bogus_stake_program);
    bank.store_account(
        &validator_vote_keypairs1.stake_keypair.pubkey(),
        &stake_account,
    );

    // Accounts must be valid stake and vote accounts
    let vote_and_stake_accounts = load_vote_and_stake_accounts(&bank);
    assert_eq!(
        vote_and_stake_accounts.len(),
        usize::from(!check_owner_change)
    );
}
```
