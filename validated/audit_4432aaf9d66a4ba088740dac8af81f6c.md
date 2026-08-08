### Title
`StakesCache::check_and_store` fails to evict vote/stake accounts on owner reassignment, leaving stale consensus/RPC state - (File: runtime/src/stakes.rs)

### Summary
### Finding Description
`StakesCache::check_and_store` is the single choke-point that keeps the `Stakes<StakeAccount>` cache (vote accounts and stake delegations) in sync with the bank's actual account state. It is invoked on every account write during transaction processing to decide whether to upsert or remove an entry from the cache. The function branches purely on the account's *current* owner: if `solana_vote_program::check_id(owner)` it updates the vote-account cache, if `stake_program::check_id(owner)` it updates the stake-delegation cache, and it only removes an entry from either cache when the account's lamports drop to zero.

The code contains an explicit, unresolved TODO acknowledging the gap: "If the account is already cached as a vote or stake account but the owner changes, then this needs to evict the account from the cache" [1](#0-0) . This means that if an account that is currently tracked in the cache as a vote account or stake account has its owner reassigned to some other program (while keeping nonzero lamports, e.g. via `SystemInstruction::Assign`/`Allocate` handled in `programs/system/src/system_processor.rs`'s `assign()` [2](#0-1) ), `check_and_store` takes neither branch, since the new owner is not the vote or stake program. As a result, no removal path fires and the stale entry (based on the account's prior data) is left in the `StakesCache`.

This differs qualitatively from other subsystems that were shown to correctly handle owner reassignment, such as accounts-db's secondary indexes which are keyed and reclaimed based on owner transitions [3](#0-2) , and the CPI callee-account update path which explicitly reassigns/tracks owner changes at each cross-program call boundary [4](#0-3) . `StakesCache`, by contrast, has no analogous "owner changed away from vote/stake program" branch.

### Impact Explanation
`Stakes<StakeAccount>` backs consensus-relevant state (validator vote/stake weighting) as well as RPC surfaces that read cached vote/stake info (e.g., `getVoteAccounts`/`getStakeActivation`-style queries derived from bank stakes). A stale vote or stake account persisting in the cache after its owner has been reassigned away from the vote/stake program means the bank could continue to report/consider an account's old vote or delegation data as live, even though the account is no longer owned by (and thus no longer controlled by) the vote/stake program. This is a wrong-state-returned/consensus-relevant-mutation class issue in the family the external report describes: a secondary registry ("the description... is not correct... tracks last deployments and does not guarantee ownership") that silently diverges from the authoritative owner field.

### Likelihood Explanation
This can be triggered by an ordinary, unprivileged transaction: any account holder can invoke the System Program's `Assign` instruction on their own vote/stake account (subject only to the account being a signer, per `assign()`), reassigning ownership away from the vote or stake program while leaving lamports > 0. That single transaction is enough to leave the cache desynchronized — no validator/operator privilege is needed, only a signature over the affected account. The developers' own TODO comment confirms this is a known, unaddressed gap rather than a hypothetical.

### Recommendation
In `StakesCache::check_and_store`, when the account's lamports are nonzero, compare the *previous* cached owner (or unconditionally attempt removal keyed on the account's own pubkey) against `solana_vote_program`/`stake_program` and evict the stale entry whenever the current owner no longer matches, mirroring the zero-lamport removal branches already present in the same function [5](#0-4) .

### Proof of Concept
1. Establish an account owned by `solana_vote_program` (or `stake_program`) with a valid, correctly-sized vote/stake state, so it is upserted into `StakesCache` via the `Ok(vote_account)`/`Ok(stake_account)` branches [6](#0-5) .
2. From that account (as its own signer), submit a System Program `Assign` instruction reassigning its owner to an arbitrary unrelated program ID, keeping lamports nonzero — permitted by `assign()`'s signer-only check [2](#0-1) .
3. `check_and_store` is invoked with the account's new owner; since it's neither the vote nor stake program and lamports != 0, neither `if`/`else if` branch executes, so the previously cached vote/stake entry for that pubkey is never removed.
4. Query the bank's stakes cache (e.g., through the code path backing `getVoteAccounts`/stake activation RPC methods) and observe the account is still reported as an active vote/stake account despite its owner no longer being the vote/stake program.

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

**File:** programs/system/src/system_processor.rs (L117-135)
```rust
fn assign(
    account: &mut BorrowedInstructionAccount,
    address: &Address,
    owner: &Pubkey,
    signers: &HashSet<Pubkey>,
    invoke_context: &InvokeContext,
) -> Result<(), InstructionError> {
    // no work to do, just return
    if account.get_owner() == owner {
        return Ok(());
    }

    if !address.is_signer(signers) {
        ic_msg!(invoke_context, "Assign: account {:?} must sign", address);
        return Err(InstructionError::MissingRequiredSignature);
    }

    account.set_owner(&owner.to_bytes())
}
```

**File:** accounts-db/src/accounts_index.rs (L614-631)
```rust
    pub(crate) fn update_secondary_indexes(
        &self,
        pubkey: &Pubkey,
        account: &impl ReadableAccount,
        account_indexes: &AccountSecondaryIndexes,
    ) {
        if account_indexes.is_empty() {
            return;
        }

        let account_owner = account.owner();
        let account_data = account.data();

        if account_indexes.contains(&AccountIndex::ProgramId)
            && account_indexes.include_key(account_owner)
        {
            self.program_id_index.insert(account_owner, pubkey);
        }
```

**File:** program-runtime/src/cpi.rs (L1165-1170)
```rust
    // Change the owner at the end so that we are allowed to change the lamports and data before
    if callee_account.get_owner() != caller_account.owner {
        callee_account.set_owner(caller_account.owner.as_ref())?;
        // caller gave ownership and thus write access away, so caller must be updated
        must_update_caller = true;
    }
```
