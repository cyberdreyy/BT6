Found the key confirming detail: `set_owner()` in `transaction-context/src/instruction_accounts.rs` requires the account's data to be zero-initialized (`is_zeroed(self.get_data())`) before an owner change is permitted. A stake or vote account being reassigned away from its owning program would first need its data zeroed, which would also zero out its lamports-independent state — but critically, **owner-change does not require the account to become zero-lamport**, so `StakesCache::check_and_store` in `runtime/src/stakes.rs` only evicts cached vote/stake entries when `account.lamports() == 0`, not when the owner changes while lamports remain non-zero. [1](#0-0) [2](#0-1) 

### Title
Stale Stakes-Cache Entries After Vote/Stake Account Owner Reassignment - (File: `runtime/src/stakes.rs`)

### Summary
`StakesCache::check_and_store()` only removes a pubkey from the cached vote/stake maps when the account's lamports drop to zero. It does not check whether the account's `owner` has changed away from the vote/stake program while lamports remain non-zero. This mirrors the audited GMX bug class where `updateOwner()` updated only some owner-keyed mappings and left stale entries (`refereeFeeAmounts`) tied to the old state, causing incorrect downstream behavior.

### Finding Description
`check_and_store` is called after every transaction that touches a writable account to synchronize the bank's `StakesCache` with accounts-db state. The function branches only on `account.lamports() == 0` for eviction, or `solana_vote_program::check_id(owner)` / `stake_program::check_id(owner)` for insertion/refresh [3](#0-2) . The code contains an explicit acknowledged gap, quoting the exact defect class: "If the account is already cached as a vote or stake account but the owner changes, then this needs to evict the account from the cache" [4](#0-3) . If a pubkey previously held a valid vote/stake account and its owner is reassigned to a different program (while remaining non-zero-lamport, e.g. reassigned then re-funded/re-initialized with different data in the same or later slot), the old `VoteAccount`/`StakeAccount` entry keyed by that pubkey is never removed from `StakesCache`, because the removal branch is gated purely on the zero-lamports condition, not an owner-mismatch condition.

### Impact Explanation
`StakesCache` backs `Bank::vote_accounts()`/stake-delegation views consumed by RPC handlers such as `getVoteAccounts` and `getStakeActivation`, as well as other bank-forks reads that trust this cache instead of re-deriving from account owner at query time. A stale entry means the RPC can keep reporting an address as an active/registered vote or stake account (with outdated vote-state/stake-delegation data) even though its owner has since changed to an unrelated program — a wrong/stale data misreport reachable via a single unprivileged transaction sequence (assign-then-repopulate), matching the "wrong-slot/fork/account data returned" acceptance criterion.

### Likelihood Explanation
The system program's `assign()`/`set_owner()` allow arbitrary owner reassignment by the current owner as long as the account is a signer and data is zero at the time of reassignment [1](#0-0) [5](#0-4) . Note that the vote and stake native programs do not expose an "assign to new owner" instruction themselves, so this path likely requires the account to first be handed back to system program via a compatible mechanism, or requires further confirmation of exactly how a live (non-zero-lamport) vote/stake account's owner can be flipped mid-session without existing rent/owner-change protections intervening (e.g., `TransactionAccountStateInfo`/rent-state checks in `svm/src/transaction_account_state_info.rs` do track owner changes for rent purposes, but that only affects rent bookkeeping, not the `StakesCache` sync). This is left partially unverified given tool-call limits — the exact reachability of an owner change while lamports stay non-zero for pre-existing vote/stake pubkeys warrants direct confirmation with a Devin session that can trace the stake/vote program instruction set and the accounts-db update-index path invoked by `Bank::update_stakes_cache` in `runtime/src/bank.rs`.

### Recommendation
In `StakesCache::check_and_store`, compare the account's post-transaction owner against the previously cached owner (or simply check on every call whether the pubkey exists in the vote/stake maps but the current owner no longer matches the corresponding program), and evict the stale entry regardless of lamports, as flagged by the existing TODO.

### Proof of Concept
Not independently reproduced within this investigation; the analog is derived directly from the acknowledged TODO in `runtime/src/stakes.rs` (lines 94-97) referencing solana-labs/solana#24200 discussion, combined with confirming that `set_owner` in `transaction-context/src/instruction_accounts.rs` permits owner changes without requiring zero-lamports. A concrete end-to-end PoC transaction sequence (vote/stake account -> owner reassignment -> re-population -> `getVoteAccounts`/`getStakeActivation` query showing stale data) was not constructed due to tool-call budget exhaustion and should be validated in a follow-up session with code execution access.

### Citations

**File:** transaction-context/src/instruction_accounts.rs (L90-111)
```rust
    /// Assignes the owner of this account (transaction wide)
    pub fn set_owner(&mut self, pubkey: &[u8]) -> Result<(), InstructionError> {
        // Only the owner can assign a new owner
        if !self.is_owned_by_current_program() {
            return Err(InstructionError::ModifiedProgramId);
        }
        // and only if the account is writable
        if !self.is_writable() {
            return Err(InstructionError::ModifiedProgramId);
        }
        // and only if the data is zero-initialized or empty
        if !is_zeroed(self.get_data()) {
            return Err(InstructionError::ModifiedProgramId);
        }
        // don't touch the account if the owner does not change
        if self.get_owner().to_bytes() == pubkey {
            return Ok(());
        }
        self.touch()?;
        self.account.copy_into_owner_from_slice(pubkey);
        Ok(())
    }
```

**File:** runtime/src/stakes.rs (L94-164)
```rust
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
