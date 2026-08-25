### Title
Stale stake/vote delegation entries in `StakesCache` are not evicted when an account's owner changes away from the stake/vote program - (File: `runtime/src/stakes.rs`)

### Summary
`StakesCache::check_and_store` in [1](#0-0)  updates the bank-level cache of vote/stake accounts after every transaction, but it only handles the case where the *current* owner of a touched account is the vote program or the stake program. If an account that was previously cached as a stake delegation or vote account has its owner reassigned to some other program (neither vote nor stake), neither branch fires and the previously cached entry is never evicted, leaving `StakesCache` holding a stale delegation/vote-account mapping that no longer corresponds to the account's actual state - the same "stale mapping after underlying config/ownership change" bug class described in the Connext `setWrapper`/`canonicalToAdopted` report.

### Finding Description
`Bank::update_stakes_cache` calls `self.stakes_cache.check_and_store(pubkey, account, ...)` for every account touched by successfully-processed transactions, once per slot, via [2](#0-1)  which is invoked from transaction commit processing [3](#0-2) .

`check_and_store` itself only reacts based on the account's *new* owner: [4](#0-3) 
```
// TODO: If the account is already cached as a vote or stake account
// but the owner changes, then this needs to evict the account from
// the cache. see:
// https://github.com/solana-labs/solana/pull/24200#discussion_r849935444
let owner = account.owner();
...
if solana_vote_program::check_id(owner) { ... }
else if stake_program::check_id(owner) { ... }
```
This explicit TODO documents the exact defect: when `pubkey` was previously cached (as a `VoteAccount` in `stakes.vote_accounts` or as a stake delegation in `stakes.stake_delegations`) but its owner is subsequently changed to a third program (i.e., neither `solana_vote_program::id()` nor `stake_program::id()`), *neither* `if` branch executes, so the function returns without calling `remove_vote_account`/`remove_stake_delegation`. The old cached `VoteAccount`/`StakeAccount` entry (and its contribution to `delegated_stakes`/`vote_accounts` totals) remains in the in-memory `Stakes<StakeAccount>` structure even though accounts-db now shows a different owner for that pubkey.

This is directly analogous to the Connext bug: `setWrapper` changed the wrapper address but `canonicalToAdopted[_canonical.id]` kept pointing at the old (now-invalid) wrapper because no code path re-synchronized the secondary mapping. Here, `StakesCache` is a secondary/derived mapping over accounts-db state, and there is no code path that re-synchronizes it when the primary state (account owner) changes to a value outside the two owners the function explicitly checks for.

### Impact Explanation
`StakesCache` underlies vote-account delegated-stake totals used for consensus-relevant bookkeeping in `Bank` (e.g., `get_current_epoch_total_stake`, epoch stake snapshotting in `update_epoch_stakes`, and reward/vote weighting paths that read `stakes_cache.stakes().vote_accounts()`). If a stake account's owner is changed away from `stake_program`/`vote_program` (which the runtime permits for zero-data accounts being fully reassigned) while the cache still records it as a live delegation, the delegated stake attributed to the associated vote account is not reduced, causing an inconsistency between actual on-chain account state and the bank's in-memory stake-weighting cache. This can misrepresent effective stake for a validator/vote account within the current bank's cache, affecting anything that trusts `stakes_cache` before the value is naturally superseded by a full delegation update elsewhere.

### Likelihood Explanation
This code path is exercised on every ordinary transaction that touches any account (any transaction whose accounts include a formerly-cached stake/vote pubkey), via the normal transaction execution/commit flow — no privileged access is required. The trigger condition is simply "an account previously owned by the stake or vote program has its owner changed to a different program in an executed transaction," which the runtime's account-reassignment rules for zero-data accounts already permit for arbitrary user-submitted transactions.

### Recommendation
In `StakesCache::check_and_store`, before branching on the new owner, check whether `pubkey` is already present in `self.0`'s `vote_accounts` or `stake_delegations` maps; if so and the new owner no longer matches the corresponding program, explicitly call `remove_vote_account`/`remove_stake_delegation` (as already done for the zero-lamports case) so the cache is unconditionally kept in sync with the account's actual owner, closing the gap noted in the existing TODO.

### Proof of Concept
1. Deploy a delegated stake account `S` for vote account `V`; `check_and_store` inserts a `stake_delegations` entry for `S` contributing stake to `V`'s `delegated_stakes` (see [5](#0-4) ).
2. Execute a transaction that reassigns `S`'s owner to a third program (not `stake_program`/`vote_program`) while zeroing its data, which the runtime permits for an owning program relinquishing ownership.
3. `Bank::update_stakes_cache` invokes `check_and_store(S, account, ...)` with `account.owner()` equal to the new (third) program; both the `solana_vote_program::check_id` and `stake_program::check_id` branches are skipped, so `remove_stake_delegation` is never called.
4. `stakes_cache.stakes().vote_accounts().get_delegated_stake(&V)` still reflects `S`'s stake even though `S` is no longer a stake-program-owned account, demonstrating the stale, unsynchronized cache entry — analogous to the Connext report's stale `canonicalToAdopted` mapping after `setWrapper`.

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

**File:** runtime/src/stakes.rs (L620-642)
```rust
    fn upsert_stake_delegation(
        &mut self,
        stake_pubkey: Pubkey,
        stake_account: StakeAccount,
        new_rate_activation_epoch: Option<Epoch>,
        use_fixed_point_stake_math: bool,
    ) {
        debug_assert_ne!(stake_account.lamports(), 0u64);
        let delegation = stake_account.delegation();
        let voter_pubkey = delegation.voter_pubkey;
        let stake = delegation_effective_stake(
            delegation,
            self.epoch,
            &self.stake_history,
            new_rate_activation_epoch,
            use_fixed_point_stake_math,
        );
        match self.stake_delegations.insert(stake_pubkey, stake_account) {
            None => {
                self.add_delegated_stake(voter_pubkey, stake);
                self.vote_accounts.add_stake(&voter_pubkey, stake);
            }
            Some(old_stake_account) => {
```

**File:** runtime/src/bank.rs (L4389-4392)
```rust
        // Cached vote and stake accounts are synchronized with accounts-db
        // after each transaction.
        let ((), update_stakes_cache_us) =
            measure_us!(self.update_stakes_cache(sanitized_txs, &processing_results));
```

**File:** runtime/src/bank.rs (L5755-5791)
```rust
    /// a bank-level cache of vote accounts and stake delegation info
    fn update_stakes_cache(
        &self,
        txs: &[impl SVMMessage],
        processing_results: &[TransactionProcessingResult],
    ) {
        debug_assert_eq!(txs.len(), processing_results.len());
        let new_warmup_cooldown_rate_epoch = self.new_warmup_cooldown_rate_epoch();
        let use_fixed_point_stake_math = self.use_fixed_point_stake_math();
        txs.iter()
            .zip(processing_results)
            .filter_map(|(tx, processing_result)| {
                processing_result
                    .processed_transaction()
                    .map(|processed_tx| (tx, processed_tx))
            })
            .filter_map(|(tx, processed_tx)| {
                processed_tx
                    .executed_transaction()
                    .map(|executed_tx| (tx, executed_tx))
            })
            .filter(|(_, executed_tx)| executed_tx.was_successful())
            .flat_map(|(tx, executed_tx)| {
                let num_account_keys = tx.account_keys().len();
                let loaded_tx = &executed_tx.loaded_transaction;
                loaded_tx.accounts.iter().take(num_account_keys)
            })
            .for_each(|(pubkey, account)| {
                // note that this could get timed to: self.rc.accounts.accounts_db.stats.stakes_cache_check_and_store_us,
                //  but this code path is captured separately in ExecuteTimingType::UpdateStakesCacheUs
                self.stakes_cache.check_and_store(
                    pubkey,
                    account,
                    new_warmup_cooldown_rate_epoch,
                    use_fixed_point_stake_math,
                );
            });
```
