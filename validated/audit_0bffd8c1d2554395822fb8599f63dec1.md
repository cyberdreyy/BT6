### Title
Stale `StakeAccount` remains cached under old pubkey after owner changes away from `stake_program::id()` with lamports > 0 - ([File: runtime/src/stakes.rs])

### Summary
`StakesCache::check_and_store` only updates or evicts the cached entry when the account's *current* owner is `solana_vote_program::id()` or `stake_program::id()`. If an account previously cached as a stake delegation has its owner changed to an unrelated program while retaining lamports > 0, the function's `else` branch is empty, leaving the previous `StakeAccount` entry in `Stakes<StakeAccount>::stake_delegations` unchanged and stale.

### Finding Description
`check_and_store` (runtime/src/stakes.rs) first checks `account.lamports() == 0` to prune vote/stake cache entries, but that path is only taken for zero-lamport accounts [1](#0-0) . For lamports > 0, the function dispatches purely on `account.owner()`: if it is the vote program it upserts/removes the vote-account cache, and if it is `stake_program::check_id(owner)` it upserts/removes the stake delegation [2](#0-1) . There is no `else` branch for "owner is neither vote nor stake program" — the function simply returns without touching the cache. This is exactly the gap called out by the code's own comment: `// TODO: If the account is already cached as a vote or stake account but the owner changes, then this needs to evict the account from the cache.` [3](#0-2) 

Exploit flow: an account is a valid stake delegation (owner = `stake_program::id()`, lamports > 0); `check_and_store` is invoked (via the bank's account-write path) and populates `stakes.stake_delegations` with a `StakeAccount` for that pubkey. In a later transaction within the same slot (or any subsequent slot), the account's owner is changed to an unrelated program while lamports remain > 0 (e.g., leaving the account non-empty). When `check_and_store` runs again for this write, `owner` is neither the vote program nor `stake_program::id()`, so neither branch executes, and the previously cached `StakeAccount` entry for that pubkey is never removed from `Stakes<StakeAccount>`. Any reader that queries `Stakes<StakeAccount>` (the bank-forks-backed stakes structure used for RPC-facing stake-activation/vote-account queries) will continue to see the pubkey reported as an active stake delegation with its old delegation data, even though the account is no longer owned by the stake program.

### Impact Explanation
The scoped impact is misreporting of validator-internal cache state through RPC surfaces that read `Stakes<StakeAccount>` (e.g., stake-activation-status style queries derived from `bank.stakes_cache`), returning delegation data for an account key whose owner is provably no longer `stake_program::id()`. This is a "wrong data returned" class finding rather than a consensus-safety break, since consensus paths still read canonical account state from accounts-db separately; the staleness is confined to this derived cache used for informational reads.

### Likelihood Explanation
This requires only an unprivileged attacker who controls an account they can (a) get delegated under the stake program with lamports > 0, then (b) subsequently reassign to a different owner program while keeping lamports > 0 — both are on-chain state transitions triggered by ordinary transactions, not privileged operations. Because the bug is a straightforward missing branch (confirmed by the maintainers' own TODO comment referencing a years-old discussion thread), the sequence is deterministic and repeatable whenever the owner-change transaction succeeds.

### Recommendation
In `StakesCache::check_and_store`, before dispatching on the *new* owner, check whether the pubkey is already present in `stakes.vote_accounts` or `stakes.stake_delegations` and the new owner no longer matches the expected program; if so, explicitly call `remove_vote_account`/`remove_stake_delegation` for that pubkey regardless of the new owner, then proceed with the owner-based upsert logic for the new state.

### Proof of Concept
Integration test sketch (runtime/src/stakes.rs test module):
1. Construct a `StakesCache` and an `AccountSharedData` owned by `stake_program::id()` with a valid initialized/delegated `StakeStateV2`, lamports > 0.
2. Call `check_and_store(&pubkey, &account, None, false)` and assert `stakes().stake_delegations().contains_key(&pubkey)` is `true`.
3. Mutate the same `AccountSharedData`'s owner to an arbitrary unrelated `Pubkey::new_unique()`, keep lamports unchanged (> 0).
4. Call `check_and_store(&pubkey, &account, None, false)` again.
5. Assert `stakes().stake_delegations().contains_key(&pubkey)` is `false` — this assertion will currently **fail**, proving the stale-entry bug, since neither the vote nor stake branch executes for the new owner.

### Citations

**File:** runtime/src/stakes.rs (L94-97)
```rust
        // TODO: If the account is already cached as a vote or stake account
        // but the owner changes, then this needs to evict the account from
        // the cache. see:
        // https://github.com/solana-labs/solana/pull/24200#discussion_r849935444
```

**File:** runtime/src/stakes.rs (L98-116)
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
```

**File:** runtime/src/stakes.rs (L118-163)
```rust
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
            } else {
                // drop the old account after releasing the lock
                let _old_vote_account = {
                    let mut stakes = self.0.write().unwrap();
                    stakes.remove_vote_account(pubkey)
                };
            };
        } else if stake_program::check_id(owner) {
            match StakeAccount::try_from(create_account_shared_data(account)) {
                Ok(stake_account) => {
                    let mut stakes = self.0.write().unwrap();
                    stakes.upsert_stake_delegation(
                        *pubkey,
                        stake_account,
                        new_rate_activation_epoch,
                        use_fixed_point_stake_math,
                    );
                }
                Err(_) => {
                    let mut stakes = self.0.write().unwrap();
                    stakes.remove_stake_delegation(
                        pubkey,
                        new_rate_activation_epoch,
                        use_fixed_point_stake_math,
                    );
                }
            }
        }
```
